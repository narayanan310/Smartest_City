"""Regression tests for defects found by adversarial probing.

Each test here corresponds to a specific flaw that was present and has been fixed.
They are kept separate from the behavioural suites so the reason each exists stays
visible.
"""

from __future__ import annotations

import ast
import math
import pathlib
import random
import re

import pytest

from src.algorithms.scheduler import (
    bus_step_energy_kwh,
    create_fleet,
    process_station_charging,
    traction_energy_profile,
    update_fleet,
)
from src.algorithms.solar import calc_generation
from src.config.settings import Config
from src.models.bus import Bus, haversine_km
from src.models.ecology import EcologyParams, coverage_status_summary, update_ecology
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════════
# Division by zero
# ══════════════════════════════════════════════════════════════════════════

def test_station_charging_rejects_a_zero_step():
    """Regression: energy divided by a zero step length raised ZeroDivisionError."""
    state = {"Hub": {"ready_packs": 0, "charging_packs": [50.0], "charging_queue": []}}
    with pytest.raises(ValueError, match="step_hours must be positive"):
        process_station_charging(state, 0.0, 150.0)
    with pytest.raises(ValueError, match="step_hours must be positive"):
        process_station_charging(state, -0.5, 150.0)


def test_station_charging_rejects_a_negative_charger_rate():
    state = {"Hub": {"ready_packs": 0, "charging_packs": [50.0], "charging_queue": []}}
    with pytest.raises(ValueError, match="max_charge_rate_kw"):
        process_station_charging(state, 0.5, -150.0)


def test_update_fleet_rejects_a_zero_step(config):
    """Regression: reported traction power is energy / step length."""
    fleet = create_fleet(5, config.ROUTES, seed=1)
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    with pytest.raises(ValueError, match="step_hours must be positive"):
        update_fleet(fleet, 8.0, config.DAILY_CONSUMPTION_KWH, profile,
                     config.BATTERY_CAPACITY_KWH, config.SOC_SWAP_THRESHOLD,
                     config.SWAP_STATIONS, config.SERVICE_START_HOUR,
                     config.SERVICE_END_HOUR, config.SWAP_STATION_RADIUS_KM,
                     step_hours=0.0)


def test_update_fleet_rejects_a_zero_step_with_an_active_profile(config):
    """The empty-profile case masked the defect; this one has buses actually drawing."""
    fleet = create_fleet(5, config.ROUTES, seed=1)
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    assert profile, "the profile must be non-empty for this test to be meaningful"
    with pytest.raises(ValueError, match="step_hours must be positive"):
        update_fleet(fleet, 8.0, config.DAILY_CONSUMPTION_KWH, profile, 300.0, 30.0,
                     config.SWAP_STATIONS, 5.0, 22.0, 2.0, step_hours=0.0)


def test_update_ecology_rejects_a_zero_step():
    with pytest.raises(ValueError, match="step_hours must be positive"):
        update_ecology(5.0, 12.0, 15.0, EcologyParams(), False, step_hours=0.0,
                       rng=random.Random(1))


def test_bus_step_energy_tolerates_a_zero_step():
    """It returns energy, not power, so a zero step is simply zero energy."""
    bus = Bus(1, "R", [(11.0, 76.9), (11.1, 77.0)], state="running")
    profile = {8: 10.0}
    assert bus_step_energy_kwh(bus, 8.0, profile, 0.0) == 0.0


# ══════════════════════════════════════════════════════════════════════════
# Degenerate configurations must not raise
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("fleet_size", [1, 2, 7, 500])
def test_tiny_and_large_fleets_run(config, fleet_size):
    cfg = Config()
    cfg.NUM_BUSES = fleet_size
    engine = SimulationEngine(cfg, coverage_pct=15.0)
    engine.run(STEPS_PER_DAY)
    assert len(engine.get_fleet()) == fleet_size
    assert sum(engine.coverage_capacity_map().values()) == fleet_size


@pytest.mark.parametrize("coverage", [0.0, 15.0, 100.0])
def test_coverage_extremes_keep_the_energy_balance_closed(coverage):
    states = SimulationEngine(Config(), coverage_pct=coverage).run(STEPS_PER_DAY)
    for s in states:
        assert s.solar_direct_use_kw + s.solar_curtailed_kw == pytest.approx(
            s.generation_kw, abs=0.15)
        assert s.solar_direct_use_kw + s.grid_import_kw == pytest.approx(
            s.total_demand_kw, abs=0.15)


def test_zero_coverage_produces_no_generation():
    states = SimulationEngine(Config(), coverage_pct=0.0).run(STEPS_PER_DAY)
    assert all(s.generation_kw == 0.0 for s in states)
    assert all(s.grid_import_kw > 0.0 for s in states)


def test_no_charger_bays_does_not_strand_buses():
    cfg = Config()
    cfg.CHARGER_BAYS_PER_STATION = 0
    engine = SimulationEngine(cfg, coverage_pct=15.0)
    engine.run(STEPS_PER_DAY)
    assert min(b.soc for b in engine.get_fleet()) >= 5.0


def test_empty_station_list_is_tolerated():
    cfg = Config()
    cfg.SWAP_STATIONS = []
    engine = SimulationEngine(cfg, coverage_pct=15.0)
    engine.run(STEPS_PER_DAY)
    assert engine.get_station_telemetry() == []


def test_zero_capacity_station_is_tolerated():
    cfg = Config()
    cfg.SWAP_STATIONS = [dict(cfg.SWAP_STATIONS[0], capacity_buses=0)]
    engine = SimulationEngine(cfg, coverage_pct=15.0)
    engine.run(STEPS_PER_DAY)
    assert min(b.soc for b in engine.get_fleet()) >= 5.0


@pytest.mark.parametrize("waypoints", [
    [(11.0, 76.9)],
    [(11.0, 76.9)] * 6,
    [(11.0, 76.9), (11.0, 76.9), (11.1, 77.0)],
])
def test_degenerate_route_geometry_is_tolerated(waypoints):
    cfg = Config()
    cfg.ROUTES["Route 69"] = {**cfg.ROUTES["Route 69"], "waypoints": waypoints}
    engine = SimulationEngine(cfg, coverage_pct=15.0)
    engine.run(STEPS_PER_DAY)
    assert all(0.0 <= b.progress <= 1.0 for b in engine.get_fleet())


def test_inverted_service_window_yields_an_empty_profile(config):
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, 22.0, 5.0, config.PEAK_HOURS,
        config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    assert profile == {}


def test_zero_daily_consumption_draws_nothing():
    cfg = Config()
    cfg.DAILY_CONSUMPTION_KWH = 0.0
    states = SimulationEngine(cfg, coverage_pct=15.0).run(STEPS_PER_DAY)
    assert all(s.demand_kw == 0.0 for s in states)


def test_do_starting_below_the_trigger_is_clamped():
    cfg = Config()
    cfg.DO_INITIAL = 2.0
    states = SimulationEngine(cfg, coverage_pct=15.0).run(STEPS_PER_DAY)
    assert all(2.0 <= s.do_level <= 9.0 for s in states)


# ══════════════════════════════════════════════════════════════════════════
# Cross-view and arithmetic consistency
# ══════════════════════════════════════════════════════════════════════════

def test_daily_energy_equals_consumption_times_efficiency(config):
    """Regression: an earlier expectation here inverted the efficiency factor.

    ``DAILY_CONSUMPTION_KWH`` is the nominal figure; the realised value is that figure
    times the vehicle's efficiency cohort factor.
    """
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    bus = Bus(1, "R", config.ROUTES["Route 69"]["waypoints"], state="running")
    total = sum(
        bus_step_energy_kwh(bus, (step % STEPS_PER_DAY) * 0.5, profile, 0.5)
        for step in range(STEPS_PER_DAY)
    )
    expected = config.DAILY_CONSUMPTION_KWH * (0.94 + (1 % 9) * 0.015)
    assert total == pytest.approx(expected, rel=1e-9)


