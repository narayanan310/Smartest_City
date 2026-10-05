"""Tests for the fleet scheduler, energy profile and swap logic."""

from __future__ import annotations

import pytest

from src.algorithms.scheduler import (
    CRITICAL_SOC_PCT,
    allocate_fleet_by_route,
    bus_step_energy_kwh,
    create_fleet,
    is_service_hour,
    optimize_swaps,
    process_station_charging,
    required_pack_energy_kwh,
    traction_energy_profile,
    update_fleet,
    vehicle_efficiency_factor,
    vehicle_speed_kmph,
)

from .conftest import make_bus, make_station_state

# ── fleet construction ───────────────────────────────────────────────────

def test_create_fleet_assigns_every_bus(config):
    fleet = create_fleet(config.NUM_BUSES, config.ROUTES, seed=42)
    assert len(fleet) == config.NUM_BUSES
    assert [b.bus_id for b in fleet] == list(range(1, config.NUM_BUSES + 1))


def test_create_fleet_respects_route_allocation_at_any_size(config):
    for size in (37, 120, 260, 413):
        fleet = create_fleet(size, config.ROUTES, seed=1)
        allocation = allocate_fleet_by_route(config.ROUTES, size)
        for route_id, count in allocation.items():
            assert sum(1 for b in fleet if b.route_id == route_id) == count


def test_create_fleet_initial_state_is_within_bounds(config):
    fleet = create_fleet(config.NUM_BUSES, config.ROUTES, seed=7)
    assert all(config.SOC_INITIAL_MIN <= b.soc <= config.SOC_INITIAL_MAX for b in fleet)
    assert all(0.0 <= b.progress <= 1.0 for b in fleet)
    assert all(b.direction in (-1, 1) for b in fleet)


def test_allocate_fleet_handles_degenerate_input():
    assert allocate_fleet_by_route({}, 10) == {}
    assert sum(allocate_fleet_by_route({"A": {"buses_allocated": 5}}, 0).values()) == 0
    # A route table with no declared weights must still divide the fleet evenly.
    allocation = allocate_fleet_by_route({"A": {}, "B": {}}, 10)
    assert sum(allocation.values()) == 10


# ── energy profile ───────────────────────────────────────────────────────

def test_energy_profile_is_zero_outside_the_service_window(config):
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    assert min(profile) == int(config.SERVICE_START_HOUR)
    assert max(profile) == int(config.SERVICE_END_HOUR) - 1
    assert profile.get(3) is None
    assert profile.get(23) is None


def test_peak_hours_draw_more_energy_than_off_peak(config):
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    peak = profile[config.PEAK_HOURS[0]]
    off_peak = profile[12]
    assert peak > off_peak


def test_service_hour_boundaries(config):
    assert is_service_hour(config.SERVICE_START_HOUR, config.SERVICE_START_HOUR,
                           config.SERVICE_END_HOUR)
    assert not is_service_hour(config.SERVICE_END_HOUR, config.SERVICE_START_HOUR,
                               config.SERVICE_END_HOUR)
    assert not is_service_hour(config.SERVICE_START_HOUR - 1, config.SERVICE_START_HOUR,
                               config.SERVICE_END_HOUR)


def test_per_vehicle_factors_are_deterministic_and_bounded(config):
    assert vehicle_efficiency_factor(3) == vehicle_efficiency_factor(3)
    assert 0.9 <= min(vehicle_efficiency_factor(i) for i in range(1, 260)) <= 1.1
    speeds = [vehicle_speed_kmph(i, 1.0) for i in range(1, 260)]
    assert min(speeds) >= 16.0 and max(speeds) <= 21.0
    # Congestion must not increase speed.
    assert vehicle_speed_kmph(3, 1.5) < vehicle_speed_kmph(3, 1.0)


def test_bus_at_depot_or_mid_swap_draws_no_energy(config):
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    for state in ("depot", "swapping"):
        bus = make_bus(config, state=state)
        assert bus_step_energy_kwh(bus, 8.0, profile, 0.5) == 0.0
    running = make_bus(config, state="running")
    assert bus_step_energy_kwh(running, 8.0, profile, 0.5) > 0.0
    # Outside the window a running bus still draws nothing.
    assert bus_step_energy_kwh(running, 3.0, profile, 0.5) == 0.0


def test_required_pack_energy_is_the_refill_not_the_whole_pack():
    assert required_pack_energy_kwh(0.0, 300.0) == pytest.approx(300.0)
    assert required_pack_energy_kwh(20.0, 300.0) == pytest.approx(240.0)
    assert required_pack_energy_kwh(100.0, 300.0) == 0.0


# ── station charging ─────────────────────────────────────────────────────

def test_charging_respects_the_bay_limit_and_records_losses():
    state = {"Hub": {"ready_packs": 0, "charging_packs": [], "charging_queue": [300.0] * 3}}
    load = process_station_charging(state, 0.5, 150.0, charging_efficiency=0.9,
                                    max_active_bays=2)
    assert load > 0
    assert len(state["Hub"]["charging_packs"]) == 2
    assert len(state["Hub"]["charging_queue"]) == 1
    assert state["Hub"]["last_charge_loss_kwh"] > 0


