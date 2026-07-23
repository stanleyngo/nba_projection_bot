"""
backtest.py — walk-forward calibration harness for the projection engine.

Purpose: show, with numbers, that the parametric engine (simulation.project_stat)
is better-calibrated than the old bootstrap baseline. We have no historical
betting lines, so we generate a synthetic line from each game's trailing history
and use the realized game outcome as ground truth.

The metric functions (brier_score, log_loss, calibration_curve) are pure and
unit-tested. walk_forward / compare_models drive them over a real player's game
log (nba_api), and __main__ prints a side-by-side comparison.
"""

from collections.abc import Callable

import numpy as np

import nba_projection_bot.simulation as simulation


# ---------------------------------------------------------------------------
# Scoring metrics (pure)
# ---------------------------------------------------------------------------

def brier_score(preds: list[float], outcomes: list[int]) -> float:
    """Mean squared error between predicted probabilities and binary outcomes."""
    p = np.asarray(preds, dtype=float)
    o = np.asarray(outcomes, dtype=float)
    if len(p) != len(o):
        raise ValueError("preds and outcomes must have the same length.")
    return float(np.mean((p - o) ** 2))


def log_loss(preds: list[float], outcomes: list[int], eps: float = 1e-15) -> float:
    """Mean binary cross-entropy. Predictions are clipped to keep it finite."""
    p = np.clip(np.asarray(preds, dtype=float), eps, 1 - eps)
    o = np.asarray(outcomes, dtype=float)
    if len(p) != len(o):
        raise ValueError("preds and outcomes must have the same length.")
    return float(-np.mean(o * np.log(p) + (1 - o) * np.log(1 - p)))


