"""Tests for the simulation engine: determinism, conservation and accounting."""

from __future__ import annotations

import pytest

from src.config.settings import Config
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine
from src.visualization.map import build_frame_trajectory, build_interactive_map_html

# ── determinism ──────────────────────────────────────────────────────────

def test_same_seed_reproduces_every_reported_quantity():
    a = SimulationEngine(Config(), seed=42).run(15)
    b = SimulationEngine(Config(), seed=42).run(15)

    assert a[-1].do_level == b[-1].do_level
    assert a[-1].total_swaps == b[-1].total_swaps
    assert a[-1].cumulative_generation_kwh == b[-1].cumulative_generation_kwh
    assert a[-1].cumulative_co2_avoided_kg == b[-1].cumulative_co2_avoided_kg
    for x, y in zip(SimulationEngine(Config(), seed=42).get_fleet(),
                    SimulationEngine(Config(), seed=42).get_fleet(), strict=False):
        assert x.soc == y.soc
        assert x.lat == y.lat


def test_different_seed_changes_the_realisation():
    a = SimulationEngine(Config(), seed=42).run(STEPS_PER_DAY)
    b = SimulationEngine(Config(), seed=43).run(STEPS_PER_DAY)
    assert (a[-1].do_level, a[-1].generation_kw) != (b[-1].do_level, b[-1].generation_kw)


def test_reset_restores_initial_state():
    engine = SimulationEngine(Config(), seed=42)
    engine.run(20)
    engine.reset()
    assert engine.history == []
    assert engine._step_idx == 0
    fresh = SimulationEngine(Config(), seed=42)
    assert fresh.step().do_level == pytest.approx(
        SimulationEngine(Config(), seed=42).step().do_level
    )


def test_engine_rejects_invalid_season_and_negative_coverage():
    with pytest.raises(ValueError, match="season must be one of"):
        SimulationEngine(Config(), season="winter")
    with pytest.raises(ValueError, match="coverage_pct must be non-negative"):
        SimulationEngine(Config(), coverage_pct=-1.0)


def test_reset_with_none_retains_current_parameters():
    engine = SimulationEngine(Config(), coverage_pct=20.0, season="monsoon", seed=7)
    engine.reset(coverage_pct=None, season=None, seed=None)
    assert engine.coverage_pct == 20.0
    assert engine.season == "monsoon"
    assert engine.seed == 7


# ── energy conservation ──────────────────────────────────────────────────

def test_energy_balance_closes_at_every_step(day_run):
    states, _ = day_run
    for s in states:
        assert s.solar_direct_use_kw + s.solar_curtailed_kw == pytest.approx(
            s.generation_kw, abs=0.15
        )
        assert s.solar_direct_use_kw + s.grid_import_kw == pytest.approx(
            s.total_demand_kw, abs=0.15
        )
        assert s.total_demand_kw == pytest.approx(
            s.municipal_load_kw + s.demand_kw + s.swapping_load_kw, abs=0.15
        )


def test_generation_never_exceeds_installed_capacity(day_run):
    states, engine = day_run
    ceiling = engine.config.max_generation_kw(engine.coverage_pct)
    assert all(s.generation_kw <= ceiling + 0.1 for s in states)


def test_no_generation_at_night(day_run):
    """The clear-sky profile is non-zero only from 06:00 to 18:00.

    The hourly profile is linearly interpolated between hours, so the dawn ramp
    begins at 05:00 and the dusk ramp ends at 19:00. Both boundaries are therefore
    half-hour ramp steps rather than abrupt transitions.
    """
    states, _ = day_run
    for s in states:
        if s.hour < 5.0 or s.hour >= 19.0:
            assert s.generation_kw == 0.0
    dawn = [s.generation_kw for s in states if s.hour == 5.5]
    assert dawn and dawn[0] > 0.0, "the interpolated profile ramps up through 05:30"


def test_overcast_reduces_but_does_not_eliminate_output(config):
    """100% cloud still delivers diffuse light, so output falls without vanishing."""
    from src.algorithms.solar import calc_generation

    clear = calc_generation(12.0, config.BASE_GENERATION_KW,
                            config.max_generation_kw(15.0), cloud_cover=0.0, temperature=32.5)
    overcast = calc_generation(12.0, config.BASE_GENERATION_KW,
                               config.max_generation_kw(15.0), cloud_cover=100.0,
                               temperature=32.5)
    assert 0.0 < overcast < clear


# ── the consumption models are one model ─────────────────────────────────

def test_traction_demand_equals_energy_the_fleet_actually_drew(day_run):
    """Guards the defect where an independent demand curve contradicted SOC drain."""
    states, engine = day_run
    reported = sum(s.demand_kw for s in states) * 0.5
    drawn = sum(b.energy_consumed_kwh for b in engine.get_fleet())
    assert reported == pytest.approx(drawn, rel=1e-4)