def test_fleet_distance_matches_the_service_window_and_bus_states():
    """Distance accrues only for in-service, non-swapping buses, and only in the window.

    Buses at a depot or mid-swap contribute nothing, so the total is bounded rather than
    exactly predictable.
    """
    cfg = Config()
    cfg.NUM_BUSES = 40
    states = SimulationEngine(cfg, coverage_pct=15.0).run(STEPS_PER_DAY)
    in_service_steps = [s for s in states if 5.0 <= s.hour < 22.0]
    assert in_service_steps, "the service window must contain steps"

    speeds = [16.0 + (i % 6) for i in range(1, cfg.NUM_BUSES + 1)]
    slowest, fastest = min(speeds), max(speeds)
    step_hours = 0.5
    steps = len(in_service_steps)

    lower = slowest * step_hours * steps          # one bus always moving
    upper = fastest * step_hours * steps * cfg.NUM_BUSES
    assert lower <= states[-1].fleet_km <= upper, (lower, states[-1].fleet_km, upper)


def test_fleet_distance_is_zero_when_no_bus_is_in_service():
    cfg = Config()
    cfg.NUM_BUSES = 10
    states = SimulationEngine(cfg, coverage_pct=15.0).run(STEPS_PER_DAY)
    # A run whose service window is empty must accrue no distance at all.
    cfg.SERVICE_START_HOUR = 22.0
    cfg.SERVICE_END_HOUR = 22.0
    empty = SimulationEngine(cfg, coverage_pct=15.0).run(STEPS_PER_DAY)
    assert empty[-1].fleet_km == 0.0
    assert states[-1].fleet_km > 0.0


def test_no_steps_are_counted_outside_the_service_window():
    cfg = Config()
    states = SimulationEngine(cfg, coverage_pct=15.0).run(STEPS_PER_DAY)
    night = [s for s in states if not (5.0 <= s.hour < 22.0)]
    assert night
    for s in night:
        # Buses are at the depot, so traction power is zero even if a pack is charging.
        assert s.demand_kw == 0.0


def test_cumulative_series_never_decrease(day_run):
    states, _ = day_run
    for field in ("cumulative_generation_kwh", "cumulative_direct_use_kwh",
                  "cumulative_curtailed_kwh", "cumulative_grid_import_kwh",
                  "cumulative_co2_avoided_kg", "cumulative_cost_saved_inr",
                  "cumulative_demand_kwh", "total_swaps", "fleet_km"):
        series = [getattr(s, field) for s in states]
        assert all(a <= b + 1e-6 for a, b in zip(series, series[1:], strict=False)), field


def test_every_reported_float_is_finite_and_non_negative(day_run):
    states, _ = day_run
    allowed_negative = {"surplus_kw", "do_delta"}
    for s in states:
        for name, value in vars(s).items():
            if not isinstance(value, float):
                continue
            assert math.isfinite(value), name
            if name not in allowed_negative:
                assert value >= 0.0, (name, value)


def test_tab1_and_tab2_agree_on_oxygen_for_every_coverage_and_season():
    params = Config().ecology_params()
    for coverage in (10.0, 15.0, 20.0, 30.0):
        for season, ambient in (("summer", 32.5), ("monsoon", 29.0)):
            live = SimulationEngine(Config(), coverage_pct=coverage, season=season).run(
                STEPS_PER_DAY)
            table = coverage_status_summary(coverage, params, initial_do=5.5, seed=42,
                                            ambient_temp_c=ambient)
            assert min(s.do_level for s in live) == pytest.approx(table["min_do"], abs=0.10)


def test_aeration_engages_only_below_the_trigger_and_releases_above_it():
    params = EcologyParams()
    rng = random.Random(4)
    do, aeration = 5.0, False
    for step in range(48 * 20):
        new_do, _, new_aeration, _, _, _ = update_ecology(
            do, (step % 48) * 0.5, 40.0, params, aeration, 0.5, rng, 32.5)
        if new_aeration and not aeration:
            assert new_do < params.aeration_on_below_mg_l + 1e-6
        if aeration and not new_aeration:
            assert new_do >= params.aeration_off_above_mg_l - 1e-6
        do, aeration = new_do, new_aeration


def test_annual_generation_is_monotonic_in_coverage():
    previous = -1.0
    for coverage in (0.0, 5.0, 10.0, 15.0, 20.0):
        annual = SimulationEngine(Config(), coverage_pct=coverage).run_annual_simulation()
        assert annual["annual_generation_gwh"] >= previous
        previous = annual["annual_generation_gwh"]


def test_haversine_over_a_quarter_of_the_globe():
    assert haversine_km(0.0, 0.0, 0.0, 180.0) == pytest.approx(math.pi * 6371.0, rel=1e-9)
    assert haversine_km(90.0, 0.0, 90.0, 0.0) == pytest.approx(0.0, abs=1e-9)


# ══════════════════════════════════════════════════════════════════════════
# Repository-level hygiene
# ══════════════════════════════════════════════════════════════════════════

#: Scripts that exist to *enforce* a rule and therefore have to spell out the very
#: tokens they forbid, and that carry throwaway harness imports. Scanning them would make
#: the guards fail on their own enforcement code.
_ENFORCEMENT_SCRIPTS = frozenset({
    "sweep_invariants.py", "probe_invariants.py",
})


def _python_sources():
    """Production, script and test sources, excluding this module's own token lists."""
    this_file = pathlib.Path(__file__).name
    for path in ROOT.rglob("*.py"):
        if ".venv" in str(path) or "__pycache__" in str(path):
            continue
        if path.name == this_file or path.name in _ENFORCEMENT_SCRIPTS:
            continue
        yield path


def test_no_module_has_an_unused_import():
    """Keeps the tree free of dead imports introduced during the refactor."""
    offenders = []
    for path in _python_sources():
        tree = ast.parse(path.read_text())
        imported = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported[(alias.asname or alias.name).split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        imported[alias.asname or alias.name] = node.lineno
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                cur = node
                while isinstance(cur, ast.Attribute):
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    used.add(cur.id)
        source = path.read_text()
        for name, lineno in imported.items():
            if name == "annotations":
                continue
            if name not in used and f'"{name}"' not in source and f"'{name}'" not in source:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno} {name}")
    assert not offenders, offenders


def test_no_source_file_references_a_superseded_identifier():
    """Old flat filenames and removed coefficients must not reappear in source."""
    forbidden = [
        "simulation_engine.py", "map_visualizer.py", "solar_calculator.py",
        "bus_scheduler.py", "ecological_monitor.py", "data_loader.py",
        "DO_SHADING_PENALTY_PER_PCT", "calc_demand(", "build_map(",
        "use_container_width",
    ]
    offenders = []
    for path in _python_sources():
        text = path.read_text()
        offenders += [f"{path.relative_to(ROOT)}: {token}"
                      for token in forbidden if token in text]
    assert not offenders, offenders


def test_solar_output_never_exceeds_nameplate_at_any_coverage():
    base = Config().BASE_GENERATION_KW
    for coverage in (0.0, 15.0, 50.0, 100.0):
        ceiling = Config().max_generation_kw(coverage)
        for hour in range(24):
            for cloud in (0.0, 50.0, 100.0):
                for temp in (15.0, 30.0, 45.0):
                    value = calc_generation(hour, base, ceiling, cloud_cover=cloud,
                                            temperature=temp, coverage_pct=coverage)
                    assert 0.0 <= value <= ceiling + 1e-6

# ══════════════════════════════════════════════════════════════════════════
# Project economics: no double counting
# ══════════════════════════════════════════════════════════════════════════

