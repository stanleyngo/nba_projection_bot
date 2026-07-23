"""Tests for the parametric projection engine in simulation.py."""

import numpy as np
import pytest

import nba_projection_bot.simulation as simulation


# ---------------------------------------------------------------------------
# fit_count_model
# ---------------------------------------------------------------------------

def test_fit_picks_negative_binomial_on_overdispersed_data():
    # Variance clearly greater than the mean -> Negative Binomial.
    rng = np.random.default_rng(0)
    values = rng.negative_binomial(n=5, p=0.25, size=2000).tolist()
    dist, model = simulation.fit_count_model(values)
    assert model == "negative_binomial"
    # Method-of-moments recovers the sample mean.
    assert dist.mean() == pytest.approx(np.mean(values), rel=0.05)


def test_fit_falls_back_to_poisson_when_not_overdispersed():
    # Binomial data is under-dispersed (variance < mean) -> Poisson fallback.
    rng = np.random.default_rng(1)
    values = rng.binomial(n=20, p=0.4, size=2000).tolist()  # mean 8, var 4.8
    dist, model = simulation.fit_count_model(values)
    assert model == "poisson"
    assert dist.mean() == pytest.approx(np.mean(values), rel=0.05)


def test_fit_handles_constant_history():
    # All identical values -> variance 0 -> Poisson fallback, no crash.
    dist, model = simulation.fit_count_model([20] * 10)
    assert model == "poisson"
    assert dist.mean() == pytest.approx(20.0, rel=1e-6)


# ---------------------------------------------------------------------------
# project_stat
# ---------------------------------------------------------------------------

def test_project_stat_probs_are_valid_and_sum_to_one():
    values = [22, 18, 25, 30, 19, 21, 27, 24, 20, 23]
    out = simulation.project_stat(values, line=22.5)
    for key in ("prob_over", "prob_under", "prob_push"):
        assert 0.0 <= out[key] <= 1.0
    assert out["prob_over"] + out["prob_under"] + out["prob_push"] == pytest.approx(1.0)
    assert out["model"] in ("negative_binomial", "poisson")


def test_tail_beyond_observed_max_is_nonzero():
    # THE core fix: the old bootstrap gave a hard P=0 above the observed max.
    values = [22, 18, 25, 30, 19, 21, 27, 24, 20, 23]  # max observed = 30
    out = simulation.project_stat(values, line=40)
    assert out["prob_over"] > 0.0


def test_integer_line_has_nonzero_push_mass():
    values = [22, 18, 25, 30, 19, 21, 27, 24, 20, 23]
    out = simulation.project_stat(values, line=22)  # integer line -> push possible
    assert out["prob_push"] > 0.0
    assert out["prob_over"] + out["prob_under"] + out["prob_push"] == pytest.approx(1.0)


def test_prob_over_is_monotonic_decreasing_in_line():
    values = [22, 18, 25, 30, 19, 21, 27, 24, 20, 23]
    probs = [simulation.project_stat(values, line=L)["prob_over"]
             for L in (10.5, 20.5, 25.5, 35.5)]
    assert probs == sorted(probs, reverse=True)


def test_recency_weighting_shifts_mean_toward_recent_games():
    # data.py returns most-recent-first, so index 0 is the latest game.
    # Recent games high, older games low.
    values = [40, 38, 36, 10, 8, 6, 9, 7, 11, 5]
    plain = simulation.project_stat(values, line=20.5)["mean"]
    weighted = simulation.project_stat(values, line=20.5, half_life=2)["mean"]
    assert weighted > plain


# ---------------------------------------------------------------------------
# project_combo_stat
# ---------------------------------------------------------------------------