def test_daily_energy_profile_sums_to_stated_consumption(config):
    from src.algorithms.scheduler import traction_energy_profile

    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    assert sum(profile.values()) == pytest.approx(config.DAILY_CONSUMPTION_KWH)


def test_battery_capacity_changes_state_of_charge_drain(config):
    """The pack-capacity slider must not be cosmetic."""
    drained = {}
    for capacity in (150.0, 450.0):
        cfg = Config()
        cfg.BATTERY_CAPACITY_KWH = capacity
        states = SimulationEngine(cfg, seed=42).run(STEPS_PER_DAY)
        drained[capacity] = states[-1].avg_soc

    # Same energy demand, but a smaller pack loses more percentage per unit energy.
    assert drained[150.0] < drained[450.0]


# ── fleet and swap accounting ────────────────────────────────────────────

def test_state_of_charge_stays_within_physical_bounds(day_run):
    _, engine = day_run
    assert all(0.0 <= b.soc <= 100.0 for b in engine.get_fleet())


def test_no_bus_is_stranded_across_scenarios(config):
    for coverage, season in ((10.0, "summer"), (15.0, "monsoon"), (20.0, "summer")):
        cfg = Config()
        cfg.NUM_BUSES = 120
        engine = SimulationEngine(cfg, coverage_pct=coverage, season=season)
        engine.run(STEPS_PER_DAY)
        assert min(b.soc for b in engine.get_fleet()) > 5.0, (coverage, season)


def test_pack_inventory_is_conserved_at_every_hub(day_run):
    _, engine = day_run
    for station, state in zip(engine.config.SWAP_STATIONS, engine._stations_state.values(), strict=False):
        total = state["ready_packs"] + len(state["charging_packs"]) + len(state["charging_queue"])
        assert total == station["capacity_buses"]


def test_charger_bay_limit_is_respected(day_run):
    _, engine = day_run
    for state in engine._stations_state.values():
        assert state["last_active_bays"] <= engine.config.CHARGER_BAYS_PER_STATION


def test_route_allocation_is_proportional_and_sums_exactly(config):
    from src.algorithms.scheduler import allocate_fleet_by_route

    for size in (50, 120, 260, 400, 500):
        allocation = allocate_fleet_by_route(config.ROUTES, size)
        assert sum(allocation.values()) == size
        assert all(count >= 0 for count in allocation.values())
        # The design ordering must be preserved at every fleet size.
        assert allocation["Route 69"] >= allocation["Route 1"] >= allocation["Route 2"]


def test_fleet_matches_configured_allocation_at_default_size(engine):
    allocation = engine.coverage_capacity_map()
    for route_id, expected in allocation.items():
        actual = sum(1 for b in engine.get_fleet() if b.route_id == route_id)
        assert actual == expected


def test_diverting_bus_moves_toward_its_target(engine):
    """heading_to_swap must be a real movement, not a cosmetic label."""
    bus = engine.get_fleet()[0]
    bus.state = "heading_to_swap"
    bus.soc = 12.0
    station = engine.config.SWAP_STATIONS[0]
    bus.swap_station_lat, bus.swap_station_lon = station["lat"], station["lon"]

    target_index = bus.nearest_waypoint_index(station["lat"], station["lon"])
    target = bus.progress_of_waypoint(target_index)

    before = abs(target - bus.progress)
    step = 0.01 if target >= bus.progress else -0.01
    bus.divert_towards(target, step)
    assert abs(target - bus.progress) < before


# ── impact accounting ────────────────────────────────────────────────────

def test_co2_is_credited_only_against_self_consumed_energy(day_run):
    states, engine = day_run
    direct_kwh = sum(s.solar_direct_use_kw for s in states) * 0.5
    factor = engine.config.CO2_GRID_KG_PER_KWH

    assert states[-1].cumulative_co2_avoided_kg == pytest.approx(direct_kwh * factor, abs=1.0)
    generated_kwh = sum(s.generation_kw for s in states) * 0.5
    assert states[-1].cumulative_co2_avoided_kg < generated_kwh * factor


def test_tariff_saving_uses_the_same_basis_as_carbon(day_run):
    """Tariff savings must rest on self-consumed energy, not total generation."""
    states, engine = day_run
    direct_kwh = sum(s.solar_direct_use_kw for s in states) * 0.5
    rate = engine.config.GRID_TARIFF_PER_KWH + engine.config.OA_SURCHARGE_PER_KWH
    cumulative = states[-1].cumulative_cost_saved_inr
    assert cumulative == pytest.approx(direct_kwh * rate, rel=1e-4)
    generated_kwh = sum(s.generation_kw for s in states) * 0.5
    assert cumulative < generated_kwh * rate