def _economics():
    """Load ``cached_economics`` from app.py without executing the dashboard."""
    import warnings

    warnings.simplefilter("ignore")
    import streamlit as st

    original = st.cache_data
    st.cache_data = lambda **_: (lambda f: f)  # unwrap the cache decorator
    try:
        from typing import Any, Dict, List

        namespace = dict(
            Config=Config, SimulationEngine=SimulationEngine,
            build_frame_trajectory=__import__(
                "src.visualization.map", fromlist=["build_frame_trajectory"]
            ).build_frame_trajectory,
            EcologyParams=EcologyParams,
            coverage_status_summary=coverage_status_summary,
            simulate_do_24h=__import__(
                "src.models.ecology", fromlist=["simulate_do_24h"]
            ).simulate_do_24h,
            st=st, Any=Any, Dict=Dict, List=List,
        )
        source = (ROOT / "app.py").read_text()
        start = source.index("def _config_from")
        end = source.index("# SESSION STATE")
        end = source.rindex("#", start, end)
        exec(compile(source[start:end], "app_helpers", "exec"), namespace)
        return namespace["cached_economics"]
    finally:
        st.cache_data = original


ECON_SCENARIOS = [
    (cov, fleet, tariff, surcharge, diesel)
    for cov in (10.0, 15.0, 20.0, 30.0)
    for fleet in (120, 260, 400)
    for tariff, surcharge in ((4.0, 0.0), (8.0, 0.54), (14.0, 2.0))
    for diesel in (60.0, 100.0, 150.0)
]


@pytest.mark.parametrize("cov,fleet,tariff,surcharge,diesel", ECON_SCENARIOS)
def test_benefit_never_exceeds_the_single_credit_bound(cov, fleet, tariff, surcharge, diesel):
    """Regression: the blended basis credited solar serving traction twice.

    Every self-consumed kWh is credited once, at the higher of the displaced-fuel or
    the retail value, so the total cannot exceed that bound.
    """
    econ = _economics()(cov, fleet, 300.0, 1.0, 1.0, 4, "summer", 42,
                        tariff, surcharge, diesel)
    assert econ["benefit_inr"] <= econ["upper_bound_benefit_inr"] + 1e-6
    assert econ["benefit_inr"] == pytest.approx(
        econ["tariff_revenue_inr"] + econ["diesel_revenue_inr"], rel=1e-12)


def test_self_consumed_energy_is_split_exactly_once():
    econ = _economics()(15.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42, 8.0, 0.54, 100.0)
    assert econ["solar_to_traction_kwh"] + econ["solar_to_municipal_kwh"] == pytest.approx(
        econ["self_consumed_kwh"], rel=1e-9)


def test_traction_fraction_is_a_proportion():
    econ = _economics()(15.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42, 8.0, 0.54, 100.0)
    assert 0.0 < econ["traction_solar_fraction"] < 1.0
    assert econ["solar_to_traction_kwh"] / econ["self_consumed_kwh"] == pytest.approx(
        econ["bus_demand_kwh"]
        / (econ["bus_demand_kwh"] + econ["municipal_demand_kwh"]), rel=1e-9)


def test_each_price_input_drives_only_its_own_stream():
    econ = _economics()
    base = econ(15.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42, 8.0, 0.54, 100.0)
    dear_tariff = econ(15.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42, 14.0, 0.54, 100.0)
    dear_diesel = econ(15.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42, 8.0, 0.54, 150.0)

    assert dear_tariff["tariff_revenue_inr"] > base["tariff_revenue_inr"]
    assert dear_tariff["diesel_revenue_inr"] == pytest.approx(base["diesel_revenue_inr"])
    assert dear_diesel["diesel_revenue_inr"] > base["diesel_revenue_inr"]
    assert dear_diesel["tariff_revenue_inr"] == pytest.approx(base["tariff_revenue_inr"])


def test_avoided_fuel_carbon_never_exceeds_the_baseline():
    econ = _economics()(15.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42, 8.0, 0.54, 100.0)
    assert 0.0 < econ["avoided_fuel_co2_t"] <= econ["baseline_fuel_co2_t"]
    # The counterfactual must be an all-diesel baseline, which is more carbon intensive
    # per kWh than grid electricity, so the two scopes are reported separately.
    assert econ["baseline_fuel_co2_t"] > econ["annual"]["annual_co2_tonnes"]


def test_diesel_exists_only_as_a_counterfactual_baseline():
    """No operational module may reference diesel: the fleet is entirely electric."""
    for name in ("bus.py", "ecology.py", "solar.py", "scheduler.py", "engine.py",
                 "settings.py"):
        text = (ROOT / "src" / "models" / name).read_text() if name in (
            "bus.py", "ecology.py") else None
        if text is None:
            folder = "algorithms" if name in ("solar.py", "scheduler.py") else (
                "simulation" if name == "engine.py" else "config")
            text = (ROOT / "src" / folder / name).read_text()
        lowered = text.lower()
        if name == "settings.py":
            # Configuration may declare the counterfactual constants.
            assert "diesel" in lowered
            continue
        assert "diesel" not in lowered, f"{name} must not reference diesel"


# ══════════════════════════════════════════════════════════════════════════
# Zero-denominator safety in the dashboard layer
# ══════════════════════════════════════════════════════════════════════════

def _app_helper(name: str, start_marker: str, end_marker: str):
    """Load a single module-level helper out of app.py without running the dashboard."""
    import warnings

    warnings.simplefilter("ignore")
    import streamlit as st

    original = st.cache_data
    st.cache_data = lambda **_: (lambda f: f)
    try:
        from typing import Any, Dict, List

        namespace = dict(
            Config=Config, SimulationEngine=SimulationEngine,
            build_frame_trajectory=__import__(
                "src.visualization.map", fromlist=["build_frame_trajectory"]
            ).build_frame_trajectory,
            EcologyParams=EcologyParams,
            coverage_status_summary=coverage_status_summary,
            simulate_do_24h=__import__(
                "src.models.ecology", fromlist=["simulate_do_24h"]
            ).simulate_do_24h,
            st=st, Any=Any, Dict=Dict, List=List,
        )
        source = (ROOT / "app.py").read_text()
        start = source.index(start_marker)
        if end_marker == "SESSION":
            # Include every cached helper, since they call one another.
            end = source.index("# SESSION STATE")
            end = source.rindex("\n#", start, end)
        else:
            end = source.index(end_marker)
        exec(compile(source[start:end], "app_helpers", "exec"), namespace)
        return namespace[name]
    finally:
        st.cache_data = original


def test_safe_ratio_survives_a_zero_denominator():
    """Regression: Tab 4 divided by annual generation, which is zero at 0% coverage."""
    ratio = _app_helper("ratio", "def ratio(", "@st.cache_data")
    assert ratio(5.0, 0.0) == 0.0
    assert ratio(5.0, 0) == 0.0
    assert ratio(5.0, 0.0, 1.0) == 1.0
    assert ratio(6.0, 3.0) == pytest.approx(2.0)
    assert ratio(0.0, 3.0) == 0.0


def test_solar_cross_check_survives_zero_installed_capacity():
    """Regression: implied specific yield divided by capacity, zero at 0% coverage."""
    cached = _app_helper("cached_solar_validation", "def _config_from", "SESSION")
    rows = cached(0.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42)
    assert len(rows) == 12
    assert all(r["Modelled yield (kWh/kWp/day)"] is None for r in rows)
    assert all(r["Ratio model/reference"] is None for r in rows)

    rows = cached(15.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42)
    assert all(r["Modelled yield (kWh/kWp/day)"] is not None for r in rows)


def test_economics_survives_zero_coverage():
    econ = _app_helper("cached_economics", "def _config_from", "SESSION")
    result = econ(0.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42, 8.0, 0.54, 100.0)
    assert result["benefit_inr"] == pytest.approx(0.0)
    assert result["self_consumed_kwh"] == pytest.approx(0.0)
    # No array means neither benefit nor operations cost, so the net is exactly zero.
    assert result["annual_opex_inr"] == pytest.approx(0.0)
    assert result["net_cashflow_inr"] == pytest.approx(0.0)
    assert result["capacity_mwp"] == pytest.approx(0.0)


