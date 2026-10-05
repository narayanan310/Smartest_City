"""Tests for vehicle geometry, position interpolation and diversion."""

from __future__ import annotations

import pytest

from src.config.settings import Config
from src.models.bus import (
    Bus,
    build_route_geometry,
    haversine_km,
    interpolate_along,
)


@pytest.fixture()
def route(config: Config):
    return config.ROUTES["Route 69"]["waypoints"]


# ── geodesy ──────────────────────────────────────────────────────────────

def test_haversine_is_zero_for_identical_points():
    assert haversine_km(11.0, 76.9, 11.0, 76.9) == pytest.approx(0.0, abs=1e-9)


def test_haversine_matches_a_known_distance():
    # Coimbatore to New Delhi is about 1,960 km great-circle.
    assert haversine_km(11.0168, 76.9558, 28.6139, 77.2090) == pytest.approx(1960, abs=40)


def test_haversine_is_symmetric():
    assert haversine_km(11.0, 76.9, 10.9, 77.0) == pytest.approx(
        haversine_km(10.9, 77.0, 11.0, 76.9)
    )


# ── geometry construction ────────────────────────────────────────────────

def test_route_geometry_sums_to_the_total_length(route):
    segments, cumulative, total = build_route_geometry(route)
    assert len(segments) == len(route) - 1
    assert len(cumulative) == len(route)
    assert cumulative[0] == 0.0
    assert total == pytest.approx(sum(segments))
    assert cumulative[-1] == pytest.approx(total)


def test_route_geometry_handles_degenerate_input():
    assert build_route_geometry([]) == ([], [0.0], 0.0)
    assert build_route_geometry([(11.0, 76.9)]) == ([], [0.0], 0.0)
    assert build_route_geometry([(11.0, 76.9)] * 3)[2] == pytest.approx(0.0)


# ── interpolation ────────────────────────────────────────────────────────

def test_interpolation_returns_the_endpoints_at_the_extremes(route):
    _, cumulative, total = build_route_geometry(route)
    assert interpolate_along(route, cumulative, total, 0.0) == route[0]
    assert interpolate_along(route, cumulative, total, 1.0) == route[-1]
    assert interpolate_along(route, cumulative, total, -0.5) == route[0]
    assert interpolate_along(route, cumulative, total, 2.0) == route[-1]


def test_interpolation_lands_on_waypoints_at_their_own_progress(route):
    _, cumulative, total = build_route_geometry(route)
    for index in (0, 5, 20, 40, len(route) - 1):
        progress = cumulative[index] / total
        lat, lon = interpolate_along(route, cumulative, total, progress)
        assert lat == pytest.approx(route[index][0], abs=1e-4)
        assert lon == pytest.approx(route[index][1], abs=1e-4)


def test_interpolation_is_continuous_along_the_route(route):
    """No step along the route may teleport across the map."""
    _, cumulative, total = build_route_geometry(route)
    longest_segment = max(
        haversine_km(route[i][0], route[i][1], route[i + 1][0], route[i + 1][1])
        for i in range(len(route) - 1)
    )
    previous = interpolate_along(route, cumulative, total, 0.0)
    for step in range(1, 201):
        current = interpolate_along(route, cumulative, total, step / 200)
        hop = haversine_km(previous[0], previous[1], current[0], current[1])
        assert hop <= longest_segment + 1e-6, "a jump larger than one segment is a teleport"
        previous = current
    assert previous == pytest.approx(route[-1])


def test_interpolation_of_a_zero_length_route_is_safe():
    points = [(11.0, 76.9)] * 4
    assert interpolate_along(points, [0.0, 0.0, 0.0, 0.0], 0.0, 0.5) == points[0]


def test_empty_route_falls_back_to_a_usable_coordinate():
    assert len(interpolate_along([], [0.0], 0.0, 0.5)) == 2


# ── caching ──────────────────────────────────────────────────────────────

def test_route_length_is_computed_once_and_cached(config):
    bus = Bus(1, "Route 69", config.ROUTES["Route 69"]["waypoints"])
    assert bus._geometry_waypoint_count == -1, "geometry must start uncached"
    first = bus.route_length_km
    assert bus._geometry_waypoint_count == len(bus.waypoints)
    # A second read must not rebuild; the sentinel stays at the waypoint count.
    assert bus.route_length_km == first


def test_geometry_rebuilds_if_the_route_changes():
    bus = Bus(1, "Route 69", [(11.0, 76.9), (11.1, 77.0)])
    short = bus.route_length_km
    bus.waypoints = [*bus.waypoints, (11.2, 77.1)]
    assert bus.route_length_km > short


def test_legacy_bus_without_geometry_fields_still_works():
    """Streamlit can restore an instance created before the cache fields existed.

    Unpickling such an object leaves the new keys absent from the instance
    dictionary. Fields with a plain class default fall back to that default, and
    fields declared with ``default_factory`` have no class attribute at all, so the
    geometry accessors must rebuild rather than raise.
    """
    bus = Bus(1, "Route 69", [(11.0, 76.9), (11.1, 77.0), (11.2, 77.1)])
    for key in ("_segment_lengths", "_cumulative_lengths", "_route_length_cache",
                "_geometry_waypoint_count"):
        bus.__dict__.pop(key, None)

    assert bus.route_length_km > 0.0
    assert isinstance(bus.lat, float) and isinstance(bus.lon, float)
    assert 0.0 <= bus.progress_of_waypoint(1) <= 1.0
    assert isinstance(bus.nearest_waypoint_index(11.1, 77.0), int)


# ── positions and helpers ────────────────────────────────────────────────

