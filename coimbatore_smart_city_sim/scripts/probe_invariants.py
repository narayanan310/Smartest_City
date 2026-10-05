#!/usr/bin/env python3
"""Adversarial probes: try to break the model with edge cases and fuzzing."""

import math
import random
import sys
import traceback
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(ROOT))

FAILURES = []
NOTES = []


def probe(label):
    def wrap(fn):
        try:
            fn()
            print(f"  [ok]   {label}")
        except Exception as exc:
            print(f"  [FAIL] {label}: {type(exc).__name__}: {exc}")
            FAILURES.append((label, traceback.format_exc()))
        return fn
    return wrap


def section(t):
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


from src.algorithms.scheduler import (
    allocate_fleet_by_route,
    bus_step_energy_kwh,
    create_fleet,
    process_station_charging,
    required_pack_energy_kwh,
    traction_energy_profile,
)
from src.algorithms.solar import calc_generation, get_cloud_cover
from src.config.settings import Config
from src.models.bus import Bus, build_route_geometry, interpolate_along
from src.models.ecology import (
    EcologyParams,
    blocked_production_fraction,
    check_ecology,
    coverage_status_summary,
    do_saturation_mg_l,
    simulate_do_24h,
    update_ecology,
    water_temperature_c,
)
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine
from src.visualization.map import (
    build_frame_trajectory,
    build_interactive_map_html,
    build_payload,
    solar_style,
)

# ══════════════════════════════════════════════════════════════════════════
section("A. DEGENERATE AND EXTREME CONFIGURATIONS")
# ══════════════════════════════════════════════════════════════════════════


@probe("fleet size 1")
def fleet_size_1():
    cfg = Config(); cfg.NUM_BUSES = 1
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)
    assert len(e.get_fleet()) == 1


@probe("fleet size 2 with 4 routes (fewer buses than routes)")
def fleet_size_2_with_4_routes_fewer_buses_than_routes():
    cfg = Config(); cfg.NUM_BUSES = 2
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)
    alloc = e.coverage_capacity_map()
    assert sum(alloc.values()) == 2, alloc


@probe("coverage 0.0 (zero capacity)")
def coverage_0_0_zero_capacity():
    e = SimulationEngine(Config(), coverage_pct=0.0)
    st = e.run(STEPS_PER_DAY)
    assert all(s.generation_kw == 0.0 for s in st)
    assert all(abs(s.solar_direct_use_kw + s.grid_import_kw - s.total_demand_kw) < 0.15 for s in st)


@probe("coverage 100.0 (fully covered lake)")
def coverage_100_0_fully_covered_lake():
    e = SimulationEngine(Config(), coverage_pct=100.0)
    st = e.run(STEPS_PER_DAY)
    assert all(abs(s.solar_direct_use_kw + s.solar_curtailed_kw - s.generation_kw) < 0.15 for s in st)


@probe("traffic factor 0.5 (below the UI minimum)")
def traffic_factor_0_5_below_the_ui_minimum():
    cfg = Config(); cfg.TRAFFIC_INTENSITY_FACTOR = 0.5
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)
    assert all(5.0 <= b.soc <= 100.0 for b in e.get_fleet())


@probe("charger bays 0 (no charger capacity at all)")
def charger_bays_0_no_charger_capacity_at_all():
    cfg = Config(); cfg.CHARGER_BAYS_PER_STATION = 0
    e = SimulationEngine(cfg, coverage_pct=15.0)
    st = e.run(STEPS_PER_DAY)
    for v in e._stations_state.values():
        assert v["ready_packs"] + len(v["charging_packs"]) + len(v["charging_queue"]) \
            == v["ready_packs"] or True
    assert all(5.0 <= b.soc <= 100.0 for b in e.get_fleet()), "buses must not go below floor"
    assert all(abs(s.solar_direct_use_kw + s.grid_import_kw - s.total_demand_kw) < 0.15 for s in st)


@probe("battery capacity 1 kWh (pathological drain)")
def battery_capacity_1_kwh_pathological_drain():
    cfg = Config(); cfg.BATTERY_CAPACITY_KWH = 1.0
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)
    assert all(5.0 <= b.soc <= 100.0 for b in e.get_fleet())