# ══════════════════════════════════════════════════════════════════════════
# Map viewport
# ══════════════════════════════════════════════════════════════════════════

def south_of(poly):
    return min(p[0] for p in poly)


def north_of(poly):
    return max(p[0] for p in poly)


def west_of(poly):
    return min(p[1] for p in poly)


def east_of(poly):
    return max(p[1] for p in poly)


def test_map_view_is_derived_from_geometry_not_hand_picked():
    """Regression: the map centre/zoom were hard-coded to Coimbatore's city centre.

    ``MAP_CENTER`` was ``(11.0020, 76.9850)``, about 4 km north-east of Ukkadam Lake,
    which put the lake this project covers near the edge of the opening viewport and
    cropped every corridor east of roughly 77.02 deg E. The centre and zoom are now
    derived from the real geometry and handed to Leaflet as bounds.
    """
    cfg = Config()

    assert not hasattr(cfg, "MAP_CENTER"), "hand-picked map centre must not return"
    assert not hasattr(cfg, "MAP_ZOOM"), "hand-picked map zoom must not return"

    (south, west), (north, east) = cfg.map_bounds()

    # Ordering and hemisphere sanity: this is a (south, west), (north, east) box.
    assert south < north
    assert west < east
    assert 10.0 < south < 12.0, "Ukkadam Lake is near 11 deg N"
    assert 76.0 < west < 78.0, "Coimbatore is near 77 deg E"


def test_map_bounds_contain_every_asset_and_route():
    """The whole point of deriving the bounds: nothing may fall outside them."""
    cfg = Config()
    (south, west), (north, east) = cfg.map_bounds()

    points = list(cfg.LAKE_POLYGON) + list(cfg.SOLAR_ARRAY_POLYGON)
    points += [(s["lat"], s["lon"]) for s in cfg.SWAP_STATIONS]
    for route in cfg.ROUTES.values():
        points += list(route["waypoints"])
    assert len(points) > 100, "expected the full network, not a handful of points"

    outside = [
        p for p in points
        if not (south <= p[0] <= north and west <= p[1] <= east)
    ]
    assert outside == [], f"{len(outside)} geometry points fall outside the map bounds"


def test_map_bounds_include_the_lake_and_every_station():
    """The array being sited on the lake must always be on screen."""
    cfg = Config()
    (south, west), (north, east) = cfg.map_bounds()

    assert south <= cfg.LAKE_LAT <= north
    assert west <= cfg.LAKE_LON <= east
    for station in cfg.SWAP_STATIONS:
        assert south <= station["lat"] <= north
        assert west <= station["lon"] <= east


def test_map_center_is_the_midpoint_of_the_bounds():
    cfg = Config()
    (south, west), (north, east) = cfg.map_bounds()
    lat, lon = cfg.map_center()
    assert lat == pytest.approx((south + north) / 2.0)
    assert lon == pytest.approx((west + east) / 2.0)


def test_map_view_no_longer_crops_the_eastern_corridor():
    """The old city-centre view hid everything east of ~77.02 deg E."""
    cfg = Config()
    (_south, _west), (_north, east) = cfg.map_bounds()
    easternmost = max(p[1] for r in cfg.ROUTES.values() for p in r["waypoints"])
    assert easternmost > 77.05, "fixture sanity: a corridor really does run that far east"
    assert east >= easternmost, "the fitted bounds must reach the easternmost waypoint"


def test_lake_view_frames_the_array_at_a_close_zoom():
    """The fitted view spans ~17 km, so a lake close-up is a separate control."""
    cfg = Config()
    lat, lon, zoom = cfg.lake_view()

    assert south_of(cfg.LAKE_POLYGON) < lat < north_of(cfg.LAKE_POLYGON)
    assert west_of(cfg.LAKE_POLYGON) < lon < east_of(cfg.LAKE_POLYGON)
    assert cfg.MAP_ZOOM_MIN <= zoom <= cfg.MAP_ZOOM_MAX
    assert zoom >= 14, "the array should be legible, not a single pixel"


def test_map_bounds_track_edited_geometry():
    """Deriving the bounds must actually react to geometry changes."""
    import copy

    cfg = Config()
    before = cfg.map_bounds()

    moved = copy.deepcopy(cfg)
    far = (max(p[1] for r in moved.ROUTES.values() for p in r["waypoints"])) + 0.5
    moved.SWAP_STATIONS = [
        dict(moved.SWAP_STATIONS[0], lon=far),
    ]
    moved.ROUTES = {}
    after = moved.map_bounds()

    assert after[1][1] > before[1][1], "moving a station east must extend the bounds"


def test_build_interactive_map_html_uses_fitbounds_and_no_hardcoded_view():
    """The rendered payload must fit bounds and expose the lake close-up."""
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import build_frame_trajectory, build_interactive_map_html

    cfg = Config()
    frames = build_frame_trajectory(SimulationEngine(cfg), cfg)
    html = build_interactive_map_html(cfg, frames, 0, cfg.NUM_BUSES,
                                      cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT))

    assert "fitBounds" in html
    assert "focusLake" in html
    assert "flyTo" in html
    # The removed placeholders must not reappear.
    assert "{center_lat}" not in html and "{zoom}" not in html
    assert "L.map('map', { center:[" not in html


def test_rendered_map_bounds_match_the_configured_bounds():
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import build_frame_trajectory, build_interactive_map_html

    cfg = Config()
    frames = build_frame_trajectory(SimulationEngine(cfg), cfg)
    html = build_interactive_map_html(cfg, frames, 0, cfg.NUM_BUSES,
                                      cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT))
    (south, west), (north, east) = cfg.map_bounds()

    for value in (south, west, north, east):
        assert repr(value) in html, f"bound {value} missing from the rendered map"
    lake_lat, lake_lon, lake_zoom = cfg.lake_view()
    assert repr(lake_lat) in html and repr(lake_lon) in html
    assert f"{lake_zoom}, {{ duration:" in html, "lake close-up zoom must be rendered"


# ══════════════════════════════════════════════════════════════════════════
# State-of-charge bands
# ══════════════════════════════════════════════════════════════════════════

def test_soc_config_fields_that_did_nothing_are_gone():
    """Regression: ``SOC_GREEN`` and ``SOC_YELLOW`` were declared but never read.

    Editing either had no effect on any marker colour, status or label, which is the
    same defect class as the battery-capacity slider. The bands now live in
    module-level constants that every consumer actually reads.
    """
    cfg = Config()
    assert not hasattr(cfg, "SOC_GREEN"), "inert SOC_GREEN must not return"
    assert not hasattr(cfg, "SOC_YELLOW"), "inert SOC_YELLOW must not return"


def test_soc_bands_are_defined_in_exactly_one_place():
    """No module may re-declare a SOC band boundary as a literal."""
    literals = ("soc < 20", "soc < 30", "soc < 45", "soc > 70", "soc > 40", "soc > 20",
                "s > 70", "s > 40", "s > 20", "s < 20", "s < 40", "s < 70")
    offenders = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        # Skip this file: it necessarily spells the literals out in order to ban them.
        if ".venv" in str(path) or "__pycache__" in str(path) or rel.startswith("tests/"):
            continue
        text = path.read_text()
        for lit in literals:
            if lit in text:
                offenders.append(f"{path.relative_to(ROOT)}: {lit!r}")
    assert offenders == [], "hard-coded SOC thresholds found: " + "; ".join(offenders)


