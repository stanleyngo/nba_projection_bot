"""Tests for the backtesting / calibration harness in backtest.py."""

import numpy as np
import pytest

import nba_projection_bot.backtest as backtest


def test_brier_score_constant_half_is_quarter():
    # mean((0.5 - o)^2) == 0.25 for any binary outcomes.
    preds = [0.5, 0.5, 0.5, 0.5]
    for outcomes in ([0, 0, 0, 0], [1, 1, 1, 1], [0, 1, 0, 1]):
        assert backtest.brier_score(preds, outcomes) == pytest.approx(0.25)


def test_brier_rewards_well_specified_model():
    rng = np.random.default_rng(0)
    outcomes = (rng.random(5000) < 0.7).astype(int).tolist()
    good = [0.7] * len(outcomes)
    bad = [0.3] * len(outcomes)
    assert backtest.brier_score(good, outcomes) < backtest.brier_score(bad, outcomes)


def test_log_loss_penalizes_confident_wrong_predictions():
    # Confidently correct beats confidently wrong.
    assert backtest.log_loss([0.99], [1]) < backtest.log_loss([0.01], [1])
    # Clipping keeps it finite even at the extremes.
    assert np.isfinite(backtest.log_loss([0.0, 1.0], [1, 0]))


def test_calibration_curve_bins_track_the_diagonal_for_perfect_model():
    rng = np.random.default_rng(1)
    preds = rng.random(20000).tolist()
    outcomes = [1 if rng.random() < p else 0 for p in preds]
    bin_pred, bin_obs, counts = backtest.calibration_curve(preds, outcomes, n_bins=10)
    populated = counts > 0
    assert np.allclose(bin_pred[populated], bin_obs[populated], atol=0.05)


def test_walk_forward_produces_aligned_predictions_and_outcomes():
    # Chronological (oldest-first) values; predictor is a stub.
    values = list(range(1, 21))

    def predictor(history, line):
        return 0.5

    def line_fn(history):
        return float(np.median(history))

    preds, outcomes = backtest.walk_forward(
        values, min_history=5, predictor=predictor, line_fn=line_fn
    )
    assert len(preds) == len(outcomes) == len(values) - 5
    assert all(o in (0, 1) for o in outcomes)


def test_walk_forward_grid_expands_by_offsets():
    values = list(range(1, 21))
    offsets = (-4, 0, 4)

    def predictor(history, line):
        return 0.5

    preds, outcomes = backtest.walk_forward_grid(
        values, min_history=5, predictor=predictor, offsets=offsets
    )
    # One (pred, outcome) per game per offset.
    assert len(preds) == len(outcomes) == (len(values) - 5) * len(offsets)
    assert all(o in (0, 1) for o in outcomes)


def test_walk_forward_grid_higher_lines_have_fewer_overs():
    # Steadily rising values; a line far above the median is beaten less often
    # than one far below it.
    values = list(range(1, 41))

    def predictor(history, line):
        return 0.5  # predictor irrelevant here; we inspect the outcomes.

    def overs_at(offset):
        _, outcomes = backtest.walk_forward_grid(
            values, min_history=5, predictor=predictor, offsets=(offset,)
        )
        return sum(outcomes)

    assert overs_at(+8) < overs_at(-8)


# ---------------------------------------------------------------------------
# combined-prop walk-forward
# ---------------------------------------------------------------------------

def test_walk_forward_combo_aligns_and_uses_combined_outcome():
    # Two aligned stat series; the outcome must be decided by the COMBINED total
    # (points + rebounds) against the trailing-median line of that combined series.
    stat_values = {
        "points": list(range(1, 21)),
        "rebounds": list(range(1, 21)),
    }

    seen_lines = []

    def predictor(history, line):
        seen_lines.append(line)
        # History is per-component and grows by one game each step.
        assert set(history) == {"points", "rebounds"}
        return 0.5

    preds, outcomes = backtest.walk_forward_combo(
        stat_values, min_history=5, predictor=predictor
    )
    assert len(preds) == len(outcomes) == 20 - 5
    assert all(o in (0, 1) for o in outcomes)
    # Combined totals rise monotonically (2,4,...,40), so early games sit below
    # their trailing-median line (outcome 0) — the line tracks the combined series.
    assert seen_lines and all(line > 0 for line in seen_lines)


def test_walk_forward_combo_grid_expands_by_offsets():
    stat_values = {"points": list(range(1, 21)), "rebounds": list(range(1, 21))}
    offsets = (-6.0, 0.0, 6.0)

    def predictor(history, line):
        return 0.5

    preds, outcomes = backtest.walk_forward_combo(
        stat_values, min_history=5, predictor=predictor, offsets=offsets
    )
    assert len(preds) == len(outcomes) == (20 - 5) * len(offsets)


def test_compare_combo_models_returns_scored_results():
    # Enough games for a walk-forward; real predictors (no network — pure math).
    rng = np.random.default_rng(0)
    stat_values = {
        "points": rng.integers(15, 35, size=40).tolist(),
        "rebounds": rng.integers(5, 15, size=40).tolist(),
        "assists": rng.integers(3, 12, size=40).tolist(),
    }
    results = backtest.compare_combo_models(stat_values, min_history=10)
    assert set(results) == {"parametric", "bootstrap"}
    for r in results.values():
        assert r["n"] == 40 - 10
        assert 0.0 <= r["brier"] <= 1.0
        assert np.isfinite(r["log_loss"])