def test_charging_is_capped_by_rate_and_efficiency():
    state = {"Hub": {"ready_packs": 0, "charging_packs": [200.0, 50.0], "charging_queue": []}}
    # 0.5 h at 150 kW = 75 kWh input per bay, so the 200 kWh pack cannot finish.
    load = process_station_charging(state, step_hours=0.5, max_charge_rate_kw=150.0)
    assert load == pytest.approx(250.0)
    assert state["Hub"]["ready_packs"] == 1
    assert state["Hub"]["charging_packs"] == pytest.approx([125.0])


def test_charging_recovers_from_an_overfull_persisted_state():
    state = {"Hub": {"ready_packs": 0, "charging_packs": [100.0] * 5, "charging_queue": []}}
    process_station_charging(state, 0.5, 150.0, max_active_bays=2)
    assert len(state["Hub"]["charging_packs"]) == 2
    assert len(state["Hub"]["charging_queue"]) == 3


def test_pack_inventory_is_conserved_through_a_charge_cycle():
    state = {"Hub": {"ready_packs": 0, "charging_packs": [], "charging_queue": [120.0]}}
    total = lambda: (state["Hub"]["ready_packs"] + len(state["Hub"]["charging_packs"])
                     + len(state["Hub"]["charging_queue"]))
    assert total() == 1
    for _ in range(6):
        process_station_charging(state, 0.5, 150.0, charging_efficiency=0.92,
                                 max_active_bays=1)
        assert total() == 1, "a pack must exist in exactly one place at a time"
    assert state["Hub"]["ready_packs"] == 1
    assert state["Hub"]["charging_packs"] == []


# ── swap scheduling ──────────────────────────────────────────────────────

def test_swap_consumes_a_ready_pack_and_queues_the_depleted_one(config):
    bus = make_bus(config, soc=20.0)
    station = config.SWAP_STATIONS[0]
    bus.swap_station_lat, bus.swap_station_lon = station["lat"], station["lon"]
    state = make_station_state(config, ready=1)

    decision = optimize_swaps([bus], [station], state, 300.0, 30.0, 2.0)

    assert decision.approved == [bus]
    assert state[station["name"]]["ready_packs"] == 0
    assert state[station["name"]]["charging_queue"] == pytest.approx([240.0])
    assert bus.state == "swapping"
    assert bus.total_swaps == 1


def test_swap_is_refused_when_no_pack_is_ready(config):
    bus = make_bus(config, soc=20.0)
    station = config.SWAP_STATIONS[0]
    bus.swap_station_lat, bus.swap_station_lon = station["lat"], station["lon"]
    state = make_station_state(config, ready=0)

    decision = optimize_swaps([bus], [station], state, 300.0, 30.0, 2.0)
    assert decision.approved == []
    assert state[station["name"]]["ready_packs"] == 0


def test_swap_requires_the_bus_to_be_inside_the_service_radius(config):
    bus = make_bus(config, soc=10.0, progress=0.5)
    station = config.SWAP_STATIONS[0]
    state = make_station_state(config, ready=5)

    decision = optimize_swaps([bus], [station], state, 300.0, 30.0, 0.001)
    assert decision.approved == []
    assert decision.diverting == 1
    assert bus.state == "heading_to_swap"
    # The bus must remember which hub it is heading for, including its coordinates.
    assert bus.swap_station_name == station["name"]
    assert bus.swap_station_lat == station["lat"]


def test_surplus_budget_limits_how_many_swaps_the_array_can_fund(config):
    station = config.SWAP_STATIONS[0]
    buses = []
    for i in range(4):
        bus = make_bus(config, bus_id=i + 1, soc=20.0)
        bus.swap_station_lat, bus.swap_station_lon = station["lat"], station["lon"]
        buses.append(bus)
    state = make_station_state(config, ready=4)

    # Enough surplus for roughly one refill (240 kWh pack / 0.92 efficiency).
    decision = optimize_swaps(buses, [station], state, 300.0, 30.0, 2.0,
                              solar_surplus_kwh=270.0)

    assert len(decision.solar_swaps) == 1
    assert decision.grid_swaps == []
    assert decision.deferred == 3, "unfunded buses are retried, not discarded"


def test_a_critical_bus_is_never_stranded_by_an_empty_surplus(config):
    station = config.SWAP_STATIONS[0]
    critical = make_bus(config, bus_id=1, soc=5.0)
    marginal = make_bus(config, bus_id=2, soc=25.0)
    for bus in (critical, marginal):
        bus.swap_station_lat, bus.swap_station_lon = station["lat"], station["lon"]
    state = make_station_state(config, ready=2)

    decision = optimize_swaps([critical, marginal], [station], state, 300.0, 30.0, 2.0,
                              solar_surplus_kwh=0.0)

    assert critical in decision.grid_swaps, "a critical bus must be topped up regardless"
    assert marginal not in decision.approved, "a merely low bus may wait for surplus"
    assert critical.soc < CRITICAL_SOC_PCT