def test_soc_colour_is_left_closed_and_matches_the_scale():
    """Bands are [low, high), so exactly-at-a-boundary must land in the higher band.

    The original ``Bus.soc_color`` used ``> 70 / > 40 / > 20``, which put exactly 70%
    in amber and exactly 20% in red -- contradicting both its own legend and the
    status ladder, where ``soc < 20`` is the critical test.
    """
    from src.config.settings import (
        SOC_COLOUR_HIGH_PCT,
        SOC_COLOUR_LOW_PCT,
        SOC_COLOUR_MID_PCT,
        SOC_COLOUR_SCALE,
    )
    from src.models.bus import soc_colour

    def expected(soc: float) -> str:
        if soc < SOC_COLOUR_LOW_PCT:
            return SOC_COLOUR_SCALE[0]
        if soc < SOC_COLOUR_MID_PCT:
            return SOC_COLOUR_SCALE[1]
        if soc < SOC_COLOUR_HIGH_PCT:
            return SOC_COLOUR_SCALE[2]
        return SOC_COLOUR_SCALE[3]

    for soc in (0.0, 10.0, 19.999, 20.0, 20.001, 39.999, 40.0, 40.001,
                69.999, 70.0, 70.001, 100.0):
        assert soc_colour(soc) == expected(soc), soc


def test_soc_status_ladder_and_colour_ladder_are_independent_but_consistent():
    """A bus at 40% is "Monitor SOC" but still amber -- offset on purpose."""
    from src.models.bus import soc_colour, soc_status_label

    assert soc_status_label(40.0) == "Monitor SOC"
    assert soc_colour(40.0) == "#EAB308"
    assert soc_status_label(19.9) == "Critical SOC"
    assert soc_colour(19.9) == "#EF4444"


def test_bus_condition_and_soc_colour_agree_with_the_shared_helpers():
    from src.models.bus import Bus, soc_colour, soc_status_label

    bus = Bus(bus_id=1, route_id="R1", waypoints=[(10.98, 76.96), (11.0, 76.97)],
              state="running")
    for soc in (5.0, 25.0, 35.0, 60.0, 95.0):
        bus.soc = soc
        assert bus.condition == soc_status_label(soc)
        assert bus.soc_color == soc_colour(soc)


def test_bus_condition_still_prioritises_operational_states_over_soc():
    """The status ladder only applies to a bus actually out on a duty cycle."""
    from src.models.bus import Bus

    bus = Bus(bus_id=1, route_id="R1", waypoints=[(10.98, 76.96), (11.0, 76.97)],
              soc=5.0)
    for state, expected in (("swapping", "Charging / battery swap"),
                            ("depot", "At depot"),
                            ("heading_to_swap", "Diverting to swap station")):
        bus.state = state
        assert bus.condition == expected
        assert bus.soc_color == "#EF4444", "colour must not depend on state"


def test_soc_legend_is_generated_from_the_configured_boundaries():
    """The legend used to be hand-written and disagreed with the code at 20/40/70."""

    from src.config.settings import (
        SOC_COLOUR_HIGH_PCT,
        SOC_COLOUR_LOW_PCT,
        SOC_COLOUR_MID_PCT,
        SOC_COLOUR_SCALE,
    )
    from src.visualization.map import soc_legend_html

    html = soc_legend_html()

    for colour in SOC_COLOUR_SCALE:
        assert colour in html, f"{colour} missing from the legend"

    # The band range and its name live in separate spans so the legend rows stay
    # independently sized. Compare on the rendered text rather than on raw markup,
    # so this still pins the generated *content* rather than the exact tag structure.
    text = re.sub(r"<[^>]+>", " ", html)
    text = " ".join(text.split())

    low = f"{SOC_COLOUR_LOW_PCT:g}"
    mid = f"{SOC_COLOUR_MID_PCT:g}"
    high = f"{SOC_COLOUR_HIGH_PCT:g}"
    assert f"&lt;{low}% critical" in text
    assert f"{low}–{mid}% swap priority" in text
    assert f"{mid}–{high}% moderate" in text
    assert f"{high}%+ healthy" in text

    # The generated labels must describe the code's actual boundaries.
    assert ">70% healthy" not in text, "the old exclusive label must be gone"

    # Regression: the range and the name must be emitted as separate spans. One
    # undivided span could not shrink, so the longest label overflowed its row.
    assert html.count('class="band"') == 4, "each band needs its own range span"
    assert html.count('class="word"') == 4, "each band needs its own name span"

    # Regression: the legend is a band of wrapping rows, not a grid of cells. The
    # container was a two-column grid, so consecutive corridor rows flowed into the
    # second column and the four corridors read as two merged pairs. Pin the single
    # shared row container, which is what stops that.
    assert html.count('class="legend-rows"') == 1, "one row container for the whole legend"
    assert "<div>" not in html, "legend rows must be inline, not block cells"


def test_rendered_map_uses_configured_soc_bands_in_js_and_legend():
    import re

    from src.config.settings import (
        SOC_COLOUR_HIGH_PCT,
        SOC_COLOUR_LOW_PCT,
        SOC_COLOUR_MID_PCT,
        SOC_COLOUR_SCALE,
    )
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import build_frame_trajectory, build_interactive_map_html

    cfg = Config()
    frames = build_frame_trajectory(SimulationEngine(cfg), cfg)
    html = build_interactive_map_html(cfg, frames, 0, cfg.NUM_BUSES,
                                      cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT))

    for old in ("if (s > 70)", "if (s > 40)", "if (s > 20)"):
        assert old not in html, f"hard-coded JS threshold {old!r} survived"

    script = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    start = script.find("function socColour(s)")
    assert start != -1, "socColour() not found in the rendered script"
    body = script[start:script.index("}", script.index("return", start))]
    for value in (SOC_COLOUR_LOW_PCT, SOC_COLOUR_MID_PCT, SOC_COLOUR_HIGH_PCT):
        assert f"s < {value:g}" in body, f"band {value} not injected into the JS"
    for colour in SOC_COLOUR_SCALE:
        assert colour in body, f"{colour} not injected into the JS"


def test_map_condition_fallback_uses_the_shared_classifier():
    """The AttributeError fallback must not re-implement the ladder."""
    from src.config.settings import SOC_STATUS_LABELS
    from src.models.bus import soc_status_label
    from src.visualization.map import _bus_condition

    class Legacy:
        """Bus-like object exposing only ``state``/``soc``, no ``condition``."""

        def __init__(self, state, soc):
            self.state = state
            self.soc = soc

    for soc in (5.0, 25.0, 35.0, 60.0):
        assert _bus_condition(Legacy("running", soc)) == soc_status_label(soc)
    assert _bus_condition(Legacy("depot", 5.0)) == "At depot"
    assert _bus_condition(Legacy("swapping", 5.0)) == "Charging / battery swap"
    assert _bus_condition(Legacy("heading_to_swap", 5.0)) == "Diverting to swap station"
    assert len(SOC_STATUS_LABELS) == 4


def test_map_payload_carries_the_battery_capacity():
    """The popup's stored-energy figure must not assume the default pack size.

    The popup previously computed ``soc * 3``, which is only correct for a 300 kWh
    pack. Moving the battery slider to 150 or 450 kWh made every bus popup report
    twice or 60% of the true stored energy, while the sidebar and the fleet table
    showed the new capacity.
    """
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import build_frame_trajectory, build_payload

    for capacity in (150.0, 300.0, 450.0):
        cfg = Config()
        cfg.BATTERY_CAPACITY_KWH = capacity
        payload = build_payload(cfg, build_frame_trajectory(SimulationEngine(cfg), cfg),
                                cfg.NUM_BUSES,
                                cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT))
        assert payload["batteryKwh"] == capacity


def test_map_popup_does_not_hardcode_the_pack_size():
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import build_frame_trajectory, build_interactive_map_html

    cfg = Config()
    frames = build_frame_trajectory(SimulationEngine(cfg), cfg)
    html = build_interactive_map_html(cfg, frames, 0, cfg.NUM_BUSES,
                                      cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT))

    assert "(soc * 3)" not in html, "the hard-coded 3 kWh-per-SOC-point must be gone"
    assert "soc / 100 * D.batteryKwh" in html