def test_solar_funded_swaps_occur_only_with_generation_surplus(day_run):
    states, _ = day_run
    solar_steps = [s for s in states if s.solar_swaps_this_step > 0]
    assert solar_steps, "the array should fund at least some swaps on a clear summer day"
    for s in solar_steps:
        assert s.surplus_kw > 0.0


def test_swap_funding_tiers_sum_to_the_total(day_run):
    states, _ = day_run
    assert states[-1].solar_swaps + states[-1].grid_swaps == states[-1].total_swaps


# ── annual model ─────────────────────────────────────────────────────────

def test_annual_energy_balance_closes(engine):
    annual = engine.run_annual_simulation()
    assert annual["annual_direct_use_gwh"] + annual["annual_curtailed_gwh"] == pytest.approx(
        annual["annual_generation_gwh"], abs=0.06
    )
    assert annual["annual_direct_use_gwh"] + annual["annual_grid_import_gwh"] == pytest.approx(
        annual["annual_bus_demand_gwh"] + annual["annual_municipal_demand_gwh"], abs=0.10
    )


def test_annual_carbon_excludes_curtailed_energy(engine):
    annual = engine.run_annual_simulation()
    assert annual["annual_co2_tonnes"] < annual["annual_co2_tonnes_if_all_generation_credited"]


def test_annual_generation_is_plausible_for_the_installed_capacity(engine):
    annual = engine.run_annual_simulation()
    capacity_mwp = engine.config.capacity_mwp(15.0)
    # Between 1,300 and 2,000 kWh/kWp/yr is the normal band for Coimbatore.
    specific = annual["annual_generation_gwh"] * 1e6 / (capacity_mwp * 1000)
    assert 1300 <= specific <= 2000


def test_annual_model_covers_every_month_and_day(engine):
    annual = engine.run_annual_simulation()
    assert set(annual["monthly_generation_mwh"]) == set(range(1, 13))
    assert sum(annual["monthly_generation_mwh"].values()) == pytest.approx(
        annual["annual_generation_gwh"] * 1000, rel=0.01
    )


# ── dataframe and reporting ──────────────────────────────────────────────

def test_to_dataframe_exposes_the_physical_quantities(engine):
    engine.run(10)
    frame = engine.to_dataframe()
    assert len(frame) == 10
    for column in ("Generation (kW)", "Solar Direct Use (kW)", "Solar Curtailed (kW)",
                   "Grid Import (kW)", "Solar-Funded Swaps", "Grid-Assisted Swaps",
                   "DO Level (mg/L)", "CO₂ Avoided (kg)"):
        assert column in frame.columns


def test_daily_summary_is_consistent_with_history(engine):
    engine.run(STEPS_PER_DAY)
    summary = engine.daily_summary()
    assert summary["steps"] == STEPS_PER_DAY
    assert summary["generation_kwh"] == pytest.approx(
        sum(s.generation_kw for s in engine.history) * 0.5, abs=1.0
    )


def test_empty_history_yields_empty_frame(engine):
    assert engine.to_dataframe().empty
    assert engine.daily_summary() == {}


# ── map trajectory ───────────────────────────────────────────────────────

def test_trajectory_has_one_frame_per_step_and_does_not_touch_the_live_engine(engine):
    engine.run(5)
    live_soc = [b.soc for b in engine.get_fleet()]
    frames = build_frame_trajectory(engine, engine.config)

    assert len(frames) == STEPS_PER_DAY
    assert [b.soc for b in engine.get_fleet()] == live_soc, "live engine must be untouched"
    for frame in frames:
        assert len(frame["buses"]) == engine.config.NUM_BUSES


def test_map_html_renders_the_requested_step_without_an_animation_loop(engine):
    frames = build_frame_trajectory(engine, engine.config)
    html = build_interactive_map_html(engine.config, frames, initial_step=23,
                                      fleet_size=engine.config.NUM_BUSES)
    assert "render(23)" in html
    assert "setInterval" not in html, "the map must not run its own clock"
    assert html.lstrip().startswith("<!DOCTYPE html>")


def test_map_html_rejects_an_empty_trajectory(engine):
    with pytest.raises(ValueError):
        build_interactive_map_html(engine.config, [], 0)


def test_map_step_argument_is_clamped_into_range(engine):
    frames = build_frame_trajectory(engine, engine.config)
    for requested, expected in ((-5, 0), (0, 0), (999, STEPS_PER_DAY - 1)):
        assert f"render({expected})" in build_interactive_map_html(
            engine.config, frames, requested, engine.config.NUM_BUSES
        )