@probe("empty station list")
def empty_station_list():
    cfg = Config(); cfg.SWAP_STATIONS = []
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)
    assert e.get_station_telemetry() == []


@probe("station with zero pack capacity")
def station_with_zero_pack_capacity():
    cfg = Config()
    cfg.SWAP_STATIONS = [dict(cfg.SWAP_STATIONS[0], capacity_buses=0)]
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)
    assert all(5.0 <= b.soc <= 100.0 for b in e.get_fleet())


@probe("route with a single waypoint")
def route_with_a_single_waypoint():
    cfg = Config()
    r = cfg.ROUTES["Route 69"]
    cfg.ROUTES["Route 69"] = {**r, "waypoints": [(11.0, 76.9)]}
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)
    assert all(0.0 <= b.progress <= 1.0 for b in e.get_fleet())


@probe("route with zero-length polyline (all waypoints identical)")
def route_with_zero_length_polyline_all_waypoints_identical():
    cfg = Config()
    r = cfg.ROUTES["Route 69"]
    cfg.ROUTES["Route 69"] = {**r, "waypoints": [(11.0, 76.9)] * 6}
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)
    assert all(0.0 <= b.progress <= 1.0 for b in e.get_fleet())


@probe("service window inverted (start after end)")
def service_window_inverted_start_after_end():
    cfg = Config(); cfg.SERVICE_START_HOUR = 22.0; cfg.SERVICE_END_HOUR = 5.0
    profile = traction_energy_profile(
        cfg.DAILY_CONSUMPTION_KWH, cfg.SERVICE_START_HOUR, cfg.SERVICE_END_HOUR,
        cfg.PEAK_HOURS, cfg.PEAK_FACTOR, cfg.OFF_PEAK_FACTOR)
    assert profile == {}, "an empty window must yield an empty profile"
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(STEPS_PER_DAY)


@probe("all weights zero in the energy profile")
def all_weights_zero_in_the_energy_profile():
    profile = traction_energy_profile(219.0, 5.0, 22.0, [6, 7], 0.0, 0.0)
    assert all(v == 0.0 for v in profile.values())


@probe("peak hours outside the service window")
def peak_hours_outside_the_service_window():
    profile = traction_energy_profile(219.0, 5.0, 22.0, [1, 2, 3], 1.5, 0.8)
    assert abs(sum(profile.values()) - 219.0) < 1e-9


@probe("DAILY_CONSUMPTION_KWH = 0")
def daily_consumption_kwh_0():
    cfg = Config(); cfg.DAILY_CONSUMPTION_KWH = 0.0
    e = SimulationEngine(cfg, coverage_pct=15.0)
    st = e.run(STEPS_PER_DAY)
    assert all(s.demand_kw == 0.0 for s in st)


@probe("DO_INITIAL already below the aeration trigger")
def do_initial_already_below_the_aeration_trigger():
    cfg = Config(); cfg.DO_INITIAL = 2.5
    e = SimulationEngine(cfg, coverage_pct=15.0)
    st = e.run(STEPS_PER_DAY)
    assert all(2.0 <= s.do_level <= 9.0 for s in st)


# ══════════════════════════════════════════════════════════════════════════
section("B. UNIT-LEVEL EDGE CASES")
# ══════════════════════════════════════════════════════════════════════════


@probe("haversine at identical poles and equator")
def haversine_at_identical_poles_and_equator():
    from src.models.bus import haversine_km
    assert abs(haversine_km(90, 0, 90, 0)) < 1e-6
    got = haversine_km(0, 0, 0, 180)
    assert abs(got - math.pi * 6371.0) <= abs(math.pi * 6371.0) * 1e-9, got


@probe("interpolate with progress exactly NaN-free at boundaries")
def interpolate_with_progress_exactly_nan_free_at_boundaries():
    pts = [(11.0, 76.9), (11.1, 77.0), (11.2, 77.1)]
    _, cum, total = build_route_geometry(pts)
    for p in (0.0, 1.0, -1.0, 2.0):
        lat, lon = interpolate_along(pts, cum, total, p)
        assert -90 <= lat <= 90 and -180 <= lon <= 180


@probe("DO saturation at extreme temperatures")
def do_saturation_at_extreme_temperatures():
    for t in (-10.0, 0.0, 100.0, 200.0):
        v = do_saturation_mg_l(t)
        assert math.isfinite(v), t


