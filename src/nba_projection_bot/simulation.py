"""
simulation.py — Stage 2: Monte Carlo projection engine.

This module's job: given a player's recent per-game stat values (from
data.py), simulate many hypothetical "next games" by resampling from that
history, and summarize the result — mean, median, and the probability of
clearing a sportsbook-style prop line.

NOTE: this is bootstrap resampling (sample with replacement from games we've
actually observed), not a fitted parametric distribution (e.g. Poisson,
Normal). It's simple and makes no shape assumptions, but it can't produce
values outside the observed range, and it gets noisy with a small n_games.
If that turns out to matter, that's the thing to revisit later.
"""

import numpy as np
import nba_projection_bot.data as data


def simulate_stat(
    values: list[int],
    n_simulations: int = 10_000,
) -> np.ndarray:
    """
    Simulate `n_simulations` hypothetical next-game outcomes for a single
    stat by resampling with replacement from `values`.

    Returns a 1D array of shape (n_simulations,).
    """
    # 1. Guard: if `values` is empty, raise ValueError — nothing to sample.
    #
    # 2. Create a numpy random generator (np.random.default_rng()) and use
    #    it to draw `n_simulations` samples from `values`, WITH replacement.
    #    Look up: Generator.choice — pay attention to its `replace` param.
    #
    # 3. Return the resulting array.
    if values is None or len(values) == 0:
        raise ValueError("Cannot simulate from an empty list of values.")
    rng = np.random.default_rng()
    simulated_values = rng.choice(values, size=n_simulations, replace=True)
    return simulated_values


def project_stat(
    values: list[int],
    line: float,
    n_simulations: int = 10_000,
) -> dict:
    """
    Run a Monte Carlo simulation for one stat and summarize it against a
    prop `line` (e.g. 22.5 points).

    Returns a dict with at least: mean, median, prob_over, prob_under.
    """
    # 1. Call simulate_stat(...) to get the simulated distribution.
    #
    # 2. Compute summary stats from it: mean (np.mean), median (np.median).
    #
    # 3. Compute prob_over: the fraction of simulated values strictly
    #    greater than `line`. prob_under: fraction strictly less than.
    #    (Real prop lines are usually X.5, so there's no exact tie — but
    #    think about what SHOULD happen if line were a whole number and a
    #    simulated value landed exactly on it, for your own understanding.)
    #
    # 4. Return a dict bundling mean, median, prob_over, prob_under.
    raise NotImplementedError


def simulate_multiple_stats(
    stat_values: dict[str, list[int]],
    n_simulations: int = 10_000,
) -> dict[str, np.ndarray]:
    """
    Simulate `n_simulations` hypothetical next-game outcomes for MULTIPLE
    stats at once (e.g. points, rebounds, assists — as returned by
    data.py's get_recent_stats), preserving the correlation between stats
    that came from the same historical game.

    Returns a dict mapping stat name -> array of shape (n_simulations,).
    """
    # 1. All lists in `stat_values` should be the same length (they're
    #    parallel lists over the same n_games) — worth asserting this.
    #
    # 2. The key design point: do NOT resample each stat independently.
    #    If you draw separate random indices per stat, you break the
    #    real-world correlation between e.g. points and rebounds in the
    #    same game (a big scoring night tends to come with more minutes,
    #    which affects rebounds too). Instead:
    #      a. Draw ONE array of `n_simulations` random game INDICES (with
    #         replacement) from range(n_games), using the shared game count.
    #      b. Reuse that SAME index array to pull values for every stat in
    #         `stat_values`, so simulated game #7 pulls its points, rebounds,
    #         AND assists from the same real historical game.
    #
    # 3. Return a dict of stat name -> simulated array, built from step 2.
    raise NotImplementedError


if __name__ == "__main__":
    # Stage 2 checkpoint — run `python -m nba_projection_bot.simulation` (from
    # src/) once you've filled in the functions above. Wire it up to
    # data.py's get_recent_stats() and sanity check that:
    #   - prob_over + prob_under is close to 1.0
    #   - mean/median roughly match a plain average of your input values
    #   - simulate_multiple_stats keeps stats correlated (spot check a few
    #     simulated indices against the source game logs)
    recent = data.get_recent_stats("Jokic", ["points", "rebounds"], n_games=15, season="2025-26")
    points = recent["points"]
    sim_out = simulate_stat(points, n_simulations=10_000)
    print("simulated mean:", sim_out.mean(), "vs actual mean: ", sum(points) / len(points))
