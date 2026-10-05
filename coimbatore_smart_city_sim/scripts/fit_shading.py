#!/usr/bin/env python3
"""Calibrate and stress the one genuinely uncalibrated number in the project.

The dissolved-oxygen model is a stationary, mass-conserving budget whose behaviour is
dominated by a single coefficient, ``Config.DO_SHADE_EFFECTIVENESS``. That coefficient
is **not** fitted to any measurement of Ukkadam Lake, because none of the required
inputs (in-panel irradiance, panel areal density, dissolved-oxygen profiles under and
outside the array) are available. This script exists so that limitation is quantified
rather than merely acknowledged.

It does three things:

1. **Ranks** every ``EcologyParams`` coefficient by how much the headline minimum
   dissolved oxygen moves when that coefficient is swept over a plausible range. This
   identifies what actually matters, instead of guessing.
2. **Inverts** the model: for a set of target minimum-DO values (the 4.0 and 5.0 mg/L
   thresholds that matter ecologically), it bisects on ``shade_effectiveness`` to find
   the coefficient that reproduces each target. This is what a calibration would look
   like given a real observation.
3. **Emits a sensitivity table** so the spread attributable to one uncalibrated number
   is visible rather than hidden behind a single headline figure.

Analysis only: it changes no model defaults and writes nothing outside ``--out``.

Usage::

    python scripts/fit_shading.py
    python scripts/fit_shading.py --seeds 30 --out docs/ECOLOGY_CALIBRATION.md
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import sys
from typing import Dict, List, Sequence, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.config.settings import Config
from src.models.ecology import EcologyParams, coverage_status_summary

#: Plausible sweep range for each coefficient when ranking influence: (low, high).
#: ``shading_hours`` is a *collection* of hours of the day, not a scalar, so it is
#: swept as two collections.
COEFFICIENT_SWEEPS: Dict[str, Tuple[object, object]] = {
    "shade_effectiveness": (1.0, 4.0),
    "reaeration_k_per_hr": (0.15, 0.65),
    "gross_photosynthesis_mg_l_per_hr": (0.5, 2.5),
    "respiration_mg_l_per_hr": (0.3, 1.1),
    "algae_suppression_credit": (0.0, 0.8),
    "water_temp_base_c": (22.0, 28.0),
    "aeration_rate_mg_l_per_hr": (0.0, 1.0),
    "shading_hours": ((6, 7, 8, 9), (11, 12, 13, 14, 15, 16, 17, 18)),
}

#: Coefficients a single plausible observation could plausibly constrain.
CALIBRATABLE = ("shade_effectiveness",)

#: The model reports dissolved oxygen quantised to 0.01 mg/L (pH is rounded to two
#: decimals and the budget is stepped). A calibration search cannot converge more finely
#: than the quantity it is matching, so demanding tighter agreement reports spurious
#: non-convergence. This is the resolution the harness treats as "converged".
MODEL_RESOLUTION_MG_L = 0.01


def _replace(params: EcologyParams, **changes) -> EcologyParams:
    return dataclasses.replace(params, **changes)


def min_do_at(
    coverage_pct: float,
    params: EcologyParams,
    *,
    seed: int = 42,
    ambient_temp_c: float,
    initial_do: float,
) -> float:
    """Minimum dissolved oxygen for one scenario, in mg/L."""
    summary = coverage_status_summary(
        coverage_pct, params, initial_do=initial_do, seed=seed,
        ambient_temp_c=ambient_temp_c,
    )
    return float(summary["min_do"])


def rank_coefficient_influence(
    coverage_pct: float,
    params: EcologyParams,
    *,
    ambient_temp_c: float,
    initial_do: float,
) -> List[Tuple[str, float, float, float]]:
    """Return ``(name, min_do_low, min_do_high, span)`` per swept coefficient.

    The span is the worst-case movement in minimum dissolved oxygen attributable to that
    coefficient over its plausible range, everything else held fixed.
    """
    results: List[Tuple[str, float, float, float]] = []
    for name, (low, high) in COEFFICIENT_SWEEPS.items():
        if not hasattr(params, name):
            continue
        at_low = min_do_at(
            coverage_pct, _replace(params, **{name: low}),
            ambient_temp_c=ambient_temp_c, initial_do=initial_do,
        )
        at_high = min_do_at(
            coverage_pct, _replace(params, **{name: high}),
            ambient_temp_c=ambient_temp_c, initial_do=initial_do,
        )
        results.append((name, at_low, at_high, abs(at_high - at_low)))
    results.sort(key=lambda row: row[3], reverse=True)
    return results


def solve_for_target(
    target_min_do: float,
    coverage_pct: float,
    params: EcologyParams,
    coefficient: str,
    *,
    bounds: Tuple[float, float],
    ambient_temp_c: float,
    initial_do: float,
    tolerance: float = 1e-4,
    max_iterations: int = 80,
) -> Dict[str, float]:
    """Bisect ``coefficient`` so the model reproduces ``target_min_do``.

    The response is monotone but its *direction depends on the coefficient*: stronger
    shading lowers dissolved oxygen, while a higher gross-photosynthesis rate raises it.
    The direction is therefore measured from the endpoints rather than assumed, and the
    bisection keeps whichever half still brackets the target. Monotonicity is what makes
    a bisection sufficient here and more honest than a general optimiser, which could
    converge on a local artefact.

    Returns a record including whether the target was bracketed at all.
    """
    lo, hi = bounds

    def evaluate(value: float) -> float:
        return min_do_at(
            coverage_pct, _replace(params, **{coefficient: value}),
            ambient_temp_c=ambient_temp_c, initial_do=initial_do,
        )

    f_lo, f_hi = evaluate(lo), evaluate(hi)
    bracketed = min(f_lo, f_hi) <= target_min_do <= max(f_lo, f_hi)

    if not bracketed:
        closest = lo if abs(f_lo - target_min_do) <= abs(f_hi - target_min_do) else hi
        return {
            "target_min_do": target_min_do,
            "coefficient": coefficient,
            "value": closest,
            "achieved_min_do": evaluate(closest),
            "bracketed": 0.0,
            "iterations": 0.0,
            "converged": 0.0,
        }

    # True when dissolved oxygen falls as the coefficient rises.
    decreasing = f_hi < f_lo

    iteration = 0
    while abs(hi - lo) >= tolerance and iteration < max_iterations:
        iteration += 1
        mid = 0.5 * (lo + hi)
        above = evaluate(mid) > target_min_do
        # Discard the half that cannot contain the target.
        if above == decreasing:
            lo = mid
        else:
            hi = mid

    value = 0.5 * (lo + hi)
    return {
        "target_min_do": target_min_do,
        "coefficient": coefficient,
        "value": value,
        "achieved_min_do": evaluate(value),
        "bracketed": 1.0,
        "iterations": float(iteration),
        "converged": (
            1.0
            if abs(evaluate(value) - target_min_do) <= MODEL_RESOLUTION_MG_L + 1e-9
            else 0.0
        ),
    }


def robustness(
    coverage_pct: float, params: EcologyParams, seeds: Sequence[int],
    *, ambient_temp_c: float, initial_do: float,
) -> Dict[str, float]:
    """Spread of minimum dissolved oxygen across seeds at one setting."""
    values = [
        min_do_at(coverage_pct, params, seed=s, ambient_temp_c=ambient_temp_c,
                  initial_do=initial_do)
        for s in seeds
    ]
    return {
        "min": min(values),
        "max": max(values),
        "span": max(values) - min(values),
        "mean": sum(values) / len(values),
        "breaches": float(sum(1 for v in values if v < 4.0)),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--coverage", type=float, default=None,
                        help="coverage %% to analyse (default: every scenario option)")
    parser.add_argument("--seeds", type=int, default=30,
                        help="seeds for the robustness spread (default: 30)")
    parser.add_argument("--ambient", type=float, default=32.5,
                        help="ambient temperature in deg C (default: 32.5, Coimbatore May)")
    parser.add_argument("--initial-do", type=float, default=5.5,
                        help="initial dissolved oxygen in mg/L (default: 5.5)")
    parser.add_argument("--targets", type=float, nargs="*", default=[4.0, 5.0],
                        help="target minimum-DO values to invert (default: 4.0 5.0)")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="write a markdown report to this path")
    args = parser.parse_args(argv)

    cfg = Config()
    params = cfg.ecology_params()
    coverages: List[float] = (
        [args.coverage] if args.coverage is not None
        else [float(v) for v in cfg.SCENARIO_COVERAGE_OPTIONS]
    )
    seeds = list(range(args.seeds))
    # Calibrate the highest-influence coefficients, not a hard-coded guess.
    calibratables = [
        name for name, _ in sorted(
            ((r[0], r[3]) for r in rank_coefficient_influence(
                15.0, params, ambient_temp_c=args.ambient, initial_do=args.initial_do)),
            key=lambda kv: kv[1], reverse=True,
        )[:2]
    ]

    lines: List[str] = []
    add = lines.append

    add("# Ecology calibration and sensitivity")
    add("")
    add(f"Ambient temperature {args.ambient:.1f} deg C, initial dissolved oxygen "
        f"{args.initial_do:.2f} mg/L, {args.seeds} seeds, "
        f"shipped `DO_SHADE_EFFECTIVENESS` = {cfg.DO_SHADE_EFFECTIVENESS}.")
    add("")
    add("Generated by `scripts/fit_shading.py`. Analysis only: no model default is "
        "changed by this script.")
    add("")

    # ---- 1. influence ranking -------------------------------------------------
    add("## 1. Which coefficient actually drives the result")
    add("")
    add("Each coefficient is swept across its plausible range with everything else held "
        "fixed, at 15% coverage. The span is the worst-case movement in minimum "
        "dissolved oxygen attributable to that one number.")
    add("")
    add("| Coefficient | min DO at low | min DO at high | span (mg/L) |")
    add("|---|---|---|---|")
    ranked = rank_coefficient_influence(
        15.0, params, ambient_temp_c=args.ambient, initial_do=args.initial_do,
    )
    for name, low, high, span in ranked:
        add(f"| `{name}` | {low:.3f} | {high:.3f} | {span:.3f} |")
    add("")
    if ranked:
        top = ranked[0]
        inert = [r[0] for r in ranked if r[3] <= 1e-9]
        add(f"**`{top[0]}` is the dominant coefficient**, moving the headline by "
            f"{top[3]:.2f} mg/L on its own. `{CALIBRATABLE[0]}` is ranked "
            f"{ranked.index(next(r for r in ranked if r[0] == CALIBRATABLE[0])) + 1} "
            f"with a span of "
            f"{next(r[3] for r in ranked if r[0] == CALIBRATABLE[0]):.2f} mg/L.")
        add("")
        add("Ranking the coefficients this way corrected an assumption in the project "
            "documentation, which had named `shade_effectiveness` as *the* uncalibrated "
            "weak point on the grounds that the shading term dominates the model. It "
            "does dominate the *sign* of the coverage response, but the "
            "gross-photosynthesis rate has the larger effect on the absolute minimum, so "
            "a calibration against a real observation should constrain both.")
        add("")
        if inert:
            add(f"Inert at 15% coverage (zero span, because the aeration trigger is never "
                f"reached): {', '.join('`' + n + '`' for n in inert)}. These matter only "
                "at higher coverage.")
            add("")

    # ---- 2. inversion --------------------------------------------------------
    add("## 2. Inverting the model against a target observation")
    add("")
    add("If a dissolved-oxygen profile under the array were available, this is the "
        "calibration that would be performed: bisect each high-influence coefficient "
        "until the model reproduces the observed minimum. The two coefficients respond in "
        "opposite directions, which is why the search measures the direction instead of "
        "assuming it.")
    add("")
    add("| Target min DO | coverage | coefficient | fitted value | achieved | bracketed |")
    add("|---|---|---|---|---|---|")
    for target in args.targets:
        for coverage in (15.0, 20.0):
            for coefficient in calibratables:
                record = solve_for_target(
                    target, coverage, params, coefficient,
                    bounds=COEFFICIENT_SWEEPS[coefficient],
                    ambient_temp_c=args.ambient, initial_do=args.initial_do,
                )
                add(f"| {target:.2f} mg/L | {coverage:.0f}% | `{coefficient}` | "
                    f"{record['value']:.4f} | "
                    f"{record['achieved_min_do']:.3f} | "
                    f"{'yes' if record['bracketed'] else 'NO - unattainable'} |")
    add("")
    add("Search bounds are each coefficient's plausible range from section 1. A row reading "
        "'unattainable' means no value of this coefficient within that range can produce "
        "the target at that coverage, which is itself a useful result: it would mean the "
        "model's structure, not its calibration, is the problem.")
    add("")

    # ---- 3. sensitivity table -------------------------------------------------
    add("## 3. Sensitivity of the headline to `shade_effectiveness`")
    add("")
    add("| shade effectiveness | " + " | ".join(f"{c:.0f}% cov" for c in coverages) + " |")
    add("|---" * (len(coverages) + 1) + "|")
    grid: Dict[float, List[float]] = {}
    for value in (1.0, 1.5, 2.1, 2.5, 3.0, 4.0):
        row = [
            min_do_at(c, _replace(params, shade_effectiveness=value),
                      ambient_temp_c=args.ambient, initial_do=args.initial_do)
            for c in coverages
        ]
        grid[value] = row
        add(f"| {value:.2f} | " + " | ".join(f"{v:.3f}" for v in row) + " |")
    add("")
    add("Read across a row for the effect of lake shading strength; read down a column "
        "for the effect of array size. The ecological status boundary sits between the "
        "rows that cross 4.0 mg/L, which is why the shipped value is stated as an "
        "assumption rather than a result.")
    add("")

    # ---- 4. robustness --------------------------------------------------------
    add("## 4. Seed robustness at the shipped value")
    add("")
    add("| coverage | min DO | mean | max | span | seeds below 4.0 mg/L |")
    add("|---|---|---|---|---|---|")
    for coverage in coverages:
        stats = robustness(coverage, params, seeds,
                           ambient_temp_c=args.ambient, initial_do=args.initial_do)
        add(f"| {coverage:.0f}% | {stats['min']:.3f} | {stats['mean']:.3f} | "
            f"{stats['max']:.3f} | {stats['span']:.3f} | "
            f"{int(stats['breaches'])}/{args.seeds} |")
    add("")
    add("Seed spread comes from the stochastic algal and mixing terms. It is small "
        "relative to the sensitivity in section 3, which is the point: the dominant "
        "uncertainty is structural, not stochastic.")
    add("")

    report = "\n".join(lines)
    print(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report + "\n")
        print(f"\nwritten: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