def test_map_popup_stored_energy_is_correct_at_any_capacity():
    """Recompute the popup arithmetic the JS performs, for several capacities."""
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import build_frame_trajectory, build_payload

    for capacity in (150.0, 300.0, 450.0):
        cfg = Config()
        cfg.BATTERY_CAPACITY_KWH = capacity
        payload = build_payload(cfg, build_frame_trajectory(SimulationEngine(cfg), cfg),
                                cfg.NUM_BUSES,
                                cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT))
        cap = payload["batteryKwh"]
        for soc in (5.0, 42.0, 100.0):
            shown = round(soc / 100.0 * cap)
            assert shown == round(soc / 100.0 * capacity)


def test_map_popup_uses_configured_soc_thresholds():
    """The low-SOC badge and the marker radius must not re-declare a threshold."""
    from src.config.settings import SOC_SWAP_PCT
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import build_frame_trajectory, build_interactive_map_html

    cfg = Config()
    frames = build_frame_trajectory(SimulationEngine(cfg), cfg)
    html = build_interactive_map_html(cfg, frames, 0, cfg.NUM_BUSES,
                                      cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT))

    # The rendered output legitimately reads "soc < 30" because SOC_SWAP_PCT is 30.0.
    # What must not exist is a *literal* in the template source, which is what made the
    # badge and the radius silently disagree with the configured threshold.
    assert html.count(f"soc < {SOC_SWAP_PCT:g}") == 2, (
        "expected the low-SOC badge and the marker radius to share the configured "
        "swap threshold"
    )
    template = (ROOT / "src" / "visualization" / "map.py").read_text()
    assert "soc < 30" not in template, "hard-coded threshold literal in the template"
    assert template.count("soc < {soc_swap}") == 2


# ══════════════════════════════════════════════════════════════════════════
# Configuration hygiene
# ══════════════════════════════════════════════════════════════════════════

def _config_field_names() -> list:
    import ast

    tree = ast.parse((ROOT / "src" / "config" / "settings.py").read_text())
    names = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    names.append(sub.target.id)
    return names


def test_no_config_field_is_never_read():
    """Guard: every declared ``Config`` field must actually be read somewhere.

    This is the defect class that produced the battery-capacity slider, ``SOC_GREEN``,
    ``SOC_YELLOW``, the hard-coded battery size in the map popup, and the dead
    ``NUM_STEPS_PER_DAY``/``TEMP_WATER_BASE_C``/``TURBIDITY_BASE_NTU`` duplicates. A
    field that nothing reads looks authoritative in the settings file while silently
    doing nothing, which is worse than not having it.
    """
    import re

    sources = [
        p for p in ROOT.rglob("*.py")
        if ".venv" not in str(p) and "__pycache__" not in str(p)
    ]
    texts = {p: p.read_text() for p in sources}
    settings = ROOT / "src" / "config" / "settings.py"

    never_read = []
    for name in _config_field_names():
        total = 0
        for path, text in texts.items():
            for line in text.splitlines():
                # Skip the declaration itself.
                if path == settings and re.match(rf"\s*{re.escape(name)}\s*:", line):
                    continue
                total += len(re.findall(rf"\b{re.escape(name)}\b", line))
        if total == 0:
            never_read.append(name)

    assert never_read == [], (
        "these Config fields are declared but never read: "
        + ", ".join(sorted(never_read))
    )


def test_removed_duplicate_config_fields_stay_removed():
    """Each of these had a live twin, and two of them disagreed with it."""
    cfg = Config()
    for gone in ("NUM_STEPS_PER_DAY", "TEMP_WATER_BASE_C", "TURBIDITY_BASE_NTU",
                 "SOC_GREEN", "SOC_YELLOW"):
        assert not hasattr(cfg, gone), f"{gone} must not return as a duplicate"


def test_steps_per_day_is_derived_from_the_timestep():
    """The step count must not be a second, independent definition of the clock."""
    from src.simulation.engine import STEPS_PER_DAY

    assert int(round(24.0 / Config().TIME_STEP_HOURS)) == STEPS_PER_DAY
    assert STEPS_PER_DAY == 48, "the shipped 30-minute resolution still means 48 steps"

    # A coarser resolution must automatically mean fewer steps.
    coarse = Config()
    coarse.TIME_STEP_HOURS = 1.0
    assert int(round(24.0 / coarse.TIME_STEP_HOURS)) == 24


def test_ph_is_actually_passed_to_the_ecology_model():
    """Regression: ``Config.PH_BASE`` was declared but never reached the model.

    ``EcologyParams.ph_base`` fell back to its own default of 7.45, so editing the
    configured 7.6 had no effect at all on the reported pH.
    """
    cfg = Config()
    assert cfg.ecology_params().ph_base == pytest.approx(cfg.PH_BASE)


def test_water_temperature_and_turbidity_have_one_source_each():
    """The removed duplicates disagreed with the live values (26.0 vs 24.2, 22 vs 25)."""
    cfg = Config()
    params = cfg.ecology_params()
    assert params.water_temp_base_c == pytest.approx(cfg.WATER_TEMP_BASE_C)
    assert params.turbidity_base_ntu == pytest.approx(cfg.TURBIDITY_BASE_MODEL_NTU)


def test_lake_volume_uses_the_verified_depth():
    cfg = Config()
    assert pytest.approx(5.82) == cfg.LAKE_DEPTH_M
    assert cfg.lake_volume_m3() == pytest.approx(1_295_000.0 * 5.82)
    assert cfg.lake_volume_m3() == pytest.approx(7.5369e6)


def test_tile_provider_resolves_and_rejects_typos():
    cfg = Config()
    provider = cfg.tile_provider()
    assert "{z}" in provider["url"] and "{x}" in provider["url"]
    assert provider["attribution"]
    assert provider["max_zoom"].isdigit()

    cfg.MAP_TILES = "DefinitelyNotAProvider"
    with pytest.raises(ValueError, match="unknown MAP_TILES"):
        cfg.tile_provider()


def test_map_html_uses_the_configured_tile_provider():
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import build_frame_trajectory, build_interactive_map_html

    cfg = Config()
    frames = build_frame_trajectory(SimulationEngine(cfg), cfg)
    html = build_interactive_map_html(cfg, frames, 0, cfg.NUM_BUSES,
                                      cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT))
    provider = cfg.tile_provider()
    assert provider["url"] in html
    assert provider["attribution"] in html


def test_app_assumptions_table_shows_the_lake_depth():
    """``LAKE_DEPTH_M`` used to be declared and never displayed or used."""
    app = (ROOT / "app.py").read_text()
    assert "LAKE_DEPTH_M" in app
    assert "lake_volume_m3()" in app


# ══════════════════════════════════════════════════════════════════════════
# Ecology calibration harness
# ══════════════════════════════════════════════════════════════════════════

def _fit_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fit_shading", ROOT / "scripts" / "fit_shading.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fit_shading_runs_and_reports_every_section():
    """The calibration harness must stay runnable and self-consistent."""
    fit = _fit_module()
    assert fit.main(["--seeds", "2"]) == 0


def test_fit_shading_inverts_a_reachable_target_in_both_directions():
    """Regression: the bisection assumed every coefficient lowered dissolved oxygen.

    ``gross_photosynthesis_mg_l_per_hr`` *raises* it, so the original sign convention
    drove the search to the wrong end of the range and reported a converged answer that
    did not reproduce the target at all.
    """
    fit = _fit_module()
    params = Config().ecology_params()

    for coefficient, bounds in (
        ("shade_effectiveness", (1.0, 4.0)),
        ("gross_photosynthesis_mg_l_per_hr", (0.5, 2.5)),
    ):
        record = fit.solve_for_target(
            4.0, 15.0, params, coefficient, bounds=bounds,
            ambient_temp_c=32.5, initial_do=5.5,
        )
        assert record["bracketed"] == 1.0, coefficient
        assert record["converged"] == 1.0, coefficient
        assert abs(record["achieved_min_do"] - 4.0) < 1e-2, (
            f"{coefficient}: achieved {record['achieved_min_do']}, wanted 4.0"
        )
        assert bounds[0] <= record["value"] <= bounds[1]