def calibration_curve(
    preds: list[float], outcomes: list[int], n_bins: int = 10
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bin predictions into `n_bins` equal-width bins over [0, 1] and return
    (mean_predicted, mean_observed, counts) per bin. A well-calibrated model has
    mean_predicted approx mean_observed in every populated bin (the diagonal).
    """
    p = np.asarray(preds, dtype=float)
    o = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    # Bin index in [0, n_bins-1]; clip so p == 1.0 lands in the last bin.
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, n_bins - 1)

    mean_pred = np.full(n_bins, np.nan)
    mean_obs = np.full(n_bins, np.nan)
    counts = np.zeros(n_bins, dtype=int)
    for b in range(n_bins):
        mask = idx == b
        counts[b] = int(mask.sum())
        if counts[b]:
            mean_pred[b] = p[mask].mean()
            mean_obs[b] = o[mask].mean()
    return mean_pred, mean_obs, counts


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------

def trailing_median_line(history: list[int]) -> float:
    """A synthetic line: the trailing median (a book's line sits near the median)."""
    return float(np.median(history))


def walk_forward(
    values: list[int],
    min_history: int,
    predictor: Callable[[list[int], float], float],
    line_fn: Callable[[list[int]], float] = trailing_median_line,
) -> tuple[list[float], list[int]]:
    """
    Replay `values` (CHRONOLOGICAL, oldest-first) one game at a time. For each game
    with at least `min_history` prior games, set a line from the trailing history,
    ask `predictor(history, line)` for P(over), and record the realized outcome
    (1 if the actual value exceeded the line, else 0).

    Returns (predictions, outcomes) aligned by index.
    """
    preds: list[float] = []
    outcomes: list[int] = []
    for t in range(min_history, len(values)):
        history = values[:t]
        actual = values[t]
        line = line_fn(history)
        preds.append(float(predictor(history, line)))
        outcomes.append(1 if actual > line else 0)
    return preds, outcomes


# A spread of lines around the trailing median, so calibration is tested away from
# the ~50/50 point (where a median-centered line always sits). Half-points avoid
# pushes (an integer stat can never land exactly on a .5 line).
DEFAULT_OFFSETS: tuple[float, ...] = (-8.5, -6.5, -4.5, -2.5, 2.5, 4.5, 6.5, 8.5)


def walk_forward_grid(
    values: list[int],
    min_history: int,
    predictor: Callable[[list[int], float], float],
    offsets: tuple[float, ...] = DEFAULT_OFFSETS,
    base_fn: Callable[[list[int]], float] = trailing_median_line,
) -> tuple[list[float], list[int]]:
    """
    Like `walk_forward`, but for each game evaluates a GRID of lines at
    `base_fn(history) + offset` for every offset. This stress-tests the engine at
    over/under levels far from the coin-flip point, where a good model's
    predictions should spread toward 0 and 1 and track the realized hit rate.

    Returns (predictions, outcomes) with one entry per game per offset.
    """
    preds: list[float] = []
    outcomes: list[int] = []
    for t in range(min_history, len(values)):
        history = values[:t]
        actual = values[t]
        base = base_fn(history)
        for offset in offsets:
            line = base + offset
            preds.append(float(predictor(history, line)))
            outcomes.append(1 if actual > line else 0)
    return preds, outcomes


def parametric_predictor(history: list[int], line: float) -> float:
    """P(over) from the fitted count model."""
    return simulation.project_stat(history, line)["prob_over"]


def bootstrap_predictor(history: list[int], line: float) -> float:
    """P(over) from the old empirical bootstrap — the baseline we want to beat."""
    sims = simulation.simulate_stat(history, n_simulations=10_000, seed=0)
    return float(np.mean(sims > line))


def parametric_combo_predictor(history: dict[str, list[int]], line: float) -> float:
    """P(over) for a combined prop from the fitted count model on the summed series."""
    return simulation.project_combo_stat(history, line)["prob_over"]


def bootstrap_combo_predictor(history: dict[str, list[int]], line: float) -> float:
    """P(over) for a combined prop from the correlation-preserving bootstrap baseline."""
    sims = simulation.simulate_combo_stat(history, n_simulations=10_000, seed=0)
    return float(np.mean(sims > line))


def compare_models(
    values: list[int],
    min_history: int = 10,
    offsets: tuple[float, ...] | None = None,
) -> dict:
    """
    Run the walk-forward for both predictors over the same games and return their
    Brier / log-loss scores plus calibration curves.

    With `offsets` given, use the line GRID (walk_forward_grid) instead of the
    single trailing-median line, so the models are scored across a spread of
    over/under levels.
    """
    results = {}
    for name, predictor in (
        ("parametric", parametric_predictor),
        ("bootstrap", bootstrap_predictor),
    ):
        if offsets:
            preds, outcomes = walk_forward_grid(values, min_history, predictor, offsets)
        else:
            preds, outcomes = walk_forward(values, min_history, predictor)
        results[name] = {
            "n": len(preds),
            "brier": brier_score(preds, outcomes),
            "log_loss": log_loss(preds, outcomes),
            "calibration": calibration_curve(preds, outcomes, n_bins=10),
        }
    return results


def walk_forward_combo(
    stat_values: dict[str, list[int]],
    min_history: int,
    predictor: Callable[[dict[str, list[int]], float], float],
    offsets: tuple[float, ...] | None = None,
    base_fn: Callable[[list[int]], float] = trailing_median_line,
) -> tuple[list[float], list[int]]:
    """
    Walk-forward replay for a COMBINED prop. `stat_values` maps each component
    stat to its CHRONOLOGICAL (oldest-first) per-game values, all the same
    length. At each game with enough history, the line is set from the trailing
    history of the COMBINED total; the predictor is asked for P(over) given the
    per-component histories, and the realized combined total decides the outcome.

    With `offsets` given, evaluates a grid of lines around the base per game
    (like `walk_forward_grid`); otherwise a single trailing-median line.
    """
    stats = list(stat_values)
    n_games = len(stat_values[stats[0]])
    if any(len(stat_values[s]) != n_games for s in stats):
        raise ValueError("All stat lists must have the same length.")
    combined = [int(sum(game)) for game in zip(*(stat_values[s] for s in stats))]

    grid = offsets if offsets else (0.0,)
    preds: list[float] = []
    outcomes: list[int] = []
    for t in range(min_history, n_games):
        history = {s: stat_values[s][:t] for s in stats}
        actual = combined[t]
        base = base_fn(combined[:t])
        for offset in grid:
            line = base + offset
            preds.append(float(predictor(history, line)))
            outcomes.append(1 if actual > line else 0)
    return preds, outcomes


def compare_combo_models(
    stat_values: dict[str, list[int]],
    min_history: int = 10,
    offsets: tuple[float, ...] | None = None,
) -> dict:
    """
    Run the combo walk-forward for both predictors (parametric summed-series vs
    correlation-preserving bootstrap) over the same games and return their
    Brier / log-loss scores plus calibration curves — the combined-prop analogue
    of `compare_models`.
    """
    results = {}
    for name, predictor in (
        ("parametric", parametric_combo_predictor),
        ("bootstrap", bootstrap_combo_predictor),
    ):
        preds, outcomes = walk_forward_combo(stat_values, min_history, predictor, offsets)
        results[name] = {
            "n": len(preds),
            "brier": brier_score(preds, outcomes),
            "log_loss": log_loss(preds, outcomes),
            "calibration": calibration_curve(preds, outcomes, n_bins=10),
        }
    return results


def _print_report(results: dict) -> None:
    print(f"{'model':<12}{'n':>6}{'brier':>10}{'log_loss':>12}")
    for name, r in results.items():
        print(f"{name:<12}{r['n']:>6}{r['brier']:>10.4f}{r['log_loss']:>12.4f}")
    print("\ncalibration (parametric) - bin_pred vs bin_obs (count):")
    mean_pred, mean_obs, counts = results["parametric"]["calibration"]
    for mp, mo, c in zip(mean_pred, mean_obs, counts):
        if c:
            print(f"  pred={mp:.2f}  obs={mo:.2f}  n={c}")


def _parse_offsets(raw: str) -> tuple[float, ...]:
    """Parse a comma-separated offset list, e.g. '-6,-3,0,3,6'."""
    return tuple(float(x) for x in raw.split(",") if x.strip() != "")


def main(argv: list[str] | None = None) -> None:
    # Live CLI — requires nba_api and network access. Pull a long game history so
    # the walk-forward has enough games, then compare the two engines.
    import argparse

    import nba_projection_bot.data as data

    parser = argparse.ArgumentParser(
        description="Backtest the projection engine against a real player's game log."
    )
    parser.add_argument("player", nargs="?", default="Nikola Jokic",
                        help="Player name (default: Nikola Jokic).")
    parser.add_argument("--stat", default="points",
                        help="Stat to backtest (default: points).")
    parser.add_argument("--combo", type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
                        default=None,
                        help="Backtest a COMBINED prop instead of --stat: a comma-separated "
                             "list of stats to sum, e.g. --combo points,rebounds,assists.")
    parser.add_argument("--n-games", type=int, default=80,
                        help="How many recent games to pull (default: 80).")
    parser.add_argument("--min-history", type=int, default=10,
                        help="Games required before the first prediction (default: 10).")
    parser.add_argument("--season", default="2025-26",
                        help="Season to start from (default: 2025-26).")
    parser.add_argument("--grid", action="store_true",
                        help="Stress-test a grid of lines around the trailing median.")
    parser.add_argument("--offsets", type=_parse_offsets, default=None,
                        help="Custom grid offsets, comma-separated (implies --grid). "
                             "Use the = form so leading minus signs aren't read as "
                             "flags, e.g. --offsets=-6,-3,3,6.")
    args = parser.parse_args(argv)

    offsets = args.offsets
    if args.grid and offsets is None:
        offsets = DEFAULT_OFFSETS

    mode = f"grid offsets={offsets}" if offsets else "single trailing-median line"

    if args.combo:
        recent = data.get_recent_stats(
            args.player, args.combo, n_games=args.n_games, season=args.season
        )
        # data returns most-recent-first; the walk-forward wants oldest-first.
        chronological = {s.lower(): list(reversed(recent[s.lower()])) for s in args.combo}
        n = len(next(iter(chronological.values())))
        label = "+".join(args.combo)
        print(f"{args.player} - {label} (combo): {n} games | {mode}\n")
        results = compare_combo_models(chronological, min_history=args.min_history, offsets=offsets)
    else:
        recent = data.get_recent_stats(
            args.player, [args.stat], n_games=args.n_games, season=args.season
        )
        # data returns most-recent-first; the walk-forward wants oldest-first.
        chronological = list(reversed(recent[args.stat.lower()]))
        print(f"{args.player} - {args.stat}: {len(chronological)} games | {mode}\n")
        results = compare_models(chronological, min_history=args.min_history, offsets=offsets)

    _print_report(results)

    lower_brier = min(results, key=lambda k: results[k]["brier"])
    print(f"\nlower Brier (better): {lower_brier}")


if __name__ == "__main__":
    main()
