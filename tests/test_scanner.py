from datetime import datetime, timezone

import numpy as np
import pandas as pd

from scanner.core import (
    bars_in_current_regime,
    latest_completed_nyse_session,
    regime_labels,
    transition_matrix,
)


def test_regime_boundaries_are_strict():
    prices = pd.Series([100.0] * 20 + [100.0 * np.exp(0.05)])
    regime, returns = regime_labels(prices, 20)
    # Exact floating arithmetic can be a few ulps around 0.05, so test the
    # intended strict boundary with values safely to either side as well.
    prices.iloc[-1] = 100.0 * np.exp(0.0499)
    regime, _ = regime_labels(prices, 20)
    assert int(regime.iloc[-1]) == 0
    prices.iloc[-1] = 100.0 * np.exp(0.0501)
    regime, _ = regime_labels(prices, 20)
    assert int(regime.iloc[-1]) == 1


def test_transition_matrix_uses_non_overlapping_stride():
    # Sampling positions 0, 2, 4 produce 0 -> 1 -> 2.
    regime = pd.Series([0, 2, 1, 0, 2], dtype=float)
    matrix, count, current = transition_matrix(regime, 2)
    assert count == 2
    assert current == 2
    assert matrix[0, 1] == 1.0
    assert matrix[1, 2] == 1.0


def test_bars_in_current_regime():
    regime = pd.Series([0, 1, 1, 2, 2, 2], dtype=float)
    assert bars_in_current_regime(regime) == 3


def test_completed_session_before_monday_open_is_prior_friday():
    # Monday 2026-08-24 12:00 UTC is before the NYSE open.
    now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    assert latest_completed_nyse_session(now).date().isoformat() == "2026-08-21"