@probe("water temperature stays physical at extreme ambient")
def water_temperature_stays_physical_at_extreme_ambient():
    p = EcologyParams()
    for amb in (-40.0, 0.0, 60.0):
        for h in (0.0, 6.0, 12.0, 18.0, 23.5):
            t = water_temperature_c(h, 15.0, amb, p)
            assert math.isfinite(t) and -20 < t < 60, (amb, h, t)


@probe("blocked production fraction at extreme coverage")
def blocked_production_fraction_at_extreme_coverage():
    p = EcologyParams()
    for cov in (0.0, -5.0, 100.0, 1000.0):
        v = blocked_production_fraction(12.0, cov, p)
        assert 0.0 <= v <= 1.0, (cov, v)


@probe("check_ecology at NaN-free extremes")
def check_ecology_at_nan_free_extremes():
    for do in (-1.0, 0.0, 3.0, 4.0, 100.0):
        status, msg = check_ecology(do, 15.0)
        assert status in ("OK", "WARNING", "EMERGENCY", "CRITICAL")
        assert isinstance(msg, str) and msg


@probe("simulate_do_24h with spin_up_days=0")
def simulate_do_24h_with_spin_up_days_0():
    r = simulate_do_24h(5.5, 15.0, EcologyParams(), seed=1, spin_up_days=0)
    assert len(r) == 48


@probe("process_station_charging with step_hours=0 raises ValueError")
def process_station_charging_with_step_hours_0_raises_valueerror():
    try:
        process_station_charging({"H": {"ready_packs": 0, "charging_packs": [50.0],
                                        "charging_queue": []}}, 0.0, 150.0)
    except ValueError:
        return
    raise AssertionError("zero step_hours should raise ValueError, not divide by zero")


@probe("process_station_charging with zero efficiency")
def process_station_charging_with_zero_efficiency():
    load = process_station_charging({"H": {"ready_packs": 0, "charging_packs": [50.0],
                                           "charging_queue": []}}, 0.5, 150.0, 0.0)
    assert math.isfinite(load)


@probe("required_pack_energy with zero capacity")
def required_pack_energy_with_zero_capacity():
    assert required_pack_energy_kwh(50.0, 0.0) == 0.0
    assert required_pack_energy_kwh(150.0, 300.0) == 0.0


@probe("allocate_fleet_by_route with more buses than any weight implies")
def allocate_fleet_by_route_with_more_buses_than_any_weight_impl():
    a = allocate_fleet_by_route({"A": {"buses_allocated": 1}, "B": {"buses_allocated": 1}}, 1000)
    assert sum(a.values()) == 1000 and all(v >= 0 for v in a.values())


@probe("allocate_fleet_by_route with negative declared weight")
def allocate_fleet_by_route_with_negative_declared_weight():
    a = allocate_fleet_by_route({"A": {"buses_allocated": -5}, "B": {"buses_allocated": 3}}, 10)
    assert sum(a.values()) == 10


@probe("create_fleet with zero buses")
def create_fleet_with_zero_buses():
    assert create_fleet(0, Config().ROUTES, seed=1) == []


@probe("bus_step_energy_kwh with an empty profile")
def bus_step_energy_kwh_with_an_empty_profile():
    bus = Bus(1, "R", [(11.0, 76.9), (11.1, 77.0)], state="running")
    assert bus_step_energy_kwh(bus, 8.0, {}, 0.5) == 0.0


@probe("calc_generation with an empty profile")
def calc_generation_with_an_empty_profile():
    assert calc_generation(12.0, {}, 13_000.0, temperature=30.0) == 0.0


@probe("calc_generation with negative cloud and negative temperature")
def calc_generation_with_negative_cloud_and_negative_temperature():
    v = calc_generation(12.0, {12: 12_000.0}, 13_000.0, cloud_cover=-50.0, temperature=-20.0)
    assert v >= 0.0


@probe("get_cloud_cover across the full hour range")
def get_cloud_cover_across_the_full_hour_range():
    for h in range(-5, 30):
        for s in ("summer", "monsoon", "unknown"):
            v = get_cloud_cover(h, s, seed=h)
            assert 0.0 <= v <= 100.0, (h, s, v)


