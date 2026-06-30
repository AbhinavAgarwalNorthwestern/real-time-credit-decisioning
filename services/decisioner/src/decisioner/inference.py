"""Day-3: ONNX inference + per-segment model registry.

Pulls ONNX artefacts from MLflow's `uplift_per_segment` registered model
(alias `champion`), caches sessions in memory, and refreshes on alias
change. Each session is per-segment so request hot-path loads only the
one needed.

Extensibility:
  - To swap to a different inference runtime (e.g., TensorRT), implement
    `InferenceBackend` with a `predict(features)` method and inject into
    `ModelRegistry`.
  - To add a challenger arm: hold sessions under both `champion` and
    `challenger` aliases; the decisioner's bandit composer reads both.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import mlflow
import numpy as np
import onnxruntime as ort
from loguru import logger
from mlflow.tracking import MlflowClient

REGISTERED_MODEL_NAME = 'uplift_per_segment'


@dataclass(frozen=True, slots=True)
class ArmPrediction:
    """One arm's outputs — three heads."""

    p_accept: float
    delta_spend: float
    p_default: float


@dataclass(frozen=True, slots=True)
class UpliftPrediction:
    """Composed two-arm prediction → ready for bandit."""

    control: ArmPrediction
    treated: ArmPrediction

    def expected_profit_treated(
        self,
        exposure: float,
        nim_annual: float = 0.12,
        lgd: float = 0.80,
        discount_factor: float = 0.95,
        capital_cost_rate: float = 0.0064,
    ) -> float:
        """Mirror label_simulator.expected_profit_if_treated formula."""
        revenue = self.treated.delta_spend * 12 * nim_annual * discount_factor
        loss = self.treated.p_default * exposure * lgd
        capital = exposure * capital_cost_rate
        return self.treated.p_accept * (revenue - loss - capital)


class InferenceBackend(Protocol):
    def predict(self, features: np.ndarray) -> UpliftPrediction: ...


class ONNXSession:
    def __init__(
        self, onnx_path: Path, intra_op_threads: int = 2, inter_op_threads: int = 1
    ) -> None:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = intra_op_threads
        opts.inter_op_num_threads = inter_op_threads
        self._sess = ort.InferenceSession(
            str(onnx_path),
            opts,
            providers=['CPUExecutionProvider'],
        )

    def predict(self, features: np.ndarray) -> UpliftPrediction:
        # features: (n_features,) → broadcast to batch dim 1
        x = features.astype(np.float32).reshape(1, -1)
        outputs = self._sess.run(None, {'features': x})
        # Order matches model.py SegmentTLearner.forward output keys.
        return UpliftPrediction(
            control=ArmPrediction(
                p_accept=float(outputs[0][0]),
                delta_spend=float(outputs[1][0]),
                p_default=float(outputs[2][0]),
            ),
            treated=ArmPrediction(
                p_accept=float(outputs[3][0]),
                delta_spend=float(outputs[4][0]),
                p_default=float(outputs[5][0]),
            ),
        )


class ModelRegistry:
    """Thread-safe cache of per-segment InferenceBackends keyed by alias."""

    def __init__(
        self, tracking_uri: str, intra_op_threads: int = 2, inter_op_threads: int = 1
    ) -> None:
        mlflow.set_tracking_uri(tracking_uri)
        self._client = MlflowClient()
        self._lock = threading.RLock()
        # alias → segment_id → backend
        self._sessions: dict[str, dict[int, InferenceBackend]] = {}
        self._intra = intra_op_threads
        self._inter = inter_op_threads
        self._last_version_per_alias: dict[str, int] = {}
        self._feature_cols: list[str] = []

    @property
    def feature_cols(self) -> list[str]:
        """Feature columns the loaded model expects (order matters)."""
        return self._feature_cols

    def warm_up(
        self, alias: str = 'champion', segments: tuple[int, ...] = (0, 1, 2, 3, 4, 5)
    ) -> None:
        with self._lock:
            mv = self._client.get_model_version_by_alias(REGISTERED_MODEL_NAME, alias)
            tmp_dir = Path('/tmp') / f'mlflow_models_{alias}_{mv.version}'
            tmp_dir.mkdir(parents=True, exist_ok=True)
            # Load feature schema — tells us which columns (and in what order)
            # the model expects. Written by training_flow/mlflow_log.py.
            schema_local = mlflow.artifacts.download_artifacts(
                run_id=mv.run_id,
                artifact_path='manifest/feature_schema.json',
                dst_path=str(tmp_dir),
            )
            with open(schema_local, 'r') as f:
                schema = json.load(f)
            self._feature_cols = schema['feature_cols']
            logger.info(
                'feature_schema_loaded',
                n_features=len(self._feature_cols),
                cols=self._feature_cols,
            )

            # Load ONNX artefacts
            local = mlflow.artifacts.download_artifacts(
                run_id=mv.run_id,
                artifact_path='onnx',
                dst_path=str(tmp_dir),
            )
            backends: dict[int, InferenceBackend] = {}
            for seg in segments:
                onnx_path = Path(local) / f'segment_{seg}.onnx'
                if onnx_path.exists():
                    backends[seg] = ONNXSession(
                        onnx_path,
                        self._intra,
                        self._inter,
                    )
                    logger.info(
                        'model_loaded', alias=alias, segment=seg, version=mv.version
                    )
            self._sessions[alias] = backends
            self._last_version_per_alias[alias] = int(mv.version)

    def has_alias(self, alias: str) -> bool:
        """Check if alias exists in the registry (without loading models)."""
        try:
            self._client.get_model_version_by_alias(REGISTERED_MODEL_NAME, alias)
            return True
        except Exception:
            return False

    def is_loaded(self, alias: str) -> bool:
        """Check if an alias has already been loaded into memory."""
        with self._lock:
            return alias in self._sessions and len(self._sessions[alias]) > 0

    def refresh_if_changed(self, alias: str = 'champion') -> bool:
        with self._lock:
            try:
                mv = self._client.get_model_version_by_alias(
                    REGISTERED_MODEL_NAME, alias
                )
            except Exception:
                return False
            if int(mv.version) != self._last_version_per_alias.get(alias):
                logger.info(
                    'model_version_changed',
                    alias=alias,
                    old=self._last_version_per_alias.get(alias),
                    new=mv.version,
                )
                self.warm_up(alias)
                return True
            return False

    def predict(
        self, segment_id: int, features: np.ndarray, alias: str = 'champion'
    ) -> UpliftPrediction:
        with self._lock:
            backend = self._sessions[alias][segment_id]
        return backend.predict(features)
