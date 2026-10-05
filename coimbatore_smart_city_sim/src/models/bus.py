"""
bus.py — Vehicle geometry, route position, and state-of-charge model.

Design notes
------------
Route geometry (segment lengths and cumulative distances) is computed **once**
per bus and cached on the instance. The previous implementation exposed
``route_length_km``, ``lat`` and ``lon`` as plain ``@property`` methods that
re-walked the whole waypoint list on every access. Because the simulation engine
touches those properties once per bus per 30-minute step, and the map trajectory
builder touches ``lat``/``lon`` twice per bus per step, that produced roughly two
million redundant haversine evaluations per simulated day.

The cached geometry is created lazily via :meth:`Bus._ensure_geometry` rather than
in ``__post_init__`` so that ``Bus`` instances restored from an older pickled /
session-state payload (which predate these fields) transparently recompute instead
of raising ``AttributeError``.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from src.config.settings import (
    BUS_STATE_LABELS,
    SOC_COLOUR_HIGH_PCT,
    SOC_COLOUR_LOW_PCT,
    SOC_COLOUR_MID_PCT,
    SOC_COLOUR_SCALE,
    SOC_CRITICAL_PCT,
    SOC_MONITOR_PCT,
    SOC_STATUS_LABELS,
    SOC_SWAP_PCT,
    Config,
)

EARTH_RADIUS_KM = 6371.0

# Used when a route has no usable geometry, so callers always receive a coordinate.
# Anchored on Config so there is one copy of the site location.
FALLBACK_COORD: Tuple[float, float] = (Config.LAKE_LAT, Config.LAKE_LON)

# Minimum plausible state of charge. Buses are modelled as never being allowed to
# reach a hard cutoff; the swap network is expected to intervene first.
SOC_HARD_FLOOR_PCT = 5.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two ``(lat, lon)`` points, in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# Backwards-compatible private alias.
_haversine = haversine_km


def soc_status_label(soc: float) -> str:
    """Classify a state of charge into one of :data:`SOC_STATUS_LABELS`.

    The band boundaries come from :mod:`src.config.settings`, so this function is the
    only place the status ladder is defined. :attr:`Bus.condition` and the map's
    defensive fallback both call it, which is what keeps the dashboard table, the map
    popups and the map legend from drifting apart.
    """
    if soc < SOC_CRITICAL_PCT:
        return SOC_STATUS_LABELS[0]
    if soc < SOC_SWAP_PCT:
        return SOC_STATUS_LABELS[1]
    if soc < SOC_MONITOR_PCT:
        return SOC_STATUS_LABELS[2]
    return SOC_STATUS_LABELS[3]


def soc_colour(soc: float) -> str:
    """Marker colour for a state of charge, from :data:`SOC_COLOUR_SCALE`.

    The ramp is intentionally offset from the status ladder: a bus at 40% is
    "Monitor SOC" but still amber, and only drops to orange below 40%.
    """
    if soc < SOC_COLOUR_LOW_PCT:
        return SOC_COLOUR_SCALE[0]
    if soc < SOC_COLOUR_MID_PCT:
        return SOC_COLOUR_SCALE[1]
    if soc < SOC_COLOUR_HIGH_PCT:
        return SOC_COLOUR_SCALE[2]
    return SOC_COLOUR_SCALE[3]


def build_route_geometry(
    waypoints: Sequence[Tuple[float, float]],
) -> Tuple[List[float], List[float], float]:
    """Pre-compute route geometry.

    Returns ``(segment_lengths, cumulative_distances, total_length_km)`` where
    ``cumulative_distances[i]`` is the distance from the start of the route to
    ``waypoints[i]``. For a degenerate route the cumulative list is ``[0.0]`` and
    the total length is ``0.0``.
    """
    if not waypoints:
        return [], [0.0], 0.0
    if len(waypoints) == 1:
        return [], [0.0], 0.0

    segment_lengths = [
        haversine_km(waypoints[i][0], waypoints[i][1], waypoints[i + 1][0], waypoints[i + 1][1])
        for i in range(len(waypoints) - 1)
    ]
    cumulative = [0.0]
    for seg in segment_lengths:
        cumulative.append(cumulative[-1] + seg)
    return segment_lengths, cumulative, cumulative[-1]


def interpolate_along(
    waypoints: Sequence[Tuple[float, float]],
    cumulative: Sequence[float],
    total_length_km: float,
    progress: float,
) -> Tuple[float, float]:
    """Return the ``(lat, lon)`` at ``progress`` (0.0–1.0) along a route polyline.

    Distances are measured along the polyline rather than by counting waypoints,
    so buses move at a constant speed regardless of how the waypoints are spaced.
    """
    if not waypoints:
        return FALLBACK_COORD
    if len(waypoints) == 1 or progress <= 0.0:
        return waypoints[0]
    if progress >= 1.0 or total_length_km <= 0.0:
        return waypoints[-1]

    target = progress * total_length_km
    # bisect_right finds the segment whose end is at or past the target distance.
    seg_index = bisect_right(cumulative, target) - 1
    seg_index = max(0, min(seg_index, len(waypoints) - 2))

    seg_start = cumulative[seg_index]
    seg_length = cumulative[seg_index + 1] - seg_start
    ratio = (target - seg_start) / seg_length if seg_length > 0.0 else 0.0
    ratio = min(1.0, max(0.0, ratio))

    lat = waypoints[seg_index][0] + ratio * (waypoints[seg_index + 1][0] - waypoints[seg_index][0])
    lon = waypoints[seg_index][1] + ratio * (waypoints[seg_index + 1][1] - waypoints[seg_index][1])
    return (round(lat, 5), round(lon, 5))


def _interpolate_position(
    waypoints: List[Tuple[float, float]], progress: float
) -> Tuple[float, float]:
    """Backwards-compatible helper: interpolate using freshly built geometry."""
    _, cumulative, total = build_route_geometry(waypoints)
    return interpolate_along(waypoints, cumulative, total, progress)


def _route_length_km(waypoints: List[Tuple[float, float]]) -> float:
    """Backwards-compatible helper: total polyline length in kilometres."""
    return build_route_geometry(waypoints)[2]


@dataclass
class Bus:
    """A single electric bus operating on one corridor."""

    bus_id: int
    route_id: str
    waypoints: List[Tuple[float, float]]
    stop_names: List[str] = field(default_factory=list)
    stop_waypoint_indices: List[int] = field(default_factory=list)
    soc: float = 100.0
    state: str = "depot"
    progress: float = 0.0
    direction: int = 1

    energy_consumed_kwh: float = 0.0
    km_travelled: float = 0.0
    total_swaps: int = 0
    swap_remaining_min: int = 0

    swap_station_name: Optional[str] = None
    swap_station_lat: Optional[float] = None
    swap_station_lon: Optional[float] = None

    #: "solar" when the swap was funded by a generation surplus this step, or
    #: "grid" when it fell back to an off-peak grid top-up. ``None`` when the bus
    #: was not swapped this step.
    swap_energy_source: Optional[str] = None

    #: Route-progress value the bus is diverting towards while ``heading_to_swap``.
    diversion_target_progress: Optional[float] = None

    # ── cached geometry (populated lazily by _ensure_geometry) ──────────────
    _segment_lengths: List[float] = field(default_factory=list, repr=False, compare=False)
    _cumulative_lengths: List[float] = field(default_factory=list, repr=False, compare=False)
    _route_length_cache: float = field(default=0.0, repr=False, compare=False)
    _geometry_waypoint_count: int = field(default=-1, repr=False, compare=False)

    def _ensure_geometry(self) -> None:
        """Build and cache route geometry if it is missing or stale.

        The staleness check compares the cached waypoint count so that a mutated
        ``waypoints`` list (never done by the current engine, but cheap to guard)
        cannot leave a bus with geometry that no longer matches its route.

        The count is read with ``getattr`` because Streamlit keeps engine and bus
        objects in session state across code reloads, so an instance may have been
        created before these fields existed and a direct attribute access would raise
        ``AttributeError`` instead of rebuilding.
        """
        if getattr(self, "_geometry_waypoint_count", -1) == len(self.waypoints):
            return
        segment_lengths, cumulative, total = build_route_geometry(self.waypoints)
        self._segment_lengths = segment_lengths
        self._cumulative_lengths = cumulative
        self._route_length_cache = total
        self._geometry_waypoint_count = len(self.waypoints)

    # ── geometry accessors ──────────────────────────────────────────────────

    @property
    def route_length_km(self) -> float:
        """Total length of the corridor polyline, in kilometres (cached)."""
        self._ensure_geometry()
        return getattr(self, "_route_length_cache", 0.0)

    @property
    def current_stop_index(self) -> int:
        """Nearest passed route waypoint, used as a repeatable virtual stop."""
        if len(self.waypoints) < 2:
            return 0
        return min(len(self.waypoints) - 1, int(self.progress * (len(self.waypoints) - 1)))

    def _anchor_index(self) -> int:
        """Index into ``stop_names`` of the stop the bus is currently closest to."""
        if not self.stop_names:
            return 0
        waypoint = self.current_stop_index
        if self.stop_waypoint_indices:
            anchors = self.stop_waypoint_indices
        else:
            last = max(1, len(self.stop_names) - 1)
            anchors = [
                round(i * (len(self.waypoints) - 1) / last) for i in range(len(self.stop_names))
            ]
        return min(range(len(anchors)), key=lambda i: abs(anchors[i] - waypoint))

    @property
    def current_stop(self) -> str:
        if not self.stop_names:
            return f"Stop {self.current_stop_index + 1}"
        return self.stop_names[self._anchor_index()]

    @property
    def next_stop(self) -> str:
        if not self.stop_names:
            if not self.waypoints:
                return "Route endpoint"
            nxt = min(len(self.waypoints) - 1, self.current_stop_index + self.direction)
            return f"Stop {nxt + 1}"
        nxt = max(0, min(len(self.stop_names) - 1, self._anchor_index() + self.direction))
        return self.stop_names[nxt]

    @property
    def condition(self) -> str:
        """Human-readable operational condition for the dashboard and map."""
        if self.state == "swapping":
            return BUS_STATE_LABELS[0]
        if self.state == "depot":
            return BUS_STATE_LABELS[1]
        if self.state == "heading_to_swap":
            return BUS_STATE_LABELS[2]
        return soc_status_label(self.soc)

    def _position(self) -> Tuple[float, float]:
        if self.state == "swapping" and self.swap_station_lat is not None and self.swap_station_lon is not None:
            return (self.swap_station_lat, self.swap_station_lon)
        self._ensure_geometry()
        return interpolate_along(
            self.waypoints,
            getattr(self, "_cumulative_lengths", []),
            getattr(self, "_route_length_cache", 0.0),
            self.progress,
        )

    @property
    def lat(self) -> float:
        """Latitude of the bus at its current route progress."""
        return self._position()[0]

    @property
    def lon(self) -> float:
        """Longitude of the bus at its current route progress."""
        return self._position()[1]

    @property
    def soc_color(self) -> str:
        """Return hex colour corresponding to state of charge."""
        return soc_colour(self.soc)

    def battery_energy_kwh(self, capacity_kwh: float) -> float:
        """Energy currently stored in the pack, in kWh."""
        return self.soc / 100.0 * capacity_kwh

    # ── station helpers ─────────────────────────────────────────────────────

    def nearest_station(self, stations: list) -> Tuple[dict, float]:
        """Return the nearest swap station and the straight-line distance to it."""
        lat, lon = self._position()
        min_dist = float("inf")
        best_station = stations[0] if stations else {}
        for station in stations:
            dist = haversine_km(lat, lon, station["lat"], station["lon"])
            if dist < min_dist:
                min_dist = dist
                best_station = station
        return best_station, min_dist

    def near_swap_station(self, stations: list, radius_km: float = 2.0) -> bool:
        """True when the bus is inside the servicing radius of any swap station."""
        if not stations:
            return False
        _, dist = self.nearest_station(stations)
        return dist <= radius_km

    def progress_of_waypoint(self, waypoint_index: int) -> float:
        """Route progress (0.0–1.0) of a given waypoint index."""
        self._ensure_geometry()
        cumulative = getattr(self, "_cumulative_lengths", [])
        total = getattr(self, "_route_length_cache", 0.0)
        if len(cumulative) < 2 or total <= 0.0:
            return 0.0
        idx = max(0, min(len(cumulative) - 1, int(waypoint_index)))
        return cumulative[idx] / total

    def nearest_waypoint_index(self, lat: float, lon: float) -> int:
        """Index of the waypoint closest to an arbitrary coordinate.

        Used to steer a diverted bus toward the point on its own corridor that passes
        nearest to a swap hub, which keeps it on real road geometry.
        """
        self._ensure_geometry()
        if not self.waypoints:
            return 0
        best_index = 0
        best_distance = float("inf")
        for index, (wp_lat, wp_lon) in enumerate(self.waypoints):
            distance = haversine_km(lat, lon, wp_lat, wp_lon)
            if distance < best_distance:
                best_distance = distance
                best_index = index
        return best_index

    def divert_towards(self, target_progress: float, step_progress: float) -> float:
        """Move ``step_progress`` of route progress toward ``target_progress``.

        Returns the signed progress delta actually applied. Used to steer a
        low-state-of-charge bus toward the nearest point on its own corridor that
        passes close to a swap station, instead of leaving ``heading_to_swap`` as
        a purely cosmetic status label.
        """
        target_progress = min(1.0, max(0.0, target_progress))
        remaining = target_progress - self.progress
        if abs(remaining) <= abs(step_progress) or remaining == 0.0:
            applied = remaining
        else:
            applied = step_progress
        self.progress = min(1.0, max(0.0, self.progress + applied))
        return applied