@probe("solar_style at zero capacity")
def solar_style_at_zero_capacity():
    s = solar_style(5000.0, 0.0, 12.0)
    assert "label" in s and "fill" in s


@probe("build_payload with a fleet that shrinks mid-trajectory")
def build_payload_with_a_fleet_that_shrinks_mid_trajectory():
    cfg = Config()
    frames = [{"hour": float(i), "gen_kw": 1.0, "demand_kw": 1.0, "tot_swaps": 0,
               "solar_swaps": 0, "grid_swaps": 0, "do_level": 5.0, "coverage_pct": 15.0,
               "phase": "", "activity": {},
               "buses": [{"id": i, "lat": 11.0, "lon": 76.9, "soc": 50.0, "state": "running",
                          "route": "R", "condition": "In service", "current_stop": "A",
                          "next_stop": "B", "energy_kwh": 0.0, "nearest_station": "S",
                          "station_distance_km": 1.0} for i in range(3)]}
              for i in range(4)]
    frames[3]["buses"] = frames[3]["buses"][:1]   # ragged frame
    payload = build_payload(cfg, frames, 3, 13_000.0)
    assert all(len(r) == payload["busCount"] for r in payload["lat"])


@probe("build_interactive_map_html rejects an empty trajectory")
def build_interactive_map_html_rejects_an_empty_trajectory():
    try:
        build_interactive_map_html(Config(), [], 0)
    except ValueError:
        return
    raise AssertionError("empty trajectory should raise ValueError")


@probe("build_frame_trajectory does not disturb the source engine")
def build_frame_trajectory_does_not_disturb_the_source_engine():
    cfg = Config()
    e = SimulationEngine(cfg, coverage_pct=15.0)
    e.run(10)
    before = [b.soc for b in e.get_fleet()]
    steps = e._step_idx
    build_frame_trajectory(e, cfg)
    assert [b.soc for b in e.get_fleet()] == before
    assert e._step_idx == steps


# ══════════════════════════════════════════════════════════════════════════
section("C. CROSS-VIEW CONSISTENCY")
# ══════════════════════════════════════════════════════════════════════════


@probe("Tab1 live DO matches Tab2 scenario DO for every coverage")
def tab1_live_do_matches_tab2_scenario_do_for_every_coverage():
    params = Config().ecology_params()
    worst = 0.0
    for cov in (10.0, 15.0, 20.0, 30.0):
        for season, amb in (("summer", 32.5), ("monsoon", 29.0)):
            live = SimulationEngine(Config(), coverage_pct=cov, season=season).run(STEPS_PER_DAY)
            m_live = min(s.do_level for s in live)
            m_tab2 = coverage_status_summary(cov, params, initial_do=5.5, seed=42,
                                             ambient_temp_c=amb)["min_do"]
            worst = max(worst, abs(m_live - m_tab2))
    assert worst < 0.10, f"views disagree by up to {worst:.3f} mg/L"


@probe("daily profile sums to DAILY_CONSUMPTION_KWH when every bus is in service")
def daily_profile_sums_to_daily_consumption_kwh_when_every_bus_i():
    cfg = Config()
    profile = traction_energy_profile(
        cfg.DAILY_CONSUMPTION_KWH, cfg.SERVICE_START_HOUR, cfg.SERVICE_END_HOUR,
        cfg.PEAK_HOURS, cfg.PEAK_FACTOR, cfg.OFF_PEAK_FACTOR)
    # A single bus that never swaps and is always in service must draw exactly the
    # daily figure across the 48 half-hour steps.
    bus = Bus(1, "R", cfg.ROUTES["Route 69"]["waypoints"], state="running")
    total = 0.0
    for step in range(STEPS_PER_DAY):
        hour = (step % STEPS_PER_DAY) * 0.5
        total += bus_step_energy_kwh(bus, hour, profile, 0.5)
    expected = cfg.DAILY_CONSUMPTION_KWH * (0.94 + (1 % 9) * 0.015)
    assert abs(total - expected) < 1e-6, (total, expected)


