"""
simulation.py — Stage 2: parametric projection engine.

Given a player's recent per-game values for a counting stat (points, rebounds,
...), fit a discrete count distribution and read the over/under/push
probabilities against a betting line straight off that distribution.

Why parametric instead of the old bootstrap resample:
  * Resampling with replacement can only ever return values already observed, so
    the probability of anything beyond the observed range is a hard 0 (or 1) —
    no tails. A player who scored <= 35 in his last 15 games would get
    P(> 40) == 0, which is obviously wrong.
  * 10,000 resampled draws add no information over the ~15 underlying data
    points; prob_over just re-expresses np.mean(values > line) with false
    precision.

Counting stats are non-negative integers and usually OVERDISPERSED (variance >
mean), so the natural model is the Negative Binomial, with the Poisson as the
fallback when the data is equi-/under-dispersed. We fit by method of moments —
no optimizer — so every step is inspectable, and probabilities come from the
fitted distribution's CDF/SF exactly.

The resampling helpers (`simulate_stat`, `simulate_multiple_stats`) are kept
because `simulate_multiple_stats` preserves the empirical cross-stat correlation
from shared games — useful for future combined-prop (e.g. points+rebounds+
assists) work.
"""

from __future__ import annotations

import math

import numpy as np
from scipy import stats


def recency_weights(n: int, half_life: float | None = None) -> np.ndarray:
    """
    Weights for `n` games, most-recent-first (index 0 is the latest game, matching
    data.get_recent_stats' ordering).

    With `half_life=None` every game is weighted equally. Otherwise weights decay
    exponentially so a game `half_life` games older counts half as much:
        w_i = 0.5 ** (i / half_life)
    """
    if n <= 0:
        raise ValueError("n must be positive.")
    if half_life is None:
        return np.ones(n)
    if half_life <= 0:
        raise ValueError("half_life must be positive.")
    return 0.5 ** (np.arange(n) / half_life)


def fit_count_model(
    values: list[int],
    weights: np.ndarray | None = None,
) -> tuple[stats.rv_frozen, str]:
    """
    Fit a discrete count distribution to `values` by method of moments and return
    `(frozen_distribution, model_name)` where model_name is "negative_binomial"
    or "poisson".

    If the (optionally weighted) sample variance exceeds the mean, fit a Negative
    Binomial; otherwise fall back to a Poisson.
    """
    if values is None or len(values) == 0:
        raise ValueError("Cannot fit a model to an empty list of values.")

    arr = np.asarray(values, dtype=float)
    if weights is None:
        weights = np.ones(len(arr))
    weights = np.asarray(weights, dtype=float)
    if len(weights) != len(arr):
        raise ValueError("weights must have the same length as values.")

    mean = float(np.average(arr, weights=weights))
    var = float(np.average((arr - mean) ** 2, weights=weights))

    # Overdispersed -> Negative Binomial. scipy's nbinom(n=r, p) has
    # mean = r*(1-p)/p and var = r*(1-p)/p**2; solving for our (mean, var) via
    # r = mean**2 / (var - mean), p = r / (r + mean) reproduces both moments.
    if var > mean and mean > 0:
        r = mean**2 / (var - mean)
        p = r / (r + mean)
        return stats.nbinom(r, p), "negative_binomial"

    # Equi-/under-dispersed (or degenerate) -> Poisson. Still yields real tails.
    return stats.poisson(mean), "poisson"


def project_stat(
    values: list[int],
    line: float,
    half_life: float | None = None,
) -> dict:
    """
    Fit a count model to `values` (optionally recency-weighted via `half_life`)
    and summarize it against a prop `line` (e.g. 22.5 points).

    Returns a dict with: mean, median, prob_over, prob_under, prob_push, model.

    Probabilities are exact (from the fitted CDF/SF), so unlike the old bootstrap
    they are never hard 0/1 just because the value wasn't seen in the sample. For
    an integer line the "push" (landing exactly on the line) carries real mass and
    is reported separately, so over/under/push sum to 1.
    """
    if values is None or len(values) == 0:
        raise ValueError("Cannot project from an empty list of values.")

    weights = recency_weights(len(values), half_life)
    dist, model = fit_count_model(values, weights)

    # Over the line means strictly greater: X >= floor(line) + 1 == sf(floor(line)).
    prob_over = float(dist.sf(math.floor(line)))
    # Under the line means strictly less: X <= ceil(line) - 1 == cdf(ceil(line) - 1).
    prob_under = float(dist.cdf(math.ceil(line) - 1))
    # A push only exists when the line is an integer the stat can land on exactly.
    prob_push = float(dist.pmf(int(line))) if float(line).is_integer() else 0.0

    return {
        "mean": float(dist.mean()),
        "median": float(dist.median()),
        "prob_over": prob_over,
        "prob_under": prob_under,
        "prob_push": prob_push,
        "model": model,
    }


def simulate_stat(
    values: list[int],
    n_simulations: int = 10_000,
    seed: int | None = None,
) -> np.ndarray:
    """
    Resample `n_simulations` hypothetical next-game outcomes for a single stat by
    drawing with replacement from `values`.

    NOTE: this is the empirical-bootstrap baseline. It is retained for comparison
    (see backtest.py) and as a building block for correlated multi-stat sampling;
    prefer `project_stat` for actual projections. Pass `seed` for reproducibility.
    """
    if values is None or len(values) == 0:
        raise ValueError("Cannot simulate from an empty list of values.")
    rng = np.random.default_rng(seed)
    return rng.choice(values, size=n_simulations, replace=True)


def simulate_multiple_stats(
    stat_values: dict[str, list[int]],
    n_simulations: int = 10_000,
    seed: int | None = None,
) -> dict[str, list[int]]:
    """
    Resample `n_simulations` hypothetical next-game outcomes for MULTIPLE stats at
    once, preserving the cross-stat correlation from shared historical games by
    sampling game INDICES (not each stat independently).

    Returns a dict mapping stat name -> list of simulated values. Pass `seed` for
    reproducibility.
    """
    if not stat_values:
        raise ValueError("stat_values dictionary is empty.")
    n_games = len(next(iter(stat_values.values())))
    rng = np.random.default_rng(seed)
    random_indices = rng.choice(n_games, size=n_simulations, replace=True)

    simulated_stats = {}
    for stat, values in stat_values.items():
        if len(values) != n_games:
            raise ValueError(
                f"All stat lists must have the same length. Stat '{stat}' has "
                f"length {len(values)}, expected {n_games}."
            )
        simulated_stats[stat] = np.array(values)[random_indices].tolist()
    return simulated_stats


if __name__ == "__main__":
    # Stage 2 checkpoint. Imported lazily so the engine itself has no dependency
    # on the data layer (and therefore on nba_api) — only this demo does.
    import nba_projection_bot.data as data

    recent = data.get_recent_stats(
        "Jokic", ["points", "rebounds", "assists"], n_games=15, season="2025-26"
    )
    points = recent["points"]
    observed_max = max(points)

    summary = project_stat(points, line=22.5)
    print("points:", points)
    print("projection vs 22.5:", summary)

    # Demonstrate the fix: the fitted model assigns real probability ABOVE the
    # highest value ever observed — the old bootstrap gave exactly 0 here.
    tail = project_stat(points, line=observed_max + 0.5)["prob_over"]
    print(f"P(points > observed max {observed_max}) = {tail:.4f}  (bootstrap would be 0.0000)")

    weighted = project_stat(points, line=22.5, half_life=5)
    print("recency-weighted (half_life=5) projection vs 22.5:", weighted)