def test_unlimited_surplus_funds_every_candidate(config):
    station = config.SWAP_STATIONS[0]
    buses = []
    for i in range(3):
        bus = make_bus(config, bus_id=i + 1, soc=15.0)
        bus.swap_station_lat, bus.swap_station_lon = station["lat"], station["lon"]
        buses.append(bus)
    state = make_station_state(config, ready=3)

    decision = optimize_swaps(buses, [station], state, 300.0, 30.0, 2.0)
    assert len(decision.approved) == 3
    assert len(decision.solar_swaps) == 3


def test_candidates_are_served_lowest_state_of_charge_first(config):
    station = config.SWAP_STATIONS[0]
    buses = []
    for bus_id, soc in ((1, 28.0), (2, 12.0), (3, 21.0)):
        bus = make_bus(config, bus_id=bus_id, soc=soc)
        bus.swap_station_lat, bus.swap_station_lon = station["lat"], station["lon"]
        buses.append(bus)
    state = make_station_state(config, ready=1)

    decision = optimize_swaps(buses, [station], state, 300.0, 30.0, 2.0)
    assert [b.bus_id for b in decision.approved] == [2], "bus 2 has the lowest charge"


def test_bus_above_the_threshold_is_never_a_candidate(config):
    station = config.SWAP_STATIONS[0]
    bus = make_bus(config, soc=45.0)
    bus.swap_station_lat, bus.swap_station_lon = station["lat"], station["lon"]
    state = make_station_state(config, ready=5)
    assert optimize_swaps([bus], [station], state, 300.0, 30.0, 2.0).approved == []


def test_no_stations_is_handled(config):
    assert optimize_swaps([make_bus(config)], [], {}, 300.0, 30.0, 2.0).approved == []


# ── fleet stepping ───────────────────────────────────────────────────────

def test_update_fleet_reports_the_power_it_applied(config):
    fleet = create_fleet(40, config.ROUTES, seed=3)
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    result = update_fleet(
        fleet, 8.0, config.DAILY_CONSUMPTION_KWH, profile, config.BATTERY_CAPACITY_KWH,
        config.SOC_SWAP_THRESHOLD, config.SWAP_STATIONS, config.SERVICE_START_HOUR,
        config.SERVICE_END_HOUR, config.SWAP_STATION_RADIUS_KM, step_hours=0.5,
    )
    drawn = sum(b.energy_consumed_kwh for b in fleet)
    # traction_kw is reported rounded to 0.01 kW by design.
    assert result.traction_kw == pytest.approx(drawn / 0.5, abs=0.01)
    assert result.active_buses == 40


def test_a_completed_swap_refills_the_pack_on_the_next_step(config):
    bus = make_bus(config, soc=18.0, state="swapping")
    fleet = [bus]
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    update_fleet(fleet, 8.0, config.DAILY_CONSUMPTION_KWH, profile,
                 config.BATTERY_CAPACITY_KWH, config.SOC_SWAP_THRESHOLD,
                 config.SWAP_STATIONS, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
                 config.SWAP_STATION_RADIUS_KM, step_hours=0.5)
    assert bus.soc == 100.0
    assert bus.state == "running"
    assert bus.swap_energy_source is None


def test_fleet_returns_to_depot_outside_the_service_window(config):
    fleet = create_fleet(10, config.ROUTES, seed=3)
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    result = update_fleet(fleet, 23.0, config.DAILY_CONSUMPTION_KWH, profile,
                          config.BATTERY_CAPACITY_KWH, config.SOC_SWAP_THRESHOLD,
                          config.SWAP_STATIONS, config.SERVICE_START_HOUR,
                          config.SERVICE_END_HOUR, config.SWAP_STATION_RADIUS_KM,
                          step_hours=0.5)
    assert result.traction_kw == 0.0
    assert all(b.state == "depot" for b in fleet)


def test_bus_progress_stays_inside_the_route(config):
    fleet = create_fleet(30, config.ROUTES, seed=11)
    profile = traction_energy_profile(
        config.DAILY_CONSUMPTION_KWH, config.SERVICE_START_HOUR, config.SERVICE_END_HOUR,
        config.PEAK_HOURS, config.PEAK_FACTOR, config.OFF_PEAK_FACTOR,
    )
    for step in range(60):
        update_fleet(fleet, 6.0 + (step % 30) * 0.5, config.DAILY_CONSUMPTION_KWH, profile,
                     config.BATTERY_CAPACITY_KWH, config.SOC_SWAP_THRESHOLD,
                     config.SWAP_STATIONS, config.SERVICE_START_HOUR,
                     config.SERVICE_END_HOUR, config.SWAP_STATION_RADIUS_KM,
                     step_hours=0.5)
    assert all(0.0 <= b.progress <= 1.0 for b in fleet)