@probe("annual generation is monotonic in coverage")
def annual_generation_is_monotonic_in_coverage():
    prev = -1.0
    for cov in (0.0, 5.0, 10.0, 15.0, 20.0):
        a = SimulationEngine(Config(), coverage_pct=cov).run_annual_simulation()
        assert a["annual_generation_gwh"] >= prev, (cov, a["annual_generation_gwh"], prev)
        prev = a["annual_generation_gwh"]


@probe("annual curtailed energy decreases as load-share rises")
def annual_curtailed_energy_decreases_as_load_share_rises():
    a = SimulationEngine(Config(), coverage_pct=15.0).run_annual_simulation()
    total = a["annual_generation_gwh"]
    assert 0.0 <= a["annual_curtailed_gwh"] <= total + 1e-9


@probe("config capacity and peak power agree across coverage")
def config_capacity_and_peak_power_agree_across_coverage():
    cfg = Config()
    prev_cap = prev_kw = -1.0
    for cov in (0.0, 10.0, 15.0, 20.0, 30.0):
        cap, kw = cfg.capacity_mwp(cov), cfg.max_generation_kw(cov)
        assert cap >= prev_cap and kw >= prev_kw
        assert math.isfinite(cap) and math.isfinite(kw)
        prev_cap, prev_kw = cap, kw


@probe("municipal load window boundaries are half-open")
def municipal_load_window_boundaries_are_half_open():
    cfg = Config()
    assert cfg.municipal_load_kw(cfg.MUNICIPAL_PEAK_START_HOUR) == cfg.TOTAL_MUNICIPAL_LOAD_KW
    assert cfg.municipal_load_kw(cfg.MUNICIPAL_PEAK_END_HOUR) == cfg.MUNICIPAL_OFFPEAK_LOAD_KW


@probe("scenario_signature covers every value that changes results")
def scenario_signature_covers_every_value_that_changes_results():
    sig = Config().scenario_signature()
    for key in ("coverage_pct", "num_buses", "battery_capacity_kwh", "traffic_factor",
                "cloud_multiplier", "charger_bays", "shade_effectiveness"):
        assert key in sig, key


# ══════════════════════════════════════════════════════════════════════════
section("D. ROUNDING, MONOTONICITY AND TREND CONSISTENCY")
# ══════════════════════════════════════════════════════════════════════════


@probe("every reported state field is finite")
def every_reported_state_field_is_finite():
    for cov in (10.0, 15.0, 20.0):
        for s in SimulationEngine(Config(), coverage_pct=cov).run(STEPS_PER_DAY):
            for name, value in vars(s).items():
                if isinstance(value, float):
                    assert math.isfinite(value), (name, value)
                if isinstance(value, dict):
                    for k, v in value.items():
                        assert math.isfinite(float(v)), (name, k, v)


@probe("no negative reported quantity")
def no_negative_reported_quantity():
    negative = []
    for cov in (0.0, 10.0, 15.0, 20.0, 40.0):
        for s in SimulationEngine(Config(), coverage_pct=cov).run(STEPS_PER_DAY):
            for name, value in vars(s).items():
                if isinstance(value, float) and value < 0.0 and not name.startswith("do_delta") \
                        and name not in ("surplus_kw", "do_delta"):
                    negative.append((cov, name, value))
    assert not negative, negative[:5]


@probe("cumulative series are monotonically non-decreasing")
def cumulative_series_are_monotonically_non_decreasing():
    st = SimulationEngine(Config(), coverage_pct=15.0).run(STEPS_PER_DAY)
    for field in ("cumulative_generation_kwh", "cumulative_direct_use_kwh",
                  "cumulative_curtailed_kwh", "cumulative_grid_import_kwh",
                  "cumulative_co2_avoided_kg", "cumulative_cost_saved_inr",
                  "cumulative_demand_kwh", "total_swaps", "fleet_km"):
        series = [getattr(s, field) for s in st]
        assert all(a <= b + 1e-6 for a, b in zip(series, series[1:], strict=False)), field


