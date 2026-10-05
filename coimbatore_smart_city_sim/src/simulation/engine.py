"""
engine.py — 24-hour and annual simulation engine for the Coimbatore smart-city system.

Couples, at a 30-minute resolution:

* a floating-PV array on Ukkadam Lake at 10% / 15% / 20% surface coverage;
* an electric bus fleet whose traction power is **derived from the fleet model**
  rather than from a separate demand curve;
* state-of-charge-aware, solar-surplus-gated battery swapping at four hubs;
* municipal captive load, with generation, direct use, curtailment and grid import
  forming a closed energy balance;
* dissolved oxygen, water temperature, pH and turbidity for the lake; and
* carbon abatement and tariff savings counted **only against self-consumed energy**.

Run standalone with::

    python -m src.simulation.engine
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from src.algorithms.scheduler import (
    allocate_fleet_by_route,
    create_fleet,
    is_service_hour,
    optimize_swaps,
    process_station_charging,
    traction_energy_profile,
    update_fleet,
)
from src.algorithms.solar import calc_generation, get_cloud_cover
from src.config.settings import Config
from src.models.bus import Bus
from src.models.ecology import EcologyParams, check_ecology, update_ecology
from src.services.hybrid_data import effective_generation_inputs

#: Length of one simulation step, in hours.
TIME_STEP_HOURS = Config.TIME_STEP_HOURS

#: Steps in one simulated day, derived from the configured timestep so the clock and the
#: step count cannot drift apart. At the shipped 0.5 h resolution this is 48, i.e. the
#: ticks at 00:00, 00:30, ... 23:30.
STEPS_PER_DAY = int(round(24.0 / Config.TIME_STEP_HOURS))

#: Day of year representative of each season for ambient temperature.
SEASON_MONTH = {"summer": 4, "monsoon": 7}

DAYS_IN_MONTH = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}

MONSOON_MONTHS = {6, 7, 8, 9}

#: Analytic planning estimate of annual generation at the reference coverage, GWh.
#: The authoritative figure is produced by :meth:`SimulationEngine.run_annual_simulation`.
ANNUAL_TARGET_GWH = 21.0

#: Days of lake ecology discarded before the reported day, so that the live
#: dissolved-oxygen trace reflects the diurnal steady state at the chosen coverage
#: rather than the transient from the nominal starting concentration.
DEFAULT_ECO_SPIN_UP_DAYS = 10


@dataclass
class SimulationState:
    """Complete system snapshot at one 30-minute simulation step."""

    hour: float = 0.0
    time_str: str = "00:00"
    step_idx: int = 0

    # Solar energy
    generation_kw: float = 0.0
    demand_kw: float = 0.0
    municipal_load_kw: float = 0.0
    swapping_load_kw: float = 0.0
    total_demand_kw: float = 0.0
    surplus_kw: float = 0.0
    grid_import_kw: float = 0.0
    solar_curtailed_kw: float = 0.0
    solar_direct_use_kw: float = 0.0
    charging_loss_kw: float = 0.0
    charged_pack_kwh: float = 0.0
    #: Share of generation consumed directly, or ``None`` when generation was zero.
    solar_utilized_pct: Optional[float] = 100.0
    cumulative_generation_kwh: float = 0.0
    cumulative_demand_kwh: float = 0.0
    cumulative_grid_import_kwh: float = 0.0
    cumulative_direct_use_kwh: float = 0.0
    cumulative_curtailed_kwh: float = 0.0

    # Bus fleet
    swaps_this_step: int = 0
    solar_swaps_this_step: int = 0
    grid_swaps_this_step: int = 0
    swaps_deferred: int = 0
    total_swaps: int = 0
    solar_swaps: int = 0
    grid_swaps: int = 0
    active_buses: int = 0
    swapping_buses: int = 0
    low_soc_buses: int = 0
    diverting_buses: int = 0
    avg_soc: float = 80.0
    fleet_km: float = 0.0
    station_activity: Dict[str, int] = field(default_factory=dict)

    # Lake ecological health
    do_level: float = 5.5
    do_delta: float = 0.0
    water_temp_c: float = 26.5
    ph_level: float = 7.6
    turbidity_ntu: float = 22.0
    aeration_on: bool = False
    aeration_events: int = 0
    eco_status: str = "OK"
    eco_message: str = ""
    coverage_pct: float = 15.0

    # Environmental and economic impact
    co2_avoided_kg: float = 0.0
    cumulative_co2_avoided_kg: float = 0.0
    cost_saved_inr: float = 0.0
    cumulative_cost_saved_inr: float = 0.0

    # Climatology
    season: str = "summer"
    cloud_cover: float = 0.0
    ambient_temp_c: float = 30.0
    phase_name: str = ""


class SimulationEngine:
    """Discrete-event simulation of Coimbatore's energy and lake ecology."""

    #: Seasons the climatology recognises.
    VALID_SEASONS = ("summer", "monsoon")

    def __init__(
        self,
        config: Config,
        coverage_pct: float = 15.0,
        season: str = "summer",
        seed: int = 42,
    ):
        self.config = config
        self.seed = int(seed)
        self.history: List[SimulationState] = []

        # Set before reset() so that every downstream initialiser can rely on these
        # existing, even if reset() is called with None for one of them.
        self.coverage_pct = float(config.DEFAULT_COVERAGE_PCT)
        self.season = "summer"

        self._step_idx = 0
        self._fleet: List[Bus] = []
        self._do = config.DO_INITIAL
        self._aeration = False
        self._aeration_event_count = 0
        self._cumulative_gen = 0.0
        self._cumulative_demand = 0.0
        self._cumulative_grid = 0.0
        self._cumulative_direct = 0.0
        self._cumulative_curtailed = 0.0
        self._cumulative_co2 = 0.0
        self._cumulative_cost = 0.0
        self._solar_swaps = 0
        self._grid_swaps = 0
        self._fleet_km = 0.0
        self._stations_state: Dict[str, dict] = {}
        self._cloud_schedule: Dict[int, float] = {}
        self._ecology_rng = random.Random(seed)
        self._energy_profile: Dict[int, float] = {}
        self._ecology_params: EcologyParams = config.ecology_params()

        self.reset(coverage_pct=coverage_pct, season=season, seed=seed)

    # ── lifecycle ──────────────────────────────────────────────────────────

    def reset(
        self,
        coverage_pct: Optional[float] = None,
        season: Optional[str] = None,
        seed: Optional[int] = None,
    ) -> None:
        """Reset all state back to 00:00 of day 1.

        Passing ``None`` for a parameter keeps its current value. ``coverage_pct`` and
        ``season`` are validated rather than silently coerced, because a typo in a
        season name would otherwise fall back to summer climatology and quietly
        invalidate a result.
        """
        if coverage_pct is not None:
            self.coverage_pct = float(coverage_pct)
        if self.coverage_pct < 0.0:
            raise ValueError(f"coverage_pct must be non-negative, got {self.coverage_pct}")
        if season is not None:
            if season not in self.VALID_SEASONS:
                raise ValueError(
                    f"season must be one of {self.VALID_SEASONS}, got {season!r}"
                )
            self.season = season
        if seed is not None:
            self.seed = int(seed)

        cfg = self.config
        self._step_idx = 0
        self._do = cfg.DO_INITIAL
        self._aeration = False
        self._aeration_event_count = 0
        self._cumulative_gen = 0.0
        self._cumulative_demand = 0.0
        self._cumulative_grid = 0.0
        self._cumulative_direct = 0.0
        self._cumulative_curtailed = 0.0
        self._cumulative_co2 = 0.0
        self._cumulative_cost = 0.0
        self._solar_swaps = 0
        self._grid_swaps = 0
        self._fleet_km = 0.0
        self._ecology_rng = random.Random(self.seed + 10_000)
        self._ecology_params = cfg.ecology_params()

        self._stations_state = {
            station["name"]: {
                "ready_packs": int(station["capacity_buses"]),
                "charging_packs": [],
                "charging_queue": [],
                "last_charged_kwh": 0.0,
                "last_charge_loss_kwh": 0.0,
                "last_active_bays": 0,
            }
            for station in cfg.SWAP_STATIONS
        }

        self._fleet = create_fleet(
            cfg.NUM_BUSES, cfg.ROUTES, cfg.SOC_INITIAL_MIN, cfg.SOC_INITIAL_MAX, seed=self.seed
        )
        self._energy_profile = traction_energy_profile(
            daily_consumption_kwh=cfg.DAILY_CONSUMPTION_KWH,
            service_start_hour=cfg.SERVICE_START_HOUR,
            service_end_hour=cfg.SERVICE_END_HOUR,
            peak_hours=cfg.PEAK_HOURS,
            peak_factor=cfg.PEAK_FACTOR,
            off_peak_factor=cfg.OFF_PEAK_FACTOR,
        )
        self._cloud_schedule = self._build_cloud_schedule()
        self._spin_up_ecology()
        self.history = []

    def _spin_up_ecology(self, days: int = DEFAULT_ECO_SPIN_UP_DAYS) -> None:
        """Advance the lake to its diurnal steady state before the reported day.

        The dashboard simulates a *representative* day, not the first day after
        commissioning. Panels only shade the lake between 10:00 and 15:00, so a lake
        started from a nominal 5.5 mg/L reaches its dissolved-oxygen minimum near
        07:00 on the very first day, before any shading has occurred. Without this
        spin-up the coverage scenario therefore has almost no effect on the live
        dissolved-oxygen trace, and the live minimum disagrees with the scenario
        comparison. Discarding ``days`` transient days removes both problems.
        """
        cfg = self.config
        month = SEASON_MONTH.get(self.season, 4)
        ambient_temp = cfg.MONTHLY_TEMP_C[month]

        for _ in range(days):
            for step_index in range(STEPS_PER_DAY):
                hour = (step_index % STEPS_PER_DAY) * TIME_STEP_HOURS
                self._do, _, self._aeration, _, _, _ = update_ecology(
                    self._do,
                    hour,
                    self.coverage_pct,
                    self._ecology_params,
                    self._aeration,
                    step_hours=TIME_STEP_HOURS,
                    rng=self._ecology_rng,
                    ambient_temp_c=ambient_temp,
                )

    def _build_cloud_schedule(self) -> Dict[int, float]:
        """Seeded diurnal cloud-cover realisation for the simulated day."""
        multiplier = self.config.CLOUD_COVER_MULTIPLIER
        return {
            hour: min(100.0, get_cloud_cover(hour, self.season, seed=self.seed + hour) * multiplier)
            for hour in range(24)
        }

    # ── main step ──────────────────────────────────────────────────────────

    def step(self) -> SimulationState:
        """Advance the simulation by one 30-minute step."""
        cfg = self.config
        step_hours = TIME_STEP_HOURS
        hour = (self._step_idx % STEPS_PER_DAY) * step_hours
        h_int = int(hour) % 24
        minute = 30 if (hour - int(hour)) > 0.25 else 0
        time_str = f"{h_int:02d}:{minute:02d}"

        # 1. Climatology
        cloud = self._cloud_schedule.get(h_int, 10.0)
        month = SEASON_MONTH.get(self.season, 4)
        ambient_temp = cfg.MONTHLY_TEMP_C[month]
        max_kw = cfg.max_generation_kw(self.coverage_pct)

        # 2. Solar generation. In hybrid mode the profile and temperature come
        # from the curated NASA snapshot via the shared helper, so the daily
        # and annual models calibrate identically by construction.
        base_profile, effective_temp = effective_generation_inputs(
            cfg, self.season, ambient_temp)
        generation_kw = calc_generation(
            hour, base_profile, max_kw,
            cloud_cover=cloud, temperature=effective_temp, coverage_pct=self.coverage_pct,
        )

        # 3. Municipal captive load
        municipal_load_kw = cfg.municipal_load_kw(hour)

        # 4. Fleet: draw traction energy, move buses, and report the power applied.
        fleet_result = update_fleet(
            self._fleet,
            hour,
            daily_consumption_kwh=cfg.DAILY_CONSUMPTION_KWH,
            energy_profile=self._energy_profile,
            battery_capacity_kwh=cfg.BATTERY_CAPACITY_KWH,
            soc_swap_threshold=cfg.SOC_SWAP_THRESHOLD,
            stations=cfg.SWAP_STATIONS,
            service_start_hour=cfg.SERVICE_START_HOUR,
            service_end_hour=cfg.SERVICE_END_HOUR,
            swap_station_radius_km=cfg.SWAP_STATION_RADIUS_KM,
            step_hours=step_hours,
            traffic_factor=cfg.TRAFFIC_INTENSITY_FACTOR,
        )
        traction_kw = fleet_result.traction_kw
        self._fleet_km += fleet_result.km_this_step

        # 5. Charging load from packs enqueued by earlier steps.
        swapping_load_kw = process_station_charging(
            self._stations_state,
            step_hours,
            cfg.CHARGE_RATE_KW,
            cfg.CHARGER_EFFICIENCY,
            cfg.CHARGER_BAYS_PER_STATION,
        )
        charged_pack_kwh = sum(s["last_charged_kwh"] for s in self._stations_state.values())
        charge_loss_kwh = sum(s["last_charge_loss_kwh"] for s in self._stations_state.values())

        total_demand_kw = municipal_load_kw + traction_kw + swapping_load_kw

        # 6. Energy balance. Direct use plus curtailment equals generation exactly;
        #    direct use plus grid import equals demand exactly.
        solar_direct_use_kw = min(generation_kw, total_demand_kw)
        surplus_kw = generation_kw - total_demand_kw
        grid_import_kw = max(0.0, -surplus_kw)
        solar_curtailed_kw = max(0.0, surplus_kw)
        # None, not 0.0, when the array produces nothing: "no sun" is not the same
        # statement as "the sun was there and none of it was used", and averaging the
        # two would understate utilisation across a day that includes night.
        solar_utilized_pct = (
            min(100.0, round(solar_direct_use_kw / generation_kw * 100.0, 1))
            if generation_kw > 0.0 else None
        )

        # 7. Swap scheduling, funded by the surplus left over this step.
        decision = optimize_swaps(
            self._fleet,
            cfg.SWAP_STATIONS,
            self._stations_state,
            battery_capacity_kwh=cfg.BATTERY_CAPACITY_KWH,
            soc_threshold=cfg.SOC_SWAP_THRESHOLD,
            swap_station_radius_km=cfg.SWAP_STATION_RADIUS_KM,
            solar_surplus_kwh=max(0.0, surplus_kw) * step_hours,
            charger_efficiency=cfg.CHARGER_EFFICIENCY,
        )
        self._solar_swaps += len(decision.solar_swaps)
        self._grid_swaps += len(decision.grid_swaps)

        # 8. Lake ecology
        new_do, do_delta, new_aeration, w_temp, ph, turb = update_ecology(
            self._do,
            hour,
            self.coverage_pct,
            self._ecology_params,
            self._aeration,
            step_hours=step_hours,
            rng=self._ecology_rng,
            ambient_temp_c=ambient_temp,
        )
        if new_aeration and not self._aeration:
            self._aeration_event_count += 1
        self._do = new_do
        self._aeration = new_aeration
        eco_status, eco_message = check_ecology(new_do, self.coverage_pct)

        # 9. Cumulative energy and impact, counted on self-consumed energy only.
        step_gen_kwh = generation_kw * step_hours
        step_direct_kwh = solar_direct_use_kw * step_hours
        step_demand_kwh = total_demand_kw * step_hours
        step_grid_kwh = grid_import_kw * step_hours
        step_curtailed_kwh = solar_curtailed_kw * step_hours

        self._cumulative_gen += step_gen_kwh
        self._cumulative_demand += step_demand_kwh
        self._cumulative_grid += step_grid_kwh
        self._cumulative_direct += step_direct_kwh
        self._cumulative_curtailed += step_curtailed_kwh

        # Curtailed energy displaces nothing: it is spilled or exported, so neither
        # carbon abatement nor tariff savings are credited against it.
        co2_step = step_direct_kwh * cfg.grid_emission_factor()
        tariff_rate = cfg.tod_tariff_per_kwh(hour) + cfg.OA_SURCHARGE_PER_KWH
        cost_step = step_direct_kwh * tariff_rate
        self._cumulative_co2 += co2_step
        self._cumulative_cost += cost_step

        # 10. Fleet statistics
        socs = [b.soc for b in self._fleet]
        avg_soc = round(sum(socs) / len(socs), 1) if socs else 100.0

        state = SimulationState(
            hour=hour,
            time_str=time_str,
            step_idx=self._step_idx,
            generation_kw=round(generation_kw, 1),
            demand_kw=round(traction_kw, 1),
            municipal_load_kw=round(municipal_load_kw, 1),
            swapping_load_kw=round(swapping_load_kw, 1),
            total_demand_kw=round(total_demand_kw, 1),
            surplus_kw=round(surplus_kw, 1),
            grid_import_kw=round(grid_import_kw, 1),
            solar_curtailed_kw=round(solar_curtailed_kw, 1),
            solar_direct_use_kw=round(solar_direct_use_kw, 1),
            charging_loss_kw=round(charge_loss_kwh / step_hours, 1),
            charged_pack_kwh=round(charged_pack_kwh, 1),
            solar_utilized_pct=solar_utilized_pct,
            cumulative_generation_kwh=round(self._cumulative_gen, 1),
            cumulative_demand_kwh=round(self._cumulative_demand, 1),
            cumulative_grid_import_kwh=round(self._cumulative_grid, 1),
            cumulative_direct_use_kwh=round(self._cumulative_direct, 1),
            cumulative_curtailed_kwh=round(self._cumulative_curtailed, 1),
            swaps_this_step=decision.total,
            solar_swaps_this_step=len(decision.solar_swaps),
            grid_swaps_this_step=len(decision.grid_swaps),
            swaps_deferred=decision.deferred,
            total_swaps=sum(b.total_swaps for b in self._fleet),
            solar_swaps=self._solar_swaps,
            grid_swaps=self._grid_swaps,
            active_buses=fleet_result.active_buses,
            swapping_buses=sum(1 for b in self._fleet if b.state == "swapping"),
            low_soc_buses=sum(1 for b in self._fleet if b.soc < cfg.SOC_SWAP_THRESHOLD),
            diverting_buses=fleet_result.diverting_buses,
            avg_soc=avg_soc,
            fleet_km=round(self._fleet_km, 1),
            station_activity=decision.station_activity,
            do_level=new_do,
            do_delta=do_delta,
            water_temp_c=w_temp,
            ph_level=ph,
            turbidity_ntu=turb,
            aeration_on=new_aeration,
            aeration_events=self._aeration_event_count,
            eco_status=eco_status,
            eco_message=eco_message,
            coverage_pct=self.coverage_pct,
            co2_avoided_kg=round(co2_step, 2),
            cumulative_co2_avoided_kg=round(self._cumulative_co2, 2),
            cost_saved_inr=round(cost_step, 2),
            cumulative_cost_saved_inr=round(self._cumulative_cost, 2),
            season=self.season,
            cloud_cover=cloud,
            ambient_temp_c=ambient_temp,
            phase_name=phase_story(h_int),
        )

        self.history.append(state)
        self._step_idx += 1
        return state

    def run(self, steps: int = STEPS_PER_DAY) -> List[SimulationState]:
        """Run ``steps`` consecutive half-hour steps (48 steps = one day)."""
        return [self.step() for _ in range(steps)]

    # ── accessors ──────────────────────────────────────────────────────────

    def get_fleet(self) -> List[Bus]:
        return self._fleet

    def get_station_telemetry(self) -> List[Dict[str, Any]]:
        """UI-safe snapshot of pack inventory and charger queues."""
        telemetry = []
        for station in self.config.SWAP_STATIONS:
            state = self._stations_state.get(station["name"], {})
            active_bays = len(state.get("charging_packs", []))
            telemetry.append({
                "name": station["name"],
                "ready_packs": int(state.get("ready_packs", 0)),
                "charging_packs": active_bays,
                "queued_packs": len(state.get("charging_queue", [])),
                "active_bays": active_bays,
                "free_bays": max(0, self.config.CHARGER_BAYS_PER_STATION - active_bays),
                "bay_capacity": self.config.CHARGER_BAYS_PER_STATION,
                "pack_capacity": int(station["capacity_buses"]),
                "last_charged_kwh": round(state.get("last_charged_kwh", 0.0), 1),
                "last_charge_loss_kwh": round(state.get("last_charge_loss_kwh", 0.0), 2),
            })
        return telemetry

    def to_dataframe(self) -> pd.DataFrame:
        """Return the recorded history as a DataFrame."""
        if not self.history:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "Hour": s.hour,
                "Time": s.time_str,
                "Generation (kW)": s.generation_kw,
                "Bus Demand (kW)": s.demand_kw,
                "Municipal Load (kW)": s.municipal_load_kw,
                "Swapping Load (kW)": s.swapping_load_kw,
                "Total Demand (kW)": s.total_demand_kw,
                "Surplus (kW)": s.surplus_kw,
                "Grid Import (kW)": s.grid_import_kw,
                "Solar Direct Use (kW)": s.solar_direct_use_kw,
                "Solar Curtailed (kW)": s.solar_curtailed_kw,
                "Solar Utilised (%)": s.solar_utilized_pct,
                "Charging Loss (kW)": s.charging_loss_kw,
                "Pack Energy Recovered (kWh)": s.charged_pack_kwh,
                "Cumulative Generation (kWh)": s.cumulative_generation_kwh,
                "Cumulative Direct Use (kWh)": s.cumulative_direct_use_kwh,
                "Cumulative Curtailed (kWh)": s.cumulative_curtailed_kwh,
                "Cumulative Demand (kWh)": s.cumulative_demand_kwh,
                "Cumulative Grid Import (kWh)": s.cumulative_grid_import_kwh,
                "Swaps This Step": s.swaps_this_step,
                "Solar-Funded Swaps": s.solar_swaps_this_step,
                "Grid-Assisted Swaps": s.grid_swaps_this_step,
                "Swaps Deferred": s.swaps_deferred,
                "Total Swaps": s.total_swaps,
                "Solar-Funded Swaps (Total)": s.solar_swaps,
                "Grid-Assisted Swaps (Total)": s.grid_swaps,
                "Active Buses": s.active_buses,
                "Buses Diverting": s.diverting_buses,
                "Low-SOC Buses": s.low_soc_buses,
                "Avg SOC (%)": s.avg_soc,
                "Fleet Distance (km)": s.fleet_km,
                "DO Level (mg/L)": s.do_level,
                "DO Change (mg/L)": s.do_delta,
                "Water Temp (°C)": s.water_temp_c,
                "pH": s.ph_level,
                "Turbidity (NTU)": s.turbidity_ntu,
                "Aeration Active": s.aeration_on,
                "Eco Status": s.eco_status,
                "CO₂ Avoided (kg)": s.co2_avoided_kg,
                "Cumulative CO₂ (kg)": s.cumulative_co2_avoided_kg,
                "Cost Saved (₹)": s.cost_saved_inr,
                "Cumulative Cost Saved (₹)": s.cumulative_cost_saved_inr,
                "Cloud Cover (%)": s.cloud_cover,
                "Ambient Temp (°C)": s.ambient_temp_c,
                "Coverage (%)": s.coverage_pct,
                "Phase": s.phase_name,
            }
            for s in self.history
        ])

    def daily_summary(self) -> Dict[str, Any]:
        """Aggregate headline figures for the day simulated so far."""
        if not self.history:
            return {}
        last = self.history[-1]
        levels = [s.do_level for s in self.history]
        return {
            "steps": len(self.history),
            "generation_kwh": round(self._cumulative_gen, 1),
            "direct_use_kwh": round(self._cumulative_direct, 1),
            "curtailed_kwh": round(self._cumulative_curtailed, 1),
            "grid_import_kwh": round(self._cumulative_grid, 1),
            "demand_kwh": round(self._cumulative_demand, 1),
            "co2_kg": round(self._cumulative_co2, 1),
            "cost_inr": round(self._cumulative_cost, 2),
            "total_swaps": last.total_swaps,
            "solar_swaps": self._solar_swaps,
            "grid_swaps": self._grid_swaps,
            "min_do": round(min(levels), 3),
            "max_do": round(max(levels), 3),
            "final_do": last.do_level,
            "eco_status": last.eco_status,
            "aeration_events": self._aeration_event_count,
            "fleet_km": round(self._fleet_km, 1),
            "peak_generation_kw": max(s.generation_kw for s in self.history),
        }

    # ── annual model ───────────────────────────────────────────────────────

    def run_annual_simulation(self) -> Dict[str, Any]:
        """Integrate every hour of all 365 days with an independent weather draw.

        Generation and demand are matched **hour by hour**, so the annual figures
        report self-consumed energy, curtailment and grid import separately rather
        than crediting carbon abatement against total generation.
        """
        cfg = self.config
        max_kw = cfg.max_generation_kw(self.coverage_pct)
        energy_profile = traction_energy_profile(
            cfg.DAILY_CONSUMPTION_KWH, cfg.SERVICE_START_HOUR, cfg.SERVICE_END_HOUR,
            cfg.PEAK_HOURS, cfg.PEAK_FACTOR, cfg.OFF_PEAK_FACTOR,
        )
        # Mean per-bus traction power, used for the annual hourly energy balance.
        mean_efficiency = sum(
            0.94 + (i % 9) * 0.015 for i in range(1, cfg.NUM_BUSES + 1)
        ) / max(1, cfg.NUM_BUSES)

        monthly = {
            month: {
                "generation_kwh": 0.0, "bus_kwh": 0.0, "municipal_kwh": 0.0,
                "direct_use_kwh": 0.0, "curtailed_kwh": 0.0, "grid_import_kwh": 0.0,
                "revenue_inr": 0.0,
            }
            for month in DAYS_IN_MONTH
        }

        flat_tariff = cfg.GRID_TARIFF_PER_KWH + cfg.OA_SURCHARGE_PER_KWH

        for month, days in DAYS_IN_MONTH.items():
            season = "monsoon" if month in MONSOON_MONTHS else "summer"
            # Same shared helper as the daily step, so hybrid calibration
            # applies identically here by construction (H1 fix).
            base_profile, eff_temp_c = effective_generation_inputs(
                cfg, season, cfg.MONTHLY_TEMP_C[month])
            bucket = monthly[month]

            for day in range(1, days + 1):
                for hour in range(24):
                    # One independent weather realisation per hour, as in the daily run.
                    cloud = min(100.0, get_cloud_cover(
                        hour, season, seed=self.seed + month * 10_000 + day * 100 + hour
                    ) * cfg.CLOUD_COVER_MULTIPLIER)
                    generation_kw = calc_generation(
                        hour, base_profile, max_kw,
                        cloud_cover=cloud, temperature=eff_temp_c,
                        coverage_pct=self.coverage_pct,
                    )
                    bus_kw = energy_profile.get(hour, 0.0) * cfg.NUM_BUSES * mean_efficiency
                    if is_service_hour(hour, cfg.SERVICE_START_HOUR, cfg.SERVICE_END_HOUR):
                        bus_kw *= max(0.5, cfg.TRAFFIC_INTENSITY_FACTOR)
                    municipal_kw = cfg.municipal_load_kw(float(hour))
                    demand_kw = bus_kw + municipal_kw

                    direct_kw = min(generation_kw, demand_kw)
                    surplus_kw = generation_kw - demand_kw

                    # Revenue accrues on self-consumed energy at the same rate
                    # the daily step uses: the ToD tariff in hybrid mode, the
                    # flat tariff otherwise (H2 fix).
                    hourly_rate = (
                        cfg.tod_tariff_per_kwh(float(hour))
                        if cfg.DATA_MODE == "hybrid"
                        else cfg.GRID_TARIFF_PER_KWH
                    )
                    bucket["revenue_inr"] += direct_kw * (hourly_rate + cfg.OA_SURCHARGE_PER_KWH)

                    bucket["generation_kwh"] += generation_kw
                    bucket["bus_kwh"] += bus_kw
                    bucket["municipal_kwh"] += municipal_kw
                    bucket["direct_use_kwh"] += direct_kw
                    if surplus_kw > 0.0:
                        bucket["curtailed_kwh"] += surplus_kw
                    else:
                        bucket["grid_import_kwh"] -= surplus_kw

        total = {key: sum(monthly[m][key] for m in monthly) for key in monthly[1]}
        blended_retail_rate = (
            total["revenue_inr"] / total["direct_use_kwh"]
            if total["direct_use_kwh"] > 0 else flat_tariff
        )

        return {
            "annual_generation_gwh": round(total["generation_kwh"] / 1e6, 2),
            "target_generation_gwh": ANNUAL_TARGET_GWH,
            "annual_direct_use_gwh": round(total["direct_use_kwh"] / 1e6, 2),
            "annual_curtailed_gwh": round(total["curtailed_kwh"] / 1e6, 2),
            "annual_grid_import_gwh": round(total["grid_import_kwh"] / 1e6, 2),
            "solar_share_of_load_pct": round(
                total["direct_use_kwh"] / total["bus_kwh"] * 100.0, 1
            ) if total["bus_kwh"] > 0 else 0.0,
            "annual_bus_demand_gwh": round(total["bus_kwh"] / 1e6, 2),
            "annual_municipal_demand_gwh": round(total["municipal_kwh"] / 1e6, 2),
            "buses_powered": cfg.NUM_BUSES,
            "annual_co2_tonnes": round(total["direct_use_kwh"] * cfg.grid_emission_factor() / 1000.0, 0),
            "annual_co2_tonnes_if_all_generation_credited": round(
                total["generation_kwh"] * cfg.grid_emission_factor() / 1000.0, 0
            ),
            "annual_savings_inr_cr": round(total["revenue_inr"] / 1e7, 2),
            # Realized retail rate per self-consumed kWh (rate + surcharge),
            # so Tab 3 can value the municipal share at the same rate the
            # engine accrued. Equals the flat tariff in simulated mode.
            "annual_blended_retail_inr_per_kwh": round(blended_retail_rate, 4),
            "monthly_generation_mwh": {m: round(monthly[m]["generation_kwh"] / 1e3, 1) for m in monthly},
            "monthly_bus_consumption_mwh": {m: round(monthly[m]["bus_kwh"] / 1e3, 1) for m in monthly},
            "monthly_direct_use_mwh": {m: round(monthly[m]["direct_use_kwh"] / 1e3, 1) for m in monthly},
            "monthly_curtailed_mwh": {m: round(monthly[m]["curtailed_kwh"] / 1e3, 1) for m in monthly},
            "monthly_grid_import_mwh": {m: round(monthly[m]["grid_import_kwh"] / 1e3, 1) for m in monthly},
            "monthly_co2_t": {
                m: round(monthly[m]["direct_use_kwh"] * cfg.grid_emission_factor() / 1000.0, 1)
                for m in monthly
            },
        }

    def coverage_capacity_map(self) -> Dict[float, int]:
        """Bus allocation per corridor at the current fleet size."""
        return allocate_fleet_by_route(self.config.ROUTES, self.config.NUM_BUSES)


