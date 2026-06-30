"""Spawn a K8s Job that runs the transactions producer in backfill mode.

We use the official `kubernetes` Python client (not subprocess to kubectl)
so the trigger is testable in unit form, returns structured errors, and
can be re-used by Day-5's Metaflow `retraining_flow` without depending on
the kubectl binary being on PATH inside the Metaflow step container.

The Job manifest template lives at
`deployments/base/services-finance/transactions/backfill-job.yaml`. This
module renders the template, applies it, then waits for completion or
failure.

Idempotency note: each Job carries a deterministic name based on
(start, end, seed). Re-running with the same parameters will collide
with an existing Job until its TTL (1800s) expires — by design, so we
don't accidentally double-spend backfills.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from kubernetes import client, config

from . import get_logger

log = get_logger(__name__)


_TEMPLATE_RELATIVE = Path(
    'deployments/base/services-finance/transactions/backfill-job.yaml'
)


def _resolve_repo_path(rel: Path) -> Path:
    """Resolve a repo-relative path robustly regardless of caller CWD.

    Tries (in order): absolute path; CWD-relative; walking up from this
    module's location until a `deployments/` sibling is found. This means
    the training pipeline runs correctly whether invoked from the repo
    root, from `services/training_flow/`, or from any subdirectory.
    """
    if rel.is_absolute() and rel.exists():
        return rel
    cwd_candidate = Path.cwd() / rel
    if cwd_candidate.exists():
        return cwd_candidate.resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / rel
        if candidate.exists():
            return candidate.resolve()
    raise FileNotFoundError(
        f'Could not resolve {rel}: not absolute, not under CWD={Path.cwd()}, '
        f'and not found walking up from {here}'
    )


def _default_template_path() -> Path:
    """Lazy resolution so module imports work even from test CWDs."""
    return _resolve_repo_path(_TEMPLATE_RELATIVE)


_DEFAULT_TEMPLATE_PATH = _TEMPLATE_RELATIVE  # back-compat for callers passing this constant; actual resolution happens in trigger_backfill


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    start: datetime
    end: datetime
    seed: int
    max_events: int | None  # None = unbounded; the producer runs until end


@dataclass(frozen=True, slots=True)
class BackfillResult:
    job_name: str
    succeeded: bool
    duration_s: float
    pod_phase: str  # 'Succeeded', 'Failed', 'Unknown'


def _load_k8s() -> None:
    """Load in-cluster config when running inside a pod; otherwise local kubeconfig."""
    try:
        config.load_incluster_config()
        log.info('k8s_config_loaded', mode='in_cluster')
    except config.ConfigException:
        config.load_kube_config()
        log.info('k8s_config_loaded', mode='kubeconfig')


def _job_name(req: BackfillRequest) -> str:
    """Deterministic Job name based on the request parameters.

    Hash keeps it under K8s' 63-char limit while remaining recognizable.
    """
    payload = f'{req.start.isoformat()}|{req.end.isoformat()}|{req.seed}'
    digest = hashlib.sha256(payload.encode()).hexdigest()[:10]
    return f'transactions-backfill-{digest}'


def _render_job(
    template_path: Path, req: BackfillRequest, namespace: str, image: str
) -> dict[str, Any]:
    """Load the YAML template and patch the placeholders.

    Returns a kubernetes-API-shaped dict ready to pass to BatchV1Api.create_namespaced_job.
    """
    with template_path.open('r', encoding='utf-8') as f:
        manifest: dict[str, Any] = yaml.safe_load(f)

    manifest['metadata']['name'] = _job_name(req)
    manifest['metadata']['namespace'] = namespace
    container = manifest['spec']['template']['spec']['containers'][0]
    container['image'] = image

    # Patch env entries in-place.
    env_patches: dict[str, str] = {
        'TXN_START': req.start.astimezone(timezone.utc).isoformat(),
        'TXN_END': req.end.astimezone(timezone.utc).isoformat(),
        'TXN_SEED': str(req.seed),
    }
    if req.max_events is not None:
        env_patches['TXN_MAX_EVENTS'] = str(req.max_events)

    patched_env = []
    for env_var in container['env']:
        name = env_var['name']
        if name == 'TXN_MAX_EVENTS' and req.max_events is None:
            # Drop the entry entirely — empty string would fail pydantic int|None coercion
            # in services/transactions/src/transactions/config.py.
            continue
        if name in env_patches:
            env_var['value'] = env_patches[name]
        patched_env.append(env_var)
    container['env'] = patched_env

    return manifest


def trigger_backfill(
    req: BackfillRequest,
    namespace: str = 'real-time-ml',
    image: str = 'localhost:5000/transactions:dev',
    timeout_s: int = 3600,
    poll_interval_s: int = 10,
    template_path: Path | None = None,
) -> BackfillResult:
    """Apply the Job and block until it Completes or Fails.

    Raises kubernetes.client.exceptions.ApiException on K8s API errors.
    Raises TimeoutError if the Job hasn't finished within timeout_s.
    """
    _load_k8s()
    batch = client.BatchV1Api()

    if template_path is None:
        template_path = _default_template_path()
    manifest = _render_job(template_path, req, namespace, image)
    job_name = manifest['metadata']['name']
    start_ts = time.monotonic()

    log.info(
        'backfill_job_creating',
        job_name=job_name,
        start=req.start.isoformat(),
        end=req.end.isoformat(),
        seed=req.seed,
        namespace=namespace,
        image=image,
        timeout_s=timeout_s,
    )

    try:
        batch.create_namespaced_job(namespace=namespace, body=manifest)
        log.info('backfill_job_submitted', job_name=job_name)
    except client.exceptions.ApiException as exc:
        if exc.status == 409:
            log.warning(
                'backfill_job_already_exists',
                job_name=job_name,
                reason='deterministic name collision — reusing existing Job',
            )
        else:
            log.error(
                'backfill_job_create_failed',
                job_name=job_name,
                status=exc.status,
                reason=exc.reason,
            )
            raise

    # Poll until Succeeded, Failed, or timeout.
    poll_count = 0
    while True:
        elapsed = time.monotonic() - start_ts
        if elapsed > timeout_s:
            raise TimeoutError(
                f'backfill Job {job_name} did not finish within {timeout_s}s'
            )

        job = batch.read_namespaced_job_status(name=job_name, namespace=namespace)
        status = job.status
        succeeded = (status.succeeded or 0) > 0
        failed = (status.failed or 0) > 0
        poll_count += 1

        log.info(
            'backfill_job_poll',
            job_name=job_name,
            poll=poll_count,
            elapsed_s=int(elapsed),
            active=status.active,
            succeeded=status.succeeded,
            failed=status.failed,
        )

        if succeeded:
            log.info(
                'backfill_job_succeeded', job_name=job_name, duration_s=int(elapsed)
            )
            return BackfillResult(
                job_name=job_name,
                succeeded=True,
                duration_s=elapsed,
                pod_phase='Succeeded',
            )

        if failed:
            log.error('backfill_job_failed', job_name=job_name, duration_s=int(elapsed))
            return BackfillResult(
                job_name=job_name,
                succeeded=False,
                duration_s=elapsed,
                pod_phase='Failed',
            )

        time.sleep(poll_interval_s)


def request_window_days(
    days: int, seed: int, max_events: int | None = None, end: datetime | None = None
) -> BackfillRequest:
    """Convenience constructor: request a `days`-long window ending at `end` (UTC)."""
    if end is None:
        end = datetime.now(tz=timezone.utc)
    end = end.replace(microsecond=0)
    start = end - _days(days)
    return BackfillRequest(start=start, end=end, seed=seed, max_events=max_events)


def _days(n: int) -> Any:
    """Local import dodge so the test surface doesn't need timedelta at module top."""
    from datetime import timedelta

    return timedelta(days=n)
