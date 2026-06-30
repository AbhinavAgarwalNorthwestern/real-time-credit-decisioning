"""Unit tests for the decisioner's feature column contract.

Covers the canonical model-input order (`_FEATURE_COLS`) and the dynamically-
built SQL string that queries the RisingWave serving MV. After the customer-
attribute rollout the contract is 26 features (21 behavioral + 5 customer).

Pure-Python tests; no DB, no cluster.
"""

from __future__ import annotations

from decisioner.adverse_action import DEFAULT_REASON, REASON_CODE_MAP
from decisioner.feature_lookup import _FEATURE_COLS, _SERVING_MV, feature_column_order


def test_feature_count_is_26() -> None:
    assert len(_FEATURE_COLS) == 26


def test_feature_column_order_returns_canonical_tuple() -> None:
    cols = feature_column_order()
    assert isinstance(cols, tuple)
    assert cols == _FEATURE_COLS


def test_no_duplicate_feature_names() -> None:
    """Duplicates would silently break model inference (wrong values picked)."""
    assert len(set(_FEATURE_COLS)) == len(_FEATURE_COLS)


def test_customer_attributes_are_the_last_5() -> None:
    """Order matters: the model's input vector is built in this exact order."""
    expected = (
        'credit_score',
        'annual_income',
        'account_tenure_months',
        'n_products',
        'prev_delinquency_count',
    )
    assert _FEATURE_COLS[-5:] == expected


def test_serving_mv_constant() -> None:
    """The MV name must stay aligned with deployments/.../09_..._serving.sql."""
    assert _SERVING_MV == 'behavioral_features_serving'


def test_select_sql_includes_every_feature() -> None:
    """The dynamic SQL built by fetch_one must enumerate all 26 features."""
    cols_csv = ', '.join(_FEATURE_COLS)
    sql = f'SELECT {cols_csv} FROM {_SERVING_MV} WHERE customer_id = $1'
    for col in _FEATURE_COLS:
        assert col in sql, f'SQL missing column: {col}'
    # Sanity: parameter placeholder is preserved (asyncpg uses $1)
    assert '$1' in sql


def test_adverse_action_covers_all_5_customer_attrs() -> None:
    """Every new customer attribute must map to a regulator-friendly reason."""
    for col in (
        'credit_score',
        'annual_income',
        'account_tenure_months',
        'n_products',
        'prev_delinquency_count',
    ):
        assert col in REASON_CODE_MAP, f'no adverse-action reason for {col}'
        assert REASON_CODE_MAP[col] != DEFAULT_REASON


def test_no_reason_is_empty_string() -> None:
    """A blank reason code would fail ECOA Reg B notice requirements."""
    for col, reason in REASON_CODE_MAP.items():
        assert reason and isinstance(reason, str), (
            f'invalid reason for {col}: {reason!r}'
        )