def phase_story(hour: int) -> str:
    """Narrative phase label for the current hour, used for presentation."""
    if hour <= 5:
        return "🌙 Night Basetime — Fleet at depot, array dormant, lake respiration dominates"
    if hour <= 8:
        return "🌅 Sunrise & Morning Rush — Buses enter service, generation ramps up"
    if hour <= 11:
        return "⚡ Solar Surplus Develops — Generation overtakes traction and municipal load"
    if hour <= 13:
        return "☀️ Noon Peak — Maximum surplus, solar-funded swap window opens"
    if hour <= 16:
        return "📉 Afternoon Tapering — Surplus shrinks, remaining swaps become grid-assisted"
    if hour <= 19:
        return "🌇 Evening Commute Peak — Array offline, fleet runs on swapped packs, grid carries load"
    return "🌌 Night Transition — Buses return to depot, lake dissolved oxygen recovers"


if __name__ == "__main__":
    print("=" * 68)
    print("  COIMBATORE SMART CITY — 24-HOUR SIMULATION ENGINE SELF-CHECK")
    print("=" * 68)

    cfg = Config()
    engine = SimulationEngine(cfg, coverage_pct=15.0, season="summer")
    states = engine.run(STEPS_PER_DAY)
    summary = engine.daily_summary()

    print(f"Steps simulated        : {summary['steps']}")
    print(f"Solar generated        : {summary['generation_kwh']:>12,.0f} kWh")
    print(f"  self-consumed        : {summary['direct_use_kwh']:>12,.0f} kWh "
          f"({summary['direct_use_kwh'] / summary['generation_kwh'] * 100:.1f}%)")
    print(f"  curtailed            : {summary['curtailed_kwh']:>12,.0f} kWh "
          f"({summary['curtailed_kwh'] / summary['generation_kwh'] * 100:.1f}%)")
    print(f"Grid import            : {summary['grid_import_kwh']:>12,.0f} kWh")
    print(f"Total demand           : {summary['demand_kwh']:>12,.0f} kWh")
    print(f"Battery swaps          : {summary['total_swaps']:>12d} "
          f"(solar {summary['solar_swaps']}, grid {summary['grid_swaps']})")
    print(f"CO2 avoided            : {summary['co2_kg'] / 1000:>12,.2f} t")
    print(f"Tariff savings         : Rs {summary['cost_inr'] / 1000:>11,.1f} k")
    print(f"Dissolved oxygen       : {summary['min_do']:.2f} - {summary['max_do']:.2f} mg/L "
          f"({summary['eco_status']})")
    print(f"Peak generation        : {summary['peak_generation_kw']:>12,.0f} kW")

    print("\n" + "-" * 68)
    print("Running 365-day climatological model...")
    annual = engine.run_annual_simulation()
    print(f"Annual generation      : {annual['annual_generation_gwh']:.2f} GWh "
          f"(analytic planning estimate: {cfg.annual_generation_mwh(15.0) / 1000:.2f} GWh)")
    print(f"Annual self-consumed   : {annual['annual_direct_use_gwh']:.2f} GWh")
    print(f"Annual curtailment     : {annual['annual_curtailed_gwh']:.2f} GWh")
    print(f"Annual grid import     : {annual['annual_grid_import_gwh']:.2f} GWh")
    print(f"Annual CO2 avoided     : {annual['annual_co2_tonnes']:,.0f} t "
          f"(grid-displacement basis, self-consumed energy only)")
    print("=" * 68)
    print("Self-check complete.")