def test_combo_probs_are_valid_and_sum_to_one():
    stat_values = {
        "points": [22, 18, 25, 30, 19, 21, 27, 24, 20, 23],
        "rebounds": [10, 12, 8, 14, 9, 11, 13, 7, 10, 12],
        "assists": [7, 5, 9, 6, 8, 4, 10, 6, 7, 5],
    }
    out = simulation.project_combo_stat(stat_values, line=45)  # integer -> push possible
    for key in ("prob_over", "prob_under", "prob_push"):
        assert 0.0 <= out[key] <= 1.0
    assert out["prob_over"] + out["prob_under"] + out["prob_push"] == pytest.approx(1.0)
    assert out["stats"] == ["points", "rebounds", "assists"]
    assert set(out["components"]) == {"points", "rebounds", "assists"}


def test_combo_mean_matches_sum_of_component_means():
    stat_values = {"points": [20, 30, 25], "rebounds": [10, 8, 12]}
    out = simulation.project_combo_stat(stat_values)
    assert out["mean"] == pytest.approx(sum(out["components"].values()), rel=1e-6)


def test_combo_tail_beyond_observed_max_is_nonzero():
    # Same parametric tail guarantee as single stats: max combined total here is
    # 30+14=44 in one game, but P(> 60) must still be strictly positive.
    stat_values = {
        "points": [22, 18, 25, 30, 19, 21, 27, 24, 20, 23],
        "rebounds": [10, 12, 8, 14, 9, 11, 13, 7, 10, 12],
    }
    out = simulation.project_combo_stat(stat_values, line=60)
    assert out["prob_over"] > 0.0


def test_combo_rejects_unequal_lengths():
    with pytest.raises(ValueError):
        simulation.project_combo_stat({"points": [20, 25, 30], "rebounds": [10, 12]})


def test_combo_rejects_fewer_than_two_stats():
    with pytest.raises(ValueError):
        simulation.project_combo_stat({"points": [20, 25, 30]})


def test_combo_captures_correlation_via_actual_sums():
    # Perfectly anti-correlated components: each game totals 30 exactly, so the
    # combined series has ZERO variance regardless of each stat's own spread.
    # Summing the actual same-game values captures this; treating the stats as
    # independent would not.
    stat_values = {"points": [10, 20, 5, 25], "rebounds": [20, 10, 25, 5]}
    combined = [30, 30, 30, 30]
    assert float(np.var(combined)) == 0.0
    out = simulation.project_combo_stat(stat_values, line=30)
    # A constant series collapses to Poisson at the mean, with almost all mass on 30.
    assert out["model"] == "poisson"
    assert out["mean"] == pytest.approx(30.0, rel=1e-6)


def test_simulate_combo_stat_is_reproducible_and_correlation_preserving():
    stat_values = {"points": [10, 20, 5, 25], "rebounds": [20, 10, 25, 5]}
    a = simulation.simulate_combo_stat(stat_values, n_simulations=500, seed=3)
    b = simulation.simulate_combo_stat(stat_values, n_simulations=500, seed=3)
    assert np.array_equal(a, b)
    # Every game sums to 30, so every resampled combined total is exactly 30.
    assert np.all(a == 30)


# ---------------------------------------------------------------------------
# reproducibility of the (retained) resampling helpers
# ---------------------------------------------------------------------------

def test_simulate_stat_is_reproducible_with_seed():
    values = [10, 12, 15, 8, 20]
    a = simulation.simulate_stat(values, n_simulations=1000, seed=42)
    b = simulation.simulate_stat(values, n_simulations=1000, seed=42)
    assert np.array_equal(a, b)


def test_simulate_multiple_stats_is_reproducible_with_seed():
    stat_values = {"points": [10, 20, 30], "rebounds": [5, 8, 11]}
    a = simulation.simulate_multiple_stats(stat_values, n_simulations=500, seed=7)
    b = simulation.simulate_multiple_stats(stat_values, n_simulations=500, seed=7)
    assert a == b


def test_simulate_stat_rejects_empty():
    with pytest.raises(ValueError):
        simulation.simulate_stat([])