def test_position_of_a_swapping_bus_is_the_station(config):
    station = config.SWAP_STATIONS[0]
    bus = Bus(1, "Route 69", config.ROUTES["Route 69"]["waypoints"], state="swapping",
              swap_station_lat=station["lat"], swap_station_lon=station["lon"])
    assert (bus.lat, bus.lon) == (station["lat"], station["lon"])


def test_waypoint_progress_lookup_is_bounded(config):
    bus = Bus(1, "Route 69", config.ROUTES["Route 69"]["waypoints"])
    assert bus.progress_of_waypoint(0) == pytest.approx(0.0)
    assert bus.progress_of_waypoint(10_000) == pytest.approx(1.0)
    assert bus.progress_of_waypoint(-5) == pytest.approx(0.0)
    values = [bus.progress_of_waypoint(i) for i in range(len(bus.waypoints))]
    assert all(a <= b for a, b in zip(values, values[1:], strict=False))


def test_nearest_waypoint_index_finds_an_endpoint(config):
    bus = Bus(1, "Route 69", config.ROUTES["Route 69"]["waypoints"])
    first = bus.waypoints[0]
    last = bus.waypoints[-1]
    assert bus.nearest_waypoint_index(first[0], first[1]) == 0
    assert bus.nearest_waypoint_index(last[0], last[1]) == len(bus.waypoints) - 1


def test_nearest_station_and_radius(config):
    bus = Bus(1, "Route 69", config.ROUTES["Route 69"]["waypoints"], progress=0.0)
    station, distance = bus.nearest_station(config.SWAP_STATIONS)
    assert station["name"] in {s["name"] for s in config.SWAP_STATIONS}
    assert distance >= 0.0
    assert bus.near_swap_station(config.SWAP_STATIONS, radius_km=1e6)
    assert not bus.near_swap_station([], radius_km=1.0)


def test_battery_energy_scales_with_state_of_charge(config):
    bus = Bus(1, "Route 69", config.ROUTES["Route 69"]["waypoints"], soc=50.0)
    assert bus.battery_energy_kwh(300.0) == pytest.approx(150.0)


def test_state_of_charge_colour_bands(config):
    route = config.ROUTES["Route 69"]["waypoints"]
    colours = {soc: Bus(1, "Route 69", route, soc=soc).soc_color
               for soc in (95.0, 60.0, 30.0, 15.0)}
    assert len(set(colours.values())) == 4, "each band must be visually distinct"


def test_condition_reflects_state_and_charge(config):
    route = config.ROUTES["Route 69"]["waypoints"]
    def condition(soc, state="running"):
        return Bus(1, "Route 69", route, soc=soc, state=state).condition

    assert condition(100.0) == "In service"
    assert condition(40.0) == "Monitor SOC"
    assert condition(25.0) == "Swap priority"
    assert condition(10.0) == "Critical SOC"
    assert condition(90.0, "swapping") == "Charging / battery swap"
    assert condition(90.0, "depot") == "At depot"
    assert condition(12.0, "heading_to_swap") == "Diverting to swap station"


# ── diversion ────────────────────────────────────────────────────────────

def test_divert_towards_closes_the_gap_without_overshooting():
    bus = Bus(1, "R", [(11.0 + i * 0.01, 76.9) for i in range(10)], progress=0.2)
    bus.divert_towards(0.8, 0.1)
    assert bus.progress == pytest.approx(0.3)
    bus.divert_towards(0.8, 0.9)
    assert bus.progress == pytest.approx(0.8), "must land exactly on the target"


def test_divert_towards_works_in_both_directions():
    bus = Bus(1, "R", [(11.0 + i * 0.01, 76.9) for i in range(10)], progress=0.8)
    bus.divert_towards(0.2, -0.1)
    assert bus.progress == pytest.approx(0.7)


def test_divert_towards_clamps_the_target_into_range():
    bus = Bus(1, "R", [(11.0, 76.9), (11.1, 77.0)], progress=0.5)
    bus.divert_towards(5.0, 1.0)
    assert bus.progress == pytest.approx(1.0)


def test_divert_is_a_no_op_when_already_at_the_target():
    bus = Bus(1, "R", [(11.0, 76.9), (11.1, 77.0)], progress=0.5)
    assert bus.divert_towards(0.5, 0.2) == pytest.approx(0.0)
    assert bus.progress == pytest.approx(0.5)


# ── named stops ──────────────────────────────────────────────────────────

def test_named_stops_advance_with_direction(config):
    route = config.ROUTES["Route 69"]
    bus = Bus(1, "Route 69", route["waypoints"], stop_names=route["stop_names"],
              stop_waypoint_indices=route["stop_waypoint_indices"], progress=0.0,
              direction=1)
    first = bus.current_stop
    bus.progress = 0.99
    last_forward = bus.current_stop
    bus.direction = -1
    assert bus.current_stop != first or last_forward != first
    assert bus.next_stop in route["stop_names"]


def test_bus_without_named_stops_still_reports_a_stop(config):
    bus = Bus(1, "Route 69", config.ROUTES["Route 69"]["waypoints"])
    assert bus.current_stop.startswith("Stop")
    assert bus.next_stop.startswith("Stop")


def test_stops_are_clamped_at_the_route_ends(config):
    route = config.ROUTES["Route 69"]
    bus = Bus(1, "Route 69", route["waypoints"], stop_names=route["stop_names"],
              stop_waypoint_indices=route["stop_waypoint_indices"], progress=0.0,
              direction=-1)
    assert bus.next_stop in route["stop_names"]
    bus.direction = 1
    bus.progress = 1.0
    assert bus.current_stop in route["stop_names"]
