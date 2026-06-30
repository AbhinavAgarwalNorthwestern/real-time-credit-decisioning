"""Load each synthetic customer's embedded ground-truth response parameters.

At training time we need every customer's `true_p_accept_cli`,
`true_delta_spend_monthly`, `true_p_default`, and `credit_limit` so the
label simulator can produce the (T, Y) tuple per row. These are NOT in
the event stream — they live in the generator's customer dataclass.

The cleanest approach for Day 2 is to regenerate the same cohort with
the same seed and read the params directly from the in-memory cohort.
The generator is deterministic for a given seed, so any session can
recover the same cohort.

For Day 5 retraining (when the cohort would be real customer data), this
module would read from a customer feature/profile store instead. The
public API stays the same — `load_cohort(seed) -> dict[customer_id,
CustomerParams]` — so callers don't change.
"""

from __future__ import annotations

from . import get_logger
from .label_simulator import CustomerParams

log = get_logger(__name__)


def load_cohort_from_generator(
    cohort_size: int, seed: int
) -> dict[str, CustomerParams]:
    """Re-instantiate the synthetic cohort with the producer's seed.

    Imports from services.transactions — relies on the uv workspace
    putting `transactions` on the python path. Day-5 swaps this for a
    real profile store.
    """
    from transactions.customer import generate_cohort  # type: ignore[import-not-found]

    cohort = generate_cohort(size=cohort_size, seed=seed)
    out: dict[str, CustomerParams] = {}
    for c in cohort:
        out[c.customer_id] = CustomerParams(
            customer_id=c.customer_id,
            segment_id=c.segment_id,
            true_p_accept_cli=c.true_p_accept_cli,
            true_delta_spend_monthly=c.true_delta_spend_if_accept,
            true_p_default=c.true_p_default,
            credit_limit=c.credit_limit,
        )
    log.info('cohort_loaded', cohort_size=len(out), seed=seed)
    return out