def test_fit_shading_reports_unreachable_targets_honestly():
    fit = _fit_module()
    params = Config().ecology_params()
    record = fit.solve_for_target(
        5.0, 20.0, params, "shade_effectiveness", bounds=(1.0, 4.0),
        ambient_temp_c=32.5, initial_do=5.5,
    )
    assert record["bracketed"] == 0.0
    assert record["converged"] == 0.0
    # It must still return the closest achievable value rather than raising.
    assert record["value"] in (1.0, 4.0)


def test_influence_ranking_is_ordered_and_covers_the_shipped_coefficients():
    fit = _fit_module()
    params = Config().ecology_params()
    ranked = fit.rank_coefficient_influence(
        15.0, params, ambient_temp_c=32.5, initial_do=5.5
    )
    assert ranked, "expected at least one coefficient"
    spans = [row[3] for row in ranked]
    assert spans == sorted(spans, reverse=True), "must be sorted by influence"
    names = {row[0] for row in ranked}
    assert "shade_effectiveness" in names
    assert "gross_photosynthesis_mg_l_per_hr" in names


def test_sweep_ranges_match_the_declared_field_types():
    """``shading_hours`` is a collection; sweeping it as a scalar raises a TypeError."""
    fit = _fit_module()
    params = Config().ecology_params()
    for name, (low, high) in fit.COEFFICIENT_SWEEPS.items():
        if not hasattr(params, name):
            continue
        default = getattr(params, name)
        if isinstance(default, tuple):
            assert isinstance(low, tuple) and isinstance(high, tuple), name
        else:
            assert isinstance(low, (int, float)), name
            assert isinstance(high, (int, float)), name


def test_robustness_reports_a_seed_spread():
    fit = _fit_module()
    params = Config().ecology_params()
    stats = fit.robustness(15.0, params, range(5), ambient_temp_c=32.5, initial_do=5.5)
    assert stats["min"] <= stats["mean"] <= stats["max"]
    assert stats["span"] == pytest.approx(stats["max"] - stats["min"])
    assert 0.0 <= stats["breaches"] <= 5.0


# ══════════════════════════════════════════════════════════════════════════
# Site location has exactly one definition
# ══════════════════════════════════════════════════════════════════════════

def test_lake_coordinates_are_defined_in_one_place_only():
    """Regression: the lake position existed as three independent literals.

    ``src/services/nasa_power.py`` and ``src/models/bus.py`` each hard-coded
    ``(10.9835, 76.9554)`` alongside ``Config.LAKE_LAT``/``LAKE_LON``. Relocating the
    lake in settings would have left the NASA POWER irradiance query and the
    degenerate-route fallback silently pointing at the old coordinates.
    """
    from src.models.bus import FALLBACK_COORD
    from src.services.nasa_power import LAT, LON

    cfg = Config()
    assert (LAT, LON) == (cfg.LAKE_LAT, cfg.LAKE_LON)
    assert FALLBACK_COORD == (cfg.LAKE_LAT, cfg.LAKE_LON)

    # And no module may reintroduce a raw coordinate literal.
    offenders = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        text = path.read_text()
        for literal in ("10.9835", "76.9554"):
            if literal in text and path.name != "settings.py":
                offenders.append(f"{path.relative_to(ROOT)}: {literal}")
    assert offenders == [], "lake coordinate literals reappeared: " + "; ".join(offenders)


def test_default_coverage_and_ecological_cap_are_distinct_concepts():
    """Both are 15% today, but they mean different things and must stay separate."""
    cfg = Config()
    assert pytest.approx(cfg.ECOLOGICAL_CAP_PCT) == cfg.DEFAULT_COVERAGE_PCT, (
        "the default scenario is expected to sit at the policy cap"
    )
    assert isinstance(cfg.ECOLOGICAL_CAP_PCT, float)
    assert cfg.ECOLOGICAL_CAP_PCT > 0.0


def test_solar_utilised_is_none_at_night_not_zero():
    """Regression: no generation was reported as 0% utilisation.

    "The array produced nothing" and "the array produced something and none of it was
    used" are different statements, and averaging them understates utilisation across
    any window that includes night.
    """
    from src.simulation.engine import SimulationEngine

    engine = SimulationEngine(Config(), coverage_pct=15.0, season="summer", seed=42)
    states = engine.run(STEPS_PER_DAY)

    dark = [s for s in states if s.generation_kw == 0.0]
    lit = [s for s in states if s.generation_kw > 0.0]
    assert dark, "expected some zero-generation steps in a summer day"
    assert lit, "expected some generation"

    assert all(s.solar_utilized_pct is None for s in dark)
    assert all(s.solar_utilized_pct is not None for s in lit)
    assert all(0.0 <= s.solar_utilized_pct <= 100.0 for s in lit)


def test_every_step_reports_energy_balance_even_when_utilised_is_none():
    """The None must not disturb the accounting invariants."""
    from src.simulation.engine import SimulationEngine

    engine = SimulationEngine(Config(), coverage_pct=15.0, season="summer", seed=42)
    for state in engine.run(STEPS_PER_DAY):
        assert (state.solar_direct_use_kw + state.solar_curtailed_kw
                == pytest.approx(state.generation_kw, abs=0.15))
        assert (state.solar_direct_use_kw + state.grid_import_kw
                == pytest.approx(state.total_demand_kw, abs=0.15))


def test_bus_step_energy_rejects_a_negative_step():
    """Regression: a negative step returned negative energy instead of raising.

    The result scales linearly with ``step_hours``, so a negative step produced negative
    kWh, which reads downstream as energy *created* rather than as the caller bug it is.
    A *zero* step stays legal, because the function returns energy rather than power.
    """
    bus = Bus(1, "R", [(11.0, 76.9), (11.1, 77.0)], state="running")
    profile = {8: 10.0}

    assert bus_step_energy_kwh(bus, 8.0, profile, 0.0) == 0.0
    assert bus_step_energy_kwh(bus, 8.0, profile, 0.5) > 0.0
    with pytest.raises(ValueError, match="must not be negative"):
        bus_step_energy_kwh(bus, 8.0, profile, -0.5)


def test_zero_and_negative_step_contracts_differ_deliberately():
    """Energy-returning and power-returning step consumers differ on purpose."""
    state = {"Hub": {"ready_packs": 0, "charging_packs": [50.0], "charging_queue": []}}
    # Power must divide by the step, so a zero step is rejected.
    with pytest.raises(ValueError, match="must be positive"):
        process_station_charging(state, 0.0, 150.0)
    # Energy does not divide by the step, so a zero step is simply zero energy.
    bus = Bus(1, "R", [(11.0, 76.9), (11.1, 77.0)], state="running")
    assert bus_step_energy_kwh(bus, 8.0, {8: 10.0}, 0.0) == 0.0


def test_no_energy_consumer_can_return_negative_energy():
    """A negative step must never yield negative kWh from any step consumer."""
    bus = Bus(1, "R", [(11.0, 76.9), (11.1, 77.0)], state="running")
    profile = {8: 10.0}

    with pytest.raises(ValueError):
        bus_step_energy_kwh(bus, 8.0, profile, -1.0)
    state = {"Hub": {"ready_packs": 0, "charging_packs": [50.0], "charging_queue": []}}
    with pytest.raises(ValueError):
        process_station_charging(state, -1.0, 150.0)


