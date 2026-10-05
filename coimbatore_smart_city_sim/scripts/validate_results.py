#!/usr/bin/env python3
"""
validate_results.py — Command-line validation of the Coimbatore simulation.

Runs the daily model, the 365-day annual model and the coverage comparison, then
asserts the physical invariants that must hold for any scenario. Intended to be run
from the project root:

    python scripts/validate_results.py

Exit status is 0 when every check passes and 1 otherwise, so it can be used as a
pre-submission gate or in continuous integration.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.algorithms.scheduler import process_station_charging, update_fleet
from src.config.settings import Config
from src.models.ecology import coverage_status_summary, update_ecology
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine

SEPARATOR = "=" * 72
THIN = "-" * 72


class Report:
    """Collects pass/fail checks so the script can exit non-zero on failure."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, condition: bool, label: str, detail: str = "") -> bool:
        status = "PASS" if condition else "FAIL"
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{status}] {label}{suffix}")
        if not condition:
            self.failures.append(label)
        return bool(condition)


def main() -> int:
    report = Report()
    cfg = Config()
    coverage = cfg.DEFAULT_COVERAGE_PCT
    season = "summer"

    print(SEPARATOR)
    print("COIMBATORE SMART CITY SIMULATION — VALIDATION")
    print(SEPARATOR)

    # ── 1. Daily model ──────────────────────────────────────────────────────
    print(f"\n[1] Representative day — {coverage:.0f}% coverage, {season}, seed 42")
    engine = SimulationEngine(cfg, coverage_pct=coverage, season=season, seed=42)
    states = engine.run(STEPS_PER_DAY)
    summary = engine.daily_summary()

    print(f"  Steps integrated        : {summary['steps']}")
    print(f"  Solar generated         : {summary['generation_kwh']:>12,.0f} kWh")
    print(f"    self-consumed         : {summary['direct_use_kwh']:>12,.0f} kWh "
          f"({summary['direct_use_kwh'] / summary['generation_kwh'] * 100:.1f}%)")
    print(f"    curtailed             : {summary['curtailed_kwh']:>12,.0f} kWh "
          f"({summary['curtailed_kwh'] / summary['generation_kwh'] * 100:.1f}%)")
    print(f"  Total demand            : {summary['demand_kwh']:>12,.0f} kWh")
    print(f"  Grid import             : {summary['grid_import_kwh']:>12,.0f} kWh")
    print(f"  Peak generation         : {summary['peak_generation_kw']:>12,.0f} kW "
          f"of {cfg.max_generation_kw(coverage):,.0f} kW installed")
    print(f"  Battery swaps           : {summary['total_swaps']:>12d} "
          f"(solar {summary['solar_swaps']}, grid {summary['grid_swaps']})")
    print(f"  CO2 avoided             : {summary['co2_kg'] / 1000:>12,.2f} t")
    print(f"  Tariff saving           : Rs {summary['cost_inr'] / 1000:>11,.1f} k")
    print(f"  Dissolved oxygen        : {summary['min_do']:.2f} - {summary['max_do']:.2f} mg/L "
          f"({summary['eco_status']})")
    print(f"  Fleet distance          : {summary['fleet_km']:>12,.0f} km")

    print("\n  Energy balance")
    report.check(
        all(abs(s.solar_direct_use_kw + s.solar_curtailed_kw - s.generation_kw) < 0.15
            for s in states),
        "direct use + curtailment equals generation at every step",
    )
    report.check(
        all(abs(s.solar_direct_use_kw + s.grid_import_kw - s.total_demand_kw) < 0.15
            for s in states),
        "direct use + grid import equals demand at every step",
    )
    report.check(
        all(s.solar_direct_use_kw <= s.generation_kw + 0.15 for s in states),
        "self-consumption never exceeds generation",
    )
    report.check(
        all(s.grid_import_kw >= 0.0 and s.solar_curtailed_kw >= 0.0 for s in states),
        "grid import and curtailment are non-negative",
    )
    report.check(
        all(abs(s.total_demand_kw
                - (s.municipal_load_kw + s.demand_kw + s.swapping_load_kw)) < 0.15
            for s in states),
        "demand equals municipal + traction + charging",
    )

    print("\n  Fleet consistency")
    fleet_energy = sum(b.energy_consumed_kwh for b in engine.get_fleet())
    reported_traction = sum(s.demand_kw for s in states) * 0.5
    report.check(
        abs(fleet_energy - reported_traction) < 1.0,
        "reported traction demand equals energy actually drawn by the fleet",
        f"{reported_traction:,.0f} vs {fleet_energy:,.0f} kWh",
    )
    report.check(
        all(5.0 <= b.soc <= 100.0 for b in engine.get_fleet()),
        "every bus stays within its physical state-of-charge floor and ceiling",
    )
    report.check(
        min(b.soc for b in engine.get_fleet()) > 5.0,
        "no bus is stranded at the state-of-charge floor",
        f"minimum {min(b.soc for b in engine.get_fleet()):.1f}%",
    )
    report.check(
        all(
            st["ready_packs"] + len(st["charging_packs"]) + len(st["charging_queue"])
            == station["capacity_buses"]
            for station, st in zip(cfg.SWAP_STATIONS, engine._stations_state.values(), strict=True)
        ),
        "pack inventory is conserved at every hub",
    )
    report.check(
        all(v["last_active_bays"] <= cfg.CHARGER_BAYS_PER_STATION
            for v in engine._stations_state.values()),
        "charger bay limit is never exceeded",
    )

    print("\n  Impact accounting")
    direct_kwh = summary["direct_use_kwh"]
    report.check(
        abs(summary["co2_kg"] - direct_kwh * cfg.CO2_GRID_KG_PER_KWH) < 1.0,
        "CO2 is credited against self-consumed energy only, not total generation",
        f"{summary['co2_kg']:,.0f} kg",
    )
    report.check(
        summary["co2_kg"] < summary["generation_kwh"] * cfg.CO2_GRID_KG_PER_KWH,
        "CO2 credit is strictly below the all-generation-credit figure",
    )
    solar_swap_hours = sorted({s.hour for s in states if s.solar_swaps_this_step > 0})
    grid_swap_hours = sorted({s.hour for s in states if s.grid_swaps_this_step > 0})
    report.check(
        all(10.0 <= h <= 16.0 for h in solar_swap_hours),
        "solar-funded swaps occur only inside the insolation surplus window",
        f"hours {[f'{h:.1f}' for h in solar_swap_hours]}",
    )
    report.check(
        all(h < 10.0 or h > 16.0 for h in grid_swap_hours),
        "grid-assisted swaps occur only outside the insolation window",
        f"hours {[f'{h:.1f}' for h in grid_swap_hours]}",
    )

    # ── 2. Determinism ──────────────────────────────────────────────────────
    print(f"\n{THIN}\n[2] Determinism")
    repeat = SimulationEngine(Config(), coverage_pct=coverage, season=season, seed=42).run(STEPS_PER_DAY)
    report.check(
        all(
            a.cumulative_generation_kwh == b.cumulative_generation_kwh
            and a.cumulative_co2_avoided_kg == b.cumulative_co2_avoided_kg
            and a.total_swaps == b.total_swaps
            and a.do_level == b.do_level
            for a, b in zip(states, repeat, strict=True)
        ),
        "re-running with the same seed reproduces every reported quantity",
    )
    other = SimulationEngine(Config(), coverage_pct=coverage, season=season, seed=43).run(STEPS_PER_DAY)
    report.check(
        other[-1].do_level != states[-1].do_level or other[-1].generation_kw != states[-1].generation_kw,
        "a different seed produces a different realisation",
    )

    # ── 3. Coverage ladder ──────────────────────────────────────────────────
    print(f"\n{THIN}\n[3] Coverage ladder — steady-state dissolved oxygen")
    params = cfg.ecology_params()
    ambient = cfg.MONTHLY_TEMP_C[4]
    ladder = []
    print(f"  {'Coverage':>9} {'Min DO':>8} {'Max DO':>8} {'Aeration':>9}  Status")
    for cov in (0.0, 10.0, 15.0, 20.0, 25.0, 30.0, 45.0):
        result = coverage_status_summary(cov, params, initial_do=cfg.DO_INITIAL,
                                         seed=42, ambient_temp_c=ambient)
        ladder.append((cov, result))
        print(f"  {cov:>8.0f}% {result['min_do']:>8.2f} {result['max_do']:>8.2f} "
              f"{result['aeration_hours']:>8d}h  {result['ecological_status']}")

    mins = [r["min_do"] for _, r in ladder]
    report.check(
        all(mins[i] > mins[i + 1] for i in range(len(mins) - 1)),
        "minimum dissolved oxygen decreases monotonically with coverage",
        " > ".join(f"{m:.2f}" for m in mins),
    )
    report.check(
        ladder[2][1]["min_do"] >= 4.0,
        "15% coverage stays above the 4.0 mg/L threshold",
        f"{ladder[2][1]['min_do']:.2f} mg/L",
    )
    report.check(
        ladder[3][1]["min_do"] < 4.0,
        "20% coverage breaches the 4.0 mg/L threshold",
        f"{ladder[3][1]['min_do']:.2f} mg/L",
    )
    report.check(
        ladder[0][1]["aeration_hours"] == 0,
        "an uncovered lake never triggers emergency aeration",
    )
    report.check(
        any(r["aeration_hours"] > 0 for _, r in ladder),
        "the aeration mitigation path is reachable within the stress range",
    )
    uncovered = SimulationEngine(Config(), coverage_pct=0.0, season=season).run(STEPS_PER_DAY)
    report.check(
        max(s.do_level for s in uncovered) < cfg.DO_MAX,
        "the uncovered lake never reaches the dissolved-oxygen ceiling",
        f"peak {max(s.do_level for s in uncovered):.2f} of {cfg.DO_MAX:.1f} mg/L",
    )

    # ── 4. Annual model ─────────────────────────────────────────────────────
    print(f"\n{THIN}\n[4] 365-day annual model")
    annual = engine.run_annual_simulation()
    print(f"  Annual generation       : {annual['annual_generation_gwh']:>12.2f} GWh")
    print(f"  Annual self-consumed    : {annual['annual_direct_use_gwh']:>12.2f} GWh")
    print(f"  Annual curtailment      : {annual['annual_curtailed_gwh']:>12.2f} GWh")
    print(f"  Annual grid import      : {annual['annual_grid_import_gwh']:>12.2f} GWh")
    print(f"  Annual fleet demand     : {annual['annual_bus_demand_gwh']:>12.2f} GWh")
    print(f"  Annual municipal demand : {annual['annual_municipal_demand_gwh']:>12.2f} GWh")
    print(f"  CO2 avoided (self-cons.) : {annual['annual_co2_tonnes']:>11,.0f} t")
    print(f"  CO2 if all output credited: {annual['annual_co2_tonnes_if_all_generation_credited']:>11,.0f} t")
    print(f"  Analytic planning estimate: "
          f"{cfg.annual_generation_mwh(coverage) / 1000:>10.2f} GWh")

    report.check(
        abs(annual["annual_direct_use_gwh"] + annual["annual_curtailed_gwh"]
            - annual["annual_generation_gwh"]) < 0.06,
        "annual self-consumption plus curtailment equals annual generation",
    )
    report.check(
        abs(annual["annual_direct_use_gwh"] + annual["annual_grid_import_gwh"]
            - (annual["annual_bus_demand_gwh"] + annual["annual_municipal_demand_gwh"])) < 0.10,
        "annual self-consumption plus grid import equals annual demand",
    )
    report.check(
        annual["annual_co2_tonnes"] < annual["annual_co2_tonnes_if_all_generation_credited"],
        "annual carbon credit excludes curtailed energy",
    )
    report.check(
        18.0 <= annual["annual_generation_gwh"] <= 25.0,
        "annual generation is plausible for a 13 MWp Coimbatore array",
        f"{annual['annual_generation_gwh']:.2f} GWh",
    )

    # ── 5. Degenerate-configuration robustness ────────────────────────────
    print(f"\n{THIN}\n[5] Robustness to degenerate configurations")
    degenerate_ok = True
    edge_cases = [
        ("fleet size 1", {"NUM_BUSES": 1}),
        ("zero coverage", {"NUM_BUSES": 260}),
        ("no charger bays", {"CHARGER_BAYS_PER_STATION": 0}),
        ("tiny packs", {"BATTERY_CAPACITY_KWH": 1.0}),
        ("zero daily consumption", {"DAILY_CONSUMPTION_KWH": 0.0}),
    ]
    for label, overrides in edge_cases:
        edge_cfg = Config()
        for field, value in overrides.items():
            setattr(edge_cfg, field, value)
        target = 0.0 if label == "zero coverage" else 15.0
        try:
            edge_engine = SimulationEngine(edge_cfg, coverage_pct=target, season="summer")
            edge_states = edge_engine.run(STEPS_PER_DAY)
            closed = all(
                abs(s.solar_direct_use_kw + s.solar_curtailed_kw - s.generation_kw) < 0.15
                and abs(s.solar_direct_use_kw + s.grid_import_kw - s.total_demand_kw) < 0.15
                for s in edge_states
            )
            floored = min(b.soc for b in edge_engine.get_fleet()) >= 5.0
            finite = all(
                math.isfinite(v)
                for st in edge_states for v in vars(st).values()
                if isinstance(v, float)
            )
            ok = closed and floored and finite
            detail = "" if ok else (
                f"closed={closed} floored={floored} finite={finite}"
            )
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        degenerate_ok &= report.check(ok, f"survives: {label}", detail)

    for label, call in (
        ("station charging rejects a zero step",
         lambda: process_station_charging(
             {"H": {"ready_packs": 0, "charging_packs": [10.0], "charging_queue": []}},
             0.0, 150.0)),
        ("fleet update rejects a zero step",
         lambda: update_fleet([], 8.0, 219.0, {}, 300.0, 30.0, [], 5.0, 22.0, 2.0,
                              step_hours=0.0)),
        ("ecology update rejects a zero step",
         lambda: update_ecology(5.0, 12.0, 15.0, Config().ecology_params(), False,
                                step_hours=0.0)),
    ):
        try:
            call()
            raised = False
        except ValueError:
            raised = True
        except Exception:
            raised = False
        degenerate_ok &= report.check(raised, f"rejects invalid input: {label}")

    # ── 6. Solar model ──────────────────────────────────────────────────────
    print(f"\n{THIN}\n[6] Photovoltaic model")
    from src.algorithms.solar import calc_generation, floating_cooling_premium, temperature_derate

    cool_air = calc_generation(12, cfg.BASE_GENERATION_KW, cfg.max_generation_kw(coverage),
                               cloud_cover=0.0, temperature=25.0, coverage_pct=coverage)
    hot_air = calc_generation(12, cfg.BASE_GENERATION_KW, cfg.max_generation_kw(coverage),
                              cloud_cover=0.0, temperature=40.0, coverage_pct=coverage)
    print(f"  Clear-sky noon output at 25 degC : {cool_air:>10,.0f} kW")
    print(f"  Clear-sky noon output at 40 degC : {hot_air:>10,.0f} kW")
    print(f"  Floating premium over ground mount: {floating_cooling_premium(32.5) * 100 - 100:>9.1f} %")
    report.check(hot_air < cool_air,
                 "output derates as ambient temperature rises above the reference",
                 f"{hot_air:,.0f} < {cool_air:,.0f} kW")
    report.check(temperature_derate(40.0) < 1.0,
                 "the temperature derate factor is below unity in hot conditions")
    report.check(floating_cooling_premium(32.5) > 1.0,
                 "the floating array outperforms an identical ground mount")
    report.check(
        all(s.generation_kw <= cfg.max_generation_kw(coverage) + 0.1 for s in states),
        "generation never exceeds installed capacity",
    )
    report.check(
        calc_generation(12.0, cfg.BASE_GENERATION_KW, cfg.max_generation_kw(coverage),
                        cloud_cover=100.0, temperature=32.5) > 0.0,
        "overcast still yields diffuse output rather than zero",
    )
    report.check(
        all(calc_generation(hour, cfg.BASE_GENERATION_KW, cfg.max_generation_kw(cov),
                            cloud_cover=cloud, temperature=temp, coverage_pct=cov)
            <= cfg.max_generation_kw(cov) + 1e-6
            for cov in (0.0, 15.0, 50.0, 100.0)
            for hour in range(24)
            for cloud in (0.0, 50.0, 100.0)
            for temp in (15.0, 30.0, 45.0)),
        "generation never exceeds installed capacity at any coverage or condition",
    )

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n{SEPARATOR}")
    if report.failures:
        print(f"VALIDATION FAILED — {len(report.failures)} check(s) did not pass:")
        for failure in report.failures:
            print(f"  - {failure}")
        print(SEPARATOR)
        return 1
    print("VALIDATION PASSED — every check succeeded.")
    print(SEPARATOR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
