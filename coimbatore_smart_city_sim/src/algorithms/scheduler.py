"""
scheduler.py — Fleet construction, traction energy, and battery-swap scheduling.

Consistency guarantee
---------------------
Traction demand and per-bus state-of-charge drain used to be two independent models
of the same physical consumption: :func:`calc_demand` invented a power curve from
``DAILY_CONSUMPTION_KWH / OPERATING_HOURS``, while :func:`update_fleet` drained
state of charge from a separate ``/ 300.0`` expression. The two agreed only by
construction, so the headline claim that solar surplus services the fleet was never
actually computed.

Both quantities now derive from one normalised energy profile built by
:func:`traction_energy_profile`, whose hourly weights sum to exactly
``DAILY_CONSUMPTION_KWH`` at unity traffic intensity. :func:`update_fleet` returns
the traction power it actually applied, so the reported demand and the energy the
buses really consumed cannot drift apart.

Swap scheduling
---------------
Swaps are prioritised by state of charge and gated by available solar surplus, which
is the mechanism the project is about. Two tiers are reported separately:

``solar``
    Funded by generation surplus in the same step. These are the swaps that make the
    case for co-locating the array with the swap hubs.
``grid``
    A documented fallback top-up, reserved for buses below
    :data:`CRITICAL_SOC_PCT` so that energy scarcity can never strand a vehicle.

The grid tier is intentionally not hidden. Showing that the array cannot fund every
swap is a more useful engineering result than suppressing it.

Scope of the tier label
-----------------------
A tier describes the **scheduling decision** made at the instant of approval, using the
surplus visible in that step. The pack's charging energy is actually drawn on the
*following* step, when the balance may differ, so the label is a commitment rather than
a retrospective measurement of where the electrons came from. The energy balance in
:mod:`src.simulation.engine` remains authoritative for sourcing; the tier explains the
operator's intent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from src.models.bus import SOC_HARD_FLOOR_PCT, Bus

#: Below this state of charge a swap is approved even when no solar surplus exists,
#: so that the energy shortfall can never strand a vehicle.
CRITICAL_SOC_PCT = 15.0

#: Per-vehicle efficiency spread. Vehicle cohorts differ slightly in mass,
#: auxiliary load and driving style; this replaces the previous per-tick randomness
#: with a stable per-bus factor so replays remain exactly reproducible.
EFFICIENCY_BASE = 0.94
EFFICIENCY_STEP = 0.015
EFFICIENCY_COHORT_COUNT = 9


def vehicle_efficiency_factor(bus_id: int) -> float:
    """Stable per-vehicle traction efficiency multiplier."""
    return EFFICIENCY_BASE + (bus_id % EFFICIENCY_COHORT_COUNT) * EFFICIENCY_STEP


def vehicle_speed_kmph(bus_id: int, traffic_factor: float = 1.0) -> float:
    """Deterministic 16-21 km/h urban corridor speed for a vehicle cohort."""
    return (16.0 + (bus_id % 6)) / max(0.5, traffic_factor)


def allocate_fleet_by_route(routes: Dict[str, dict], num_buses: int) -> Dict[str, int]:
    """Distribute ``num_buses`` across routes in proportion to their design share.

    Uses the largest-remainder method so the allocations sum to exactly
    ``num_buses``. Previously the configured per-route counts were used verbatim and
    any surplus or shortfall was assigned at random, so a fleet size other than 260
    silently contradicted both the configuration and the dashboard's own route table.
    """
    route_ids = list(routes.keys())
    if not route_ids or num_buses <= 0:
        return {r: 0 for r in route_ids}

    weights = [max(0, int(routes[r].get("buses_allocated", 0))) for r in route_ids]
    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [1] * len(route_ids)
        total_weight = len(route_ids)

    exact = [num_buses * w / total_weight for w in weights]
    base = [int(x) for x in exact]
    remainder = num_buses - sum(base)

    # Hand out the largest fractional parts first; ties break on route order so the
    # result is deterministic.
    order = sorted(range(len(route_ids)), key=lambda i: (-(exact[i] - base[i]), i))
    for i in order[:remainder]:
        base[i] += 1

    return {r: base[i] for i, r in enumerate(route_ids)}


def create_fleet(
    num_buses: int,
    routes: Dict[str, dict],
    soc_min: float = 55.0,
    soc_max: float = 100.0,
    seed: int = 42,
) -> List[Bus]:
    """Build the fleet, distributed proportionally across corridors.

    Buses start staggered along their corridor and in both directions, so the
    initial state is spatially uniform rather than a convoy at the depot.
    """
    import random

    rng = random.Random(seed)
    allocation = allocate_fleet_by_route(routes, num_buses)

    fleet: List[Bus] = []
    for route_id, count in allocation.items():
        route = routes[route_id]
        for _ in range(count):
            bus_id = len(fleet) + 1
            fleet.append(Bus(
                bus_id=bus_id,
                route_id=route_id,
                waypoints=route["waypoints"],
                stop_names=route.get("stop_names", []),
                stop_waypoint_indices=route.get("stop_waypoint_indices", []),
                soc=round(rng.uniform(soc_min, soc_max), 1),
                state="running",
                progress=round(rng.random(), 3),
                direction=1 if (bus_id % 2 == 0) else -1,
            ))
    return fleet


def traction_energy_profile(
    daily_consumption_kwh: float,
    service_start_hour: float,
    service_end_hour: float,
    peak_hours: Sequence[int],
    peak_factor: float,
    off_peak_factor: float,
) -> Dict[int, float]:
    """Return ``{hour: kWh per bus per hour}`` for one service day.

    The hourly weights (peak demand versus off-peak) are **normalised** so they sum
    to exactly ``daily_consumption_kwh`` across the service window. Without that
    normalisation a peak factor of 1.5 and an off-peak factor of 0.8 silently inflate
    daily fleet consumption by about 15% relative to the stated figure.
    """
    start = int(service_start_hour)
    end = int(service_end_hour)
    hours = [h for h in range(start, end) if start <= h < end]
    if not hours:
        return {}

    weights = {h: (peak_factor if h in set(peak_hours) else off_peak_factor) for h in hours}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return {h: 0.0 for h in hours}

    return {h: daily_consumption_kwh * w / total_weight for h, w in weights.items()}


def is_service_hour(hour: float, service_start_hour: float, service_end_hour: float) -> bool:
    """True when buses are in revenue service at this time of day."""
    return service_start_hour <= hour < service_end_hour


def bus_step_energy_kwh(
    bus: Bus,
    hour: float,
    energy_profile: Dict[int, float],
    step_hours: float,
    traffic_factor: float = 1.0,
) -> float:
    """Traction energy this bus consumes over one simulation step, in kWh.

    Zero for buses that are at a depot or mid-swap. This is the single definition of
    fleet energy use; both the reported traction power and the state-of-charge drain
    are derived from it.

    A **zero-length step is legitimate** and yields ``0.0``: the result is energy, not
    power, so no elapsed time means no energy. That is deliberately different from
    :func:`process_station_charging`, which must divide by the step to report charging
    *power* and therefore rejects a non-positive step outright.

    A **negative step is rejected**. Nothing can produce one, and because the result
    scales linearly with the step it would silently return negative energy -- which reads
    downstream as energy *created* rather than as the caller bug it is.
    """
    if step_hours < 0.0:
        raise ValueError(f"step_hours must not be negative, got {step_hours!r}")
    if bus.state in ("depot", "swapping"):
        return 0.0
    h_int = int(hour) % 24
    per_hour = energy_profile.get(h_int, 0.0)
    if per_hour <= 0.0:
        return 0.0
    return (
        per_hour
        * vehicle_efficiency_factor(bus.bus_id)
        * max(0.5, traffic_factor)
        * step_hours
    )


@dataclass
class FleetStepResult:
    """Outcome of advancing the fleet by one simulation step."""

    traction_kw: float = 0.0
    active_buses: int = 0
    running_buses: int = 0
    diverting_buses: int = 0
    km_this_step: float = 0.0


def update_fleet(
    bus_fleet: List[Bus],
    hour: float,
    daily_consumption_kwh: float,
    energy_profile: Dict[int, float],
    battery_capacity_kwh: float,
    soc_swap_threshold: float,
    stations: Sequence[dict],
    service_start_hour: float,
    service_end_hour: float,
    swap_station_radius_km: float,
    step_hours: float = 0.5,
    traffic_factor: float = 1.0,
) -> FleetStepResult:
    """Advance every bus by one step and return the traction power actually applied.

    Responsibilities, in order:

    1. Complete any swap that started in a previous step (a 7-minute swap finishes
       well inside one 30-minute step).
    2. Draw traction energy and reduce state of charge for buses in service.
    3. Move buses along their corridor at a deterministic cohort speed.
    4. Steer state-of-charge-critical buses toward the nearest corridor point that
       passes a swap station, so ``heading_to_swap`` is a real movement rather than
       a cosmetic label.

    Raises:
        ValueError: if ``step_hours`` is not positive. The reported traction power is
            energy divided by the step length, so a zero or negative step would be a
            division by zero or a physically meaningless negative power.
    """
    if step_hours <= 0.0:
        raise ValueError(f"step_hours must be positive, got {step_hours!r}")

    result = FleetStepResult()
    capacity = max(1.0, battery_capacity_kwh)
    in_service = is_service_hour(hour, service_start_hour, service_end_hour)

    for bus in bus_fleet:
        # 1. Complete a swap started in an earlier step.
        if bus.state == "swapping":
            bus.soc = 100.0
            bus.state = "running"
            bus.swap_remaining_min = 0
            bus.swap_energy_source = None
            bus.diversion_target_progress = None
            continue

        if not in_service:
            bus.state = "depot"
            continue

        # 2. Traction energy. Identical expression to the reported traction power.
        energy_kwh = bus_step_energy_kwh(bus, hour, energy_profile, step_hours, traffic_factor)
        if energy_kwh > 0.0:
            bus.soc = max(SOC_HARD_FLOOR_PCT, round(bus.soc - energy_kwh / capacity * 100.0, 2))
            bus.energy_consumed_kwh += energy_kwh
            result.traction_kw += energy_kwh / step_hours
            result.running_buses += 1

        bus.state = "running" if bus.soc >= soc_swap_threshold else "heading_to_swap"
        if bus.state == "heading_to_swap":
            result.diverting_buses += 1

        # 3./4. Movement: normal corridor running, or diversion toward a swap station.
        speed = vehicle_speed_kmph(bus.bus_id, traffic_factor)
        step_progress = (speed * step_hours / max(bus.route_length_km, 0.1)) * bus.direction

        if bus.state == "heading_to_swap" and bus.swap_station_lat is not None:
            station_lat = bus.swap_station_lat
            station_lon = bus.swap_station_lon
            target_index = bus.nearest_waypoint_index(station_lat, station_lon)
            target_progress = bus.progress_of_waypoint(target_index)
            bus.diversion_target_progress = target_progress
            applied = bus.divert_towards(target_progress, step_progress)
            bus.direction = 1 if applied >= 0 else -1
        else:
            bus.diversion_target_progress = None
            bus.progress += step_progress
            if bus.progress >= 1.0:
                bus.progress = 1.0
                bus.direction = -1
            elif bus.progress <= 0.0:
                bus.progress = 0.0
                bus.direction = 1

        result.km_this_step += speed * step_hours
        if bus.state != "depot":
            result.active_buses += 1

    result.traction_kw = round(result.traction_kw, 2)
    return result


def process_station_charging(
    stations_state: dict,
    step_hours: float,
    max_charge_rate_kw: float = 150.0,
    charging_efficiency: float = 1.0,
    max_active_bays: int = 4,
) -> float:
    """Charge queued packs within finite charger bays; return wall-side power in kW.

    ``charging_packs`` holds packs currently occupying a charger and
    ``charging_queue`` holds packs waiting for a free bay. A station state created
    without an explicit queue is treated as having all supplied packs active, which
    keeps older payloads working.

    Raises:
        ValueError: if ``step_hours`` is not positive, or ``max_charge_rate_kw`` is
            negative. The returned load is energy divided by the step length.
    """
    if step_hours <= 0.0:
        raise ValueError(f"step_hours must be positive, got {step_hours!r}")
    if max_charge_rate_kw < 0.0:
        raise ValueError(f"max_charge_rate_kw must be non-negative, got {max_charge_rate_kw!r}")

    total_load_kw = 0.0
    efficiency = max(0.01, charging_efficiency)

    for state in stations_state.values():
        queue = state.setdefault("charging_queue", [])
        active = state.setdefault("charging_packs", [])

        # Recover from a persisted state that predates a lower bay limit.
        if len(active) > max_active_bays:
            queue[0:0] = active[max_active_bays:]
            active = active[:max_active_bays]
            state["charging_packs"] = active

        while queue and len(active) < max_active_bays:
            active.append(queue.pop(0))

        still_charging: List[float] = []
        charged_kwh = 0.0
        loss_kwh = 0.0

        for missing_kwh in active:
            if missing_kwh <= 0.01:
                state["ready_packs"] += 1
                continue
            # Electrical energy drawn from the bus bar to deliver this much to the pack.
            input_kwh = min(max_charge_rate_kw * step_hours, missing_kwh / efficiency)
            delivered_kwh = min(missing_kwh, input_kwh * efficiency)
            total_load_kw += input_kwh / step_hours
            charged_kwh += delivered_kwh
            loss_kwh += input_kwh - delivered_kwh
            missing_kwh -= delivered_kwh
            if missing_kwh <= 0.01:
                state["ready_packs"] += 1
            else:
                still_charging.append(missing_kwh)

        state["charging_packs"] = still_charging
        state["last_charged_kwh"] = charged_kwh
        state["last_charge_loss_kwh"] = loss_kwh
        state["last_active_bays"] = len(still_charging)

    return total_load_kw


@dataclass
class SwapDecision:
    """Aggregated outcome of one swap-scheduling pass."""

    approved: List[Bus] = field(default_factory=list)
    solar_swaps: List[Bus] = field(default_factory=list)
    grid_swaps: List[Bus] = field(default_factory=list)
    deferred: int = 0
    diverting: int = 0
    station_activity: Dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.approved)


def optimize_swaps(
    bus_fleet: List[Bus],
    stations: Sequence[dict],
    stations_state: dict,
    battery_capacity_kwh: float = 300.0,
    soc_threshold: float = 30.0,
    swap_station_radius_km: float = 2.0,
    solar_surplus_kwh: Optional[float] = None,
    charger_efficiency: float = 0.92,
    critical_soc_pct: float = CRITICAL_SOC_PCT,
    max_swaps_per_step: Optional[int] = None,
) -> SwapDecision:
    """Approve battery swaps for eligible buses, prioritising the lowest state of charge.

    A bus is eligible when it is in service, below ``soc_threshold``, and physically
    within ``swap_station_radius_km`` of a swap hub.

    Funding tiers, in priority order:

    * **Solar** — the step's generation surplus can pay for the pack refill. The
      surplus budget is consumed pack by pack, so surplus genuinely limits how many
      swaps the array can fund.
    * **Grid** — reserved for buses below ``critical_soc_pct``, which are topped up
      from the grid rather than left stranded.

    Buses that are eligible but cannot be funded are simply deferred; they are
    retried on the next step. ``solar_surplus_kwh=None`` disables the budget, which
    is the behaviour used by the unit tests.

    Returns:
        A :class:`SwapDecision`. Approved buses have already been transitioned to
        ``state="swapping"``, a ready pack has been consumed from their hub, and the
        depleted pack has been queued for charging.
    """
    decision = SwapDecision()
    if not stations:
        return decision

    capacity = max(1.0, battery_capacity_kwh)
    efficiency = max(0.01, charger_efficiency)

    eligible: List[Bus] = []
    for bus in bus_fleet:
        if bus.state not in ("running", "heading_to_swap"):
            continue
        if bus.soc >= soc_threshold:
            continue

        station, distance_km = bus.nearest_station(stations)
        bus.swap_station_name = station["name"]
        bus.swap_station_lat = station["lat"]
        bus.swap_station_lon = station["lon"]

        if distance_km <= swap_station_radius_km:
            eligible.append(bus)
        else:
            # Out of range: flag for diversion so update_fleet steers it toward the
            # nearest corridor point that passes this hub.
            bus.state = "heading_to_swap"
            decision.diverting += 1

    eligible.sort(key=lambda b: (b.soc, b.bus_id))

    solar_budget = solar_surplus_kwh
    for bus in eligible:
        if max_swaps_per_step is not None and decision.total >= max_swaps_per_step:
            decision.deferred += 1
            continue

        station_state = stations_state.get(bus.swap_station_name)
        if station_state is None or station_state["ready_packs"] <= 0:
            continue

        # Pack energy needed to bring this pack to full charge.
        pack_kwh_missing = (100.0 - bus.soc) / 100.0 * capacity
        # Wall-side energy the charger must draw to deliver that.
        wall_kwh = pack_kwh_missing / efficiency

        if solar_budget is None:
            source = "solar"
            solar_budget = float("inf")
        elif solar_budget >= wall_kwh:
            source = "solar"
            solar_budget -= wall_kwh
        elif bus.soc < critical_soc_pct:
            source = "grid"
        else:
            decision.deferred += 1
            continue

        station_state["ready_packs"] -= 1
        station_state.setdefault("charging_queue", []).append(pack_kwh_missing)

        bus.state = "swapping"
        bus.swap_remaining_min = 0
        bus.swap_energy_source = source
        bus.diversion_target_progress = None
        bus.total_swaps += 1

        decision.approved.append(bus)
        (decision.solar_swaps if source == "solar" else decision.grid_swaps).append(bus)
        name = bus.swap_station_name
        decision.station_activity[name] = decision.station_activity.get(name, 0) + 1

    return decision


def required_pack_energy_kwh(soc_pct: float, battery_capacity_kwh: float) -> float:
    """Pack energy needed to refill a pack from ``soc_pct`` to full."""
    return max(0.0, (100.0 - soc_pct) / 100.0 * max(0.0, battery_capacity_kwh))