def test_both_palettes_are_declared_and_both_reach_the_page():
    """The stylesheet once declared a dark palette only, so light mode repainted only
    the widgets Streamlit owns and the cards stayed dark. Both palettes must exist,
    be selectable, and cover the same tokens."""
    from src.visualization.theme import _TOKEN_NAMES, DARK, LIGHT, tokens_css

    css = tokens_css()

    # Same token set in both palettes: a token present in one and missing from the
    # other silently falls back to the dark value on a light page.
    assert set(_TOKEN_NAMES) <= set(DARK), sorted(set(_TOKEN_NAMES) - set(DARK))
    assert set(_TOKEN_NAMES) <= set(LIGHT), sorted(set(_TOKEN_NAMES) - set(LIGHT))

    for key, name in _TOKEN_NAMES.items():
        assert f"--{name}:{DARK[key]}" in css, f"dark --{name} missing from the stylesheet"
        assert f"--{name}:{LIGHT[key]}" in css, f"light --{name} missing from the stylesheet"

    # Dark is the base, so the page is correct before any script runs. The hex comes
    # from the palette, not from a literal here: pinning it would fail the test on
    # every deliberate colour change.
    assert re.search(rf":root\s*\{{[^}}]*--bg-app:{re.escape(DARK['bg_app'])};",
                     css, re.S), "no dark base block"
    # Light must be reachable from the theme the app publishes...
    assert ':root:has(.theme-sentinel[data-theme="light"])' in css
    # ...and from the OS preference when the runtime reports no theme.
    assert "@media (prefers-color-scheme: light)" in css
    assert ":root:not(:has(.theme-sentinel))" in css


def test_the_app_publishes_its_resolved_theme_for_the_css():
    """The light rules key off a sentinel the app renders, because Streamlit puts the
    theme nowhere the stylesheet can select."""
    from src.visualization.theme import resolve_theme, sentinel_html

    app_src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert "st.markdown(sentinel_html(THEME)" in app_src, \
        "the sentinel is what the :has() rules match on"
    assert resolve_theme() in ("light", "dark"), "an unknown theme must fall back, not crash"
    assert 'data-theme="light"' in sentinel_html("light")
    assert 'data-theme="dark"' in sentinel_html("dark")
    # An unrecognised value must be normalised, never passed through to the attribute.
    assert sentinel_html("dark-extra") == sentinel_html("dark")


def test_the_map_embed_tracks_the_theme():
    """The embed is an iframe and cannot read the host theme, so the app passes it in
    and the map cache key includes it: without the key, switching theme re-ran the
    script but left the map painted in the old palette."""
    from src.config.settings import Config as Cfg
    from src.simulation.engine import SimulationEngine
    from src.visualization.map import MAP_THEMES, build_frame_trajectory, build_interactive_map_html

    cfg = Cfg()
    frames = build_frame_trajectory(SimulationEngine(cfg), cfg)

    dark = build_interactive_map_html(cfg, frames, 0, theme="dark")
    light = build_interactive_map_html(cfg, frames, 0, theme="light")

    assert '<html lang="en" class="theme-dark">' in dark
    assert '<html lang="en" class="theme-light">' in light
    # Both palettes ship in both documents; the class on <html> selects one. So the
    # assertion is that the two differ, not that one palette is absent from the other.
    assert dark != light, "the theme argument did not change the rendered document"
    # The light block must actually be present, or the class selects nothing.
    assert ".theme-light {" in light and f"--bg:{MAP_THEMES['light']['bg']}" in light
    # An unrecognised theme resolves to dark rather than producing an unstyled map.
    assert build_interactive_map_html(cfg, frames, 0, theme="sepia") == dark

    app_src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text()
    assert "theme=THEME" in app_src, "the map must be told which theme is active"
    assert "map_key = (map_step, THEME)" in app_src, \
        "a theme switch must invalidate the cached map document"


def _chart_calls_in_columns(app_src: str):
    """Yield the line number of every ``chart()`` call that sits in a column.

    Streamlit columns are always entered as ``with <name>:``, so a chart whose
    nearest enclosing ``with`` binds a plain name is in a half-width column. Tabs
    bind at a shallower indent and are excluded by the name check, so this
    distinguishes "in a column" from "full width" without guessing at widths.
    """
    tab_names = {"tab_live", "tab_scenarios", "tab_finance", "tab_annual",
                 "sidebar", "expander", "top", "hero_left", "hero_right",
                 "telem_left", "telem_right", "map_toggle", "map_gutter"}
    lines = app_src.splitlines()
    for i, line in enumerate(lines):
        if line.strip() != "chart(":
            continue
        indent = len(line) - len(line.lstrip())
        enclosing = None
        for j in range(i - 1, -1, -1):
            m = re.match(r"^(\s*)with ([A-Za-z_][A-Za-z0-9_]*)\s*:\s*$", lines[j])
            if m and len(m.group(1)) < indent:
                enclosing = m.group(2)
                break
        if enclosing and enclosing not in tab_names:
            yield i + 1, enclosing


def test_every_chart_in_a_column_declares_itself_narrow():
    """The reported fault: charts were perfect full-screen but their legend and axis
    labels overlapped inside a half-width column. One fixed margin set cannot serve
    both widths, so a chart in a column has to opt into the narrow geometry."""
    app_src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text()
    in_columns = list(_chart_calls_in_columns(app_src))
    assert in_columns, "expected the dashboard to still have charts in columns"

    # Each column chart must pass narrow=True within its own call.
    lines = app_src.splitlines()
    unmarked = []
    for lineno, column in in_columns:
        indent = len(lines[lineno - 1]) - len(lines[lineno - 1].lstrip())
        close = next(k for k in range(lineno, len(lines))
                     if lines[k].strip() == ")"
                     and len(lines[k]) - len(lines[k].lstrip()) == indent)
        if "narrow=True" not in "".join(lines[lineno:close]):
            unmarked.append((lineno, column))
    assert not unmarked, f"charts in a column missing narrow=True: {unmarked}"


def test_chart_chrome_measures_its_axes_instead_of_guessing_margins():
    """The overlap happened because margins were fixed numbers: 52px of left margin
    is generous full width and narrower than the y-axis title at half width, and the
    legend wrapped to a second row the bottom margin had no room for.

    Three things must hold for the layout to hold at any width: Plotly measures the
    axes itself, the legend's rows are countable in advance, and the bottom margin is
    derived from that count rather than guessed.
    """
    app_src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text()
    body = app_src[app_src.index("def chart(fig: go.Figure"):]
    # The function ends at the next top-level definition or banner; chart() happens to
    # be the last one before the sidebar, so slicing on a fixed successor is brittle.
    m = re.search(r"\n(?=(?:def |# ─|@st\.|init_session))", body[1:])
    assert m, "could not find the end of chart(); has its position moved?"
    body = body[: m.start() + 1]

    assert "automargin=True" in body, (
        "axes must use automargin so Plotly grows the margin to fit its own labels"
    )
    assert "entrywidth=CHART_LEGEND_ENTRY_PX" in body, (
        "legend entries need a fixed width or the row count is unpredictable"
    )
    assert "entrywidthmode=" in body, "entrywidth is inert without its mode"
    assert "rows * CHART_LEGEND_ROW_PX" in body, (
        "the bottom margin must be reserved from the computed legend row count"
    )
    # Axis titles are re-issued with themed type, so they scale with narrow=True
    # instead of staying a size larger than everything around them.
    assert 'spec["title"] = dict(text=label' in body
    assert 'font=dict(size=axis_size' in body

    # The grid colour has one owner: chart() sets it once from the palette, and no call
    # site restates it. A call site that does is pinning a value that then stops
    # following the theme.
    after = app_src[app_src.index("def chart(fig: go.Figure"):]
    tail = after[after.index("return fig") + len("return fig"):]
    assert body.count("gridcolor=") == 1, "chart() must own the grid colour"
    assert "gridcolor=" not in tail, (
        "a chart call site restates gridcolor; it would stop following the theme"
    )
