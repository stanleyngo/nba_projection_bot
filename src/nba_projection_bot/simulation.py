"""
simulation.py — Stage 2: Monte Carlo projection engine.

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

    Returns a 1D array of simulated values with size n_simulations.
    """
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
    simulated_values = simulate_stat(values, n_simulations)
    mean = np.mean(simulated_values)
    median = np.median(simulated_values)
    prob_over = np.mean(simulated_values > line)
    prob_under = np.mean(simulated_values < line)
    return {
        "mean": float(mean),
        "median": float(median),
        "prob_over": float(prob_over),
        "prob_under": float(prob_under),
    }


def simulate_multiple_stats(
    stat_values: dict[str, list[int]],
    n_simulations: int = 10_000,
) -> dict[str, list[int]]:
    """
    Simulate `n_simulations` hypothetical next-game outcomes for MULTIPLE
    stats at once (e.g. points, rebounds, assists — as returned by
    data.py's get_recent_stats), preserving the correlation between stats
    that came from the same historical game.

    Returns a dict mapping stat name -> array of values of size n_simulations.
    """
    if not stat_values:
        raise ValueError("stat_values dictionary is empty.")
    n_games = len(next(iter(stat_values.values())))
    rng = np.random.default_rng()
    random_indices = rng.choice(n_games, size=n_simulations, replace=True)

    simulated_stats = {}
    for stat, values in stat_values.items():
        if len(values) != n_games:
            raise ValueError(f"All stat lists must have the same length. Stat '{stat}' has length {len(values)}, expected {n_games}.")
        simulated_stats[stat] = np.array(values)[random_indices].tolist()
    return simulated_stats


if __name__ == "__main__":
    recent = data.get_recent_stats("Jokic", ["points", "rebounds", "assists"], n_games=15, season="2025-26")
    points = recent["points"]
    sim_out = simulate_stat(points, n_simulations=10_000)
    summary = project_stat(points, line=22.5, n_simulations=10_000)
    multiple_stats = simulate_multiple_stats(recent, n_simulations=10_000)
    print("summary:", summary)
    print("simulated mean:", sim_out.mean(), "vs actual mean: ", sum(points) / len(points))
    print("multiple stats:", {stat: np.mean(values) for stat, values in multiple_stats.items()})