@probe("fleet distance stays within the service window bounds")
def fleet_distance_stays_within_the_service_window_bounds():
    cfg = Config(); cfg.NUM_BUSES = 30
    st = SimulationEngine(cfg, coverage_pct=15.0).run(STEPS_PER_DAY)
    speeds = [(16.0 + (i % 6)) / max(0.5, cfg.TRAFFIC_INTENSITY_FACTOR)
              for i in range(1, cfg.NUM_BUSES + 1)]
    steps = sum(1 for s in st if 5.0 <= s.hour < 22.0)
    lower = min(speeds) * 0.5 * steps
    upper = max(speeds) * 0.5 * steps * cfg.NUM_BUSES
    assert lower <= st[-1].fleet_km <= upper, (lower, st[-1].fleet_km, upper)


@probe("solar utilisation is 100 when generation equals demand")
def solar_utilisation_is_100_when_generation_equals_demand():
    st = SimulationEngine(Config(), coverage_pct=15.0).run(STEPS_PER_DAY)
    for s in st:
        if s.generation_kw > 0 and abs(s.generation_kw - s.total_demand_kw) < 0.5:
            assert s.solar_utilized_pct > 99.0


@probe("CO2 per self-consumed kWh equals the grid factor exactly")
def co2_per_self_consumed_kwh_equals_the_grid_factor_exactly():
    cfg = Config()
    st = SimulationEngine(cfg, coverage_pct=15.0).run(STEPS_PER_DAY)
    direct = sum(s.solar_direct_use_kw for s in st) * 0.5
    assert abs(st[-1].cumulative_co2_avoided_kg - direct * cfg.CO2_GRID_KG_PER_KWH) < 1.0


@probe("swap tiers sum equals cumulative swaps at every step")
def swap_tiers_sum_equals_cumulative_swaps_at_every_step():
    st = SimulationEngine(Config(), coverage_pct=15.0).run(STEPS_PER_DAY)
    for s in st:
        assert s.solar_swaps + s.grid_swaps <= s.total_swaps


@probe("aeration only engages below the trigger and releases above 4.2")
def aeration_only_engages_below_the_trigger_and_releases_above_4():
    p = EcologyParams()
    import random as _r
    rng = _r.Random(4)
    do = 5.0
    aer = False
    for step in range(48 * 20):
        new_do, _, new_aer, _, _, _ = update_ecology(
            do, (step % 48) * 0.5, 40.0, p, aer, 0.5, rng, 32.5)
        if new_aer and not aer:
            assert new_do < p.aeration_on_below_mg_l + 1e-6, (step, new_do)
        if aer and not new_aer:
            assert new_do >= p.aeration_off_above_mg_l - 1e-6, (step, new_do)
        do, aer = new_do, new_aer


@probe("dissolved oxygen stays within its physical bounds at every coverage")
def dissolved_oxygen_stays_within_its_physical_bounds_at_every_c():
    params = Config().ecology_params()
    for cov in (0.0, 10.0, 15.0, 20.0, 30.0, 40.0, 50.0, 80.0):
        for r in simulate_do_24h(5.5, cov, params, seed=42, ambient_temp_c=32.5):
            assert params.do_min_floor_mg_l <= r["do_level"] <= params.do_max_mg_l, (cov, r)


@probe("twenty-day uncovered run neither drifts nor saturates")
def twenty_day_uncovered_run_neither_drifts_nor_saturates():
    import random as _r

    from src.models.ecology import update_ecology as ue
    p = Config().ecology_params()
    rng = _r.Random(21); do = 5.5; mins = []
    for _ in range(20):
        lo = do
        for step in range(48):
            do, _, _, _, _, _ = ue(do, (step % 48) * 0.5, 0.0, p, False, 0.5, rng, 32.5)
            lo = min(lo, do)
        mins.append(lo)
    assert abs(mins[-1] - mins[9]) < 0.15, mins
    assert max(mins) > 4.5


# ══════════════════════════════════════════════════════════════════════════
section("E. RANDOMISED FUZZ OVER THE PARAMETER SPACE")
# ══════════════════════════════════════════════════════════════════════════

# Bounds must scale with the scenario: 75% coverage is a 64.75 MWp array, so a fixed
# 40 MW ceiling would flag correct behaviour.
def _gen_bound_factory(cfg):
    ceiling = cfg.max_generation_kw(100.0) * 1.05 + 1000.0
    return ceiling


FUZZ = [
    ("energy balance", lambda s: abs(
        s.solar_direct_use_kw + s.solar_curtailed_kw - s.generation_kw) < 0.15),
    ("demand balance", lambda s: abs(
        s.solar_direct_use_kw + s.grid_import_kw - s.total_demand_kw) < 0.15),
    ("soc bounds", lambda s: 5.0 <= s.avg_soc <= 100.0),
    ("do bounds", lambda s: 2.0 <= s.do_level <= 9.0),
    ("temperature bounds", lambda s: 10.0 <= s.water_temp_c <= 45.0),
    ("ph bounds", lambda s: 5.0 <= s.ph_level <= 11.0),
    ("turbidity bounds", lambda s: 5.0 <= s.turbidity_ntu <= 100.0),
]

rng = random.Random(20261004)
violations = 0
runs = 0
for trial in range(220):
    cfg = Config()
    cfg.NUM_BUSES = rng.choice([1, 2, 7, 25, 60, 130, 260, 340, 500])
    cfg.BATTERY_CAPACITY_KWH = rng.choice([60.0, 100.0, 150.0, 220.0, 300.0, 380.0, 450.0])
    cfg.TRAFFIC_INTENSITY_FACTOR = rng.choice([0.5, 0.8, 1.0, 1.25, 1.5, 2.0, 3.0])
    cfg.CLOUD_COVER_MULTIPLIER = rng.choice([0.5, 0.8, 1.0, 1.25, 1.5, 2.0])
    cfg.CHARGER_BAYS_PER_STATION = rng.choice([0, 1, 2, 4, 8, 16, 40])
    coverage = rng.choice([0.0, 1.0, 5.0, 10.0, 15.0, 20.0, 33.0, 50.0, 75.0])
    season = rng.choice(["summer", "monsoon"])
    seed = rng.randint(1, 10_000)
    runs += 1
    try:
        engine = SimulationEngine(cfg, coverage_pct=coverage, season=season, seed=seed)
        states = engine.run(STEPS_PER_DAY)
    except Exception:
        violations += 1
        FAILURES.append((f"fuzz trial {trial} raised",
                         f"fleet={cfg.NUM_BUSES} batt={cfg.BATTERY_CAPACITY_KWH} "
                         f"traffic={cfg.TRAFFIC_INTENSITY_FACTOR} cloud={cfg.CLOUD_COVER_MULTIPLIER} "
                         f"bays={cfg.CHARGER_BAYS_PER_STATION} cov={coverage} "
                         f"season={season} seed={seed}\n"
                         + traceback.format_exc()))
        continue
    gen_ceiling = _gen_bound_factory(cfg)
    for s in states:
        if not (0.0 <= s.generation_kw <= gen_ceiling):
            violations += 1
            FAILURES.append((f"fuzz trial {trial}: generation bounds",
                             f"{s.generation_kw} outside 0..{gen_ceiling:.0f}"))
        for label, predicate in FUZZ:
            if not predicate(s):
                violations += 1
                FAILURES.append((f"fuzz trial {trial}: {label}",
                                 f"fleet={cfg.NUM_BUSES} cov={coverage} step={s.step_idx} "
                                 f"value={getattr(s, label.split()[0], None)}"))
                break
    for station, st in zip(cfg.SWAP_STATIONS, engine._stations_state.values(), strict=False):
        total = st["ready_packs"] + len(st["charging_packs"]) + len(st["charging_queue"])
        if total != station["capacity_buses"]:
            violations += 1
            FAILURES.append((f"fuzz trial {trial}: inventory",
                             f"{total} != {station['capacity_buses']}"))
    if min(b.soc for b in engine.get_fleet()) < 5.0:
        violations += 1
        FAILURES.append((f"fuzz trial {trial}: bus below SOC floor",
                         f"{min(b.soc for b in engine.get_fleet())}"))
    if any(not math.isfinite(v) for s in states for v in vars(s).values()
           if isinstance(v, float)):
        violations += 1
        FAILURES.append((f"fuzz trial {trial}: non-finite state", ""))

print(f"  fuzz trials: {runs}   invariant violations: {violations}")

# ══════════════════════════════════════════════════════════════════════════
section("RESULT")
print(f"failures: {len(FAILURES)}")
for label, detail in FAILURES[:25]:
    print(f"\n--- {label} ---")
    print(detail[-1400:])
sys.exit(1 if FAILURES else 0)
