"""
app.py — Streamlit dashboard for the Coimbatore smart-city simulation.

BCSE355L Smart Cities · VIT Vellore
Team: Aditya Saxena (24BCE1633) · Narayanan Subramanian (24BCE1699)

What this dashboard shows
-------------------------
* A 48-step (30-minute resolution) simulation of one representative day coupling a
  floating-PV array, a 260-bus electric fleet, battery swapping, municipal captive
  load, and lake ecology.
* Battery swaps split into **solar-funded** and **grid-assisted** tiers, so the
  energy-limited nature of the array is visible rather than hidden.
* Carbon abatement and tariff savings credited **only against self-consumed energy**.
* A scenario comparison across lake coverage, including a **sensitivity analysis**
  over the one calibrated ecological coefficient, and a **stress-test** range that
  reaches the mechanical-aeration mitigation path.
* A 365-day annual model, and a cost-benefit model on a blended savings basis.

Every headline figure carries a provenance tag — ``measured``, ``calibrated`` or
``assumed`` — so a reader can tell at a glance which numbers rest on data.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).parent))

from src.config.settings import BUS_CONDITION_LABELS, Config
from src.models.ecology import EcologyParams, coverage_status_summary, simulate_do_24h
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine, SimulationState
from src.visualization.map import build_frame_trajectory, build_interactive_map_html

# ``st.components.v1.html`` is the only Streamlit API that can host an isolated
# document from an HTML string (``st.iframe`` takes a URL, not markup), so it stays
# in use despite its deprecation notice. The Streamlit version is pinned.
STEPS = STEPS_PER_DAY

#: Epistemic status tags attached to headline figures, so a reader can tell at a
#: glance which numbers rest on data and which rest on judgement.
PROVENANCE = {
    "measured": {
        "tag": "📐 Measured",
        "note": "Taken from a cited published, regulatory or satellite source.",
    },
    "calibrated": {
        "tag": "🔧 Calibrated",
        "note": "One coefficient was tuned so the model reproduces the stated result; "
                "it is not an independent measurement.",
    },
    "assumed": {
        "tag": "📝 Assumed",
        "note": "An editable engineering assumption, not measured.",
    },
    "modelled": {
        "tag": "🧮 Modelled",
        "note": "Output of this simulation, valid only under its stated assumptions.",
    },
}


#: Short basis text reused throughout the assumptions tables.
ASSUMED_BASIS = "engineering assumption, editable in the UI"


def ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Return ``numerator / denominator``, or ``default`` when the denominator is zero.

    Several dashboard ratios have a denominator that is legitimately zero at 0% lake
    coverage, where installed capacity and therefore annual generation are both zero.
    Reporting those as "n/a" is correct; dividing would raise.
    """
    if denominator == 0:
        return default
    return numerator / denominator


def prov(kind: str, detail: str = "") -> str:
    """Render a provenance caption."""
    entry = PROVENANCE[kind]
    suffix = f" {detail}" if detail else ""
    return f'<div class="prov"><b>{entry["tag"]}</b> — {entry["note"]}{suffix}</div>'

# ─────────────────────────────────────────────────────────
# PAGE CONFIGURATION & STYLING
# ─────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Coimbatore Smart City Simulation",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');
html, body, [class*="css"] { font-family:'Outfit',sans-serif !important; }
.stApp { background:linear-gradient(135deg,#090e17 0%,#112035 50%,#090e17 100%); color:#E2E8F0; }
[data-testid="stSidebar"] { background:rgba(8,18,32,0.75); backdrop-filter:blur(12px);
    border-right:1px solid rgba(56,189,248,0.2); }

.hero-banner { background:linear-gradient(135deg,rgba(15,37,62,0.8),rgba(23,59,97,0.6));
    border:1px solid rgba(56,189,248,0.4); border-radius:16px; padding:18px 26px; margin-bottom:16px;
    box-shadow:0 8px 32px rgba(0,0,0,0.4); backdrop-filter:blur(16px); }

.flow-container { display:flex; gap:12px; margin-bottom:18px; flex-wrap:wrap; align-items:stretch; }
.flow-card { flex:1; min-width:138px; background:rgba(13,31,53,0.6);
    border:1px solid rgba(56,189,248,0.25); border-radius:12px; padding:12px 16px; text-align:center;
    box-shadow:0 4px 12px rgba(0,0,0,0.3); backdrop-filter:blur(12px); position:relative; overflow:hidden; }
.flow-card:hover { transform:translateY(-3px); border-color:rgba(56,189,248,0.6); }
.flow-card h4 { margin:0; font-size:10px; color:#94A3B8; text-transform:uppercase;
    letter-spacing:1px; font-weight:600; }
.flow-card .flow-val { font-size:19px; font-weight:800; margin-top:6px; }
.flow-arrow { display:flex; align-items:center; color:rgba(56,189,248,0.4); font-size:20px;
    font-weight:bold; }
.badge { padding:10px 16px; border-radius:8px; font-size:13px; backdrop-filter:blur(8px);
    border-left:4px solid; }
.badge-ok { background:rgba(34,197,94,0.15); border-color:#22C55E; color:#86EFAC; }
.badge-warn { background:rgba(234,179,8,0.15); border-color:#EAB308; color:#FDE047; }
.badge-emerg { background:rgba(239,68,68,0.15); border-color:#EF4444; color:#FCA5A5;
    animation:pulse-emerg 1.5s infinite; }
.badge-crit { background:rgba(168,85,247,0.15); border-color:#A855F7; color:#D8B4FE; }
@keyframes pulse-emerg { 0%,100%{box-shadow:inset 0 0 0 rgba(239,68,68,0);}
    50%{box-shadow:inset 0 0 15px rgba(239,68,68,0.3);} }

[data-testid="metric-container"] { background:rgba(15,37,62,0.6); backdrop-filter:blur(12px);
    border:1px solid rgba(56,189,248,0.2); border-radius:12px; padding:12px 16px; }
[data-testid="metric-container"] label { color:#94A3B8 !important; font-weight:600 !important;
    text-transform:uppercase; letter-spacing:0.5px; font-size:11px !important; }
[data-testid="metric-container"] div[data-testid="stMetricValue"] > div { color:#38BDF8 !important;
    font-weight:800 !important; font-size:25px !important; }

.ticker-wrap { overflow:hidden; background:rgba(8,18,32,0.55);
    border:1px solid rgba(56,189,248,0.18); border-radius:8px; padding:5px 0; margin-bottom:14px; }
.ticker-track { display:inline-block; white-space:nowrap; animation:ticker-scroll 48s linear infinite; }
.ticker-item { display:inline-block; padding:0 30px; font-size:13px; color:#94A3B8; }
.ticker-item b { color:#38BDF8; font-weight:700; }
.ticker-sep { color:rgba(56,189,248,0.25); }
@keyframes ticker-scroll { 0%{transform:translateX(0);} 100%{transform:translateX(-50%);} }

.prov { font-size:11px; color:#7C8CA5; margin:2px 0 8px 0; }
.day-banner { background:linear-gradient(135deg,rgba(16,185,129,0.15),rgba(56,189,248,0.10));
    border:2px solid #10B981; border-radius:14px; padding:14px 22px; margin-bottom:14px;
    animation:banner-glow 2.5s ease-in-out infinite; }
@keyframes banner-glow { 0%,100%{box-shadow:0 0 0 rgba(16,185,129,0);}
    50%{box-shadow:0 0 28px rgba(16,185,129,0.35);} }
.clock-strip { background:rgba(15,37,62,0.9); border:1px solid rgba(56,189,248,0.4);
    border-radius:10px; padding:12px 18px; display:flex; justify-content:space-between;
    align-items:center; margin-bottom:12px; flex-wrap:wrap; gap:8px; }
.wq { background:rgba(15,23,42,0.6); padding:10px 14px; border-radius:8px; font-size:12px; }
.kpi { text-align:center; }
.kpi .k-lbl { font-size:10px; color:#94A3B8; text-transform:uppercase; letter-spacing:1px; }
.kpi .k-val { font-size:17px; font-weight:800; }
.leaflet-container { border-radius:10px; }
</style>
""",
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────
# CACHED MODEL HELPERS
# ─────────────────────────────────────────────────────────
# Each of these is a pure function of the scenario tuple, so caching is safe and each
# replaces a computation that previously ran on every single Streamlit rerun.


def _config_from(coverage: float, fleet: int, battery: float, traffic: float,
                 cloud: float, bays: int, data_mode: str = "simulated",
                 network_mode: str = "standard") -> Config:
    cfg = Config()
    cfg.NUM_BUSES = int(fleet)
    cfg.BATTERY_CAPACITY_KWH = float(battery)
    cfg.TRAFFIC_INTENSITY_FACTOR = float(traffic)
    cfg.CLOUD_COVER_MULTIPLIER = float(cloud)
    cfg.CHARGER_BAYS_PER_STATION = int(bays)
    cfg.DEFAULT_COVERAGE_PCT = float(coverage)
    cfg.set_data_mode(data_mode)
    cfg.set_network_mode(network_mode)
    return cfg


@st.cache_data(show_spinner=False)
def cached_economics(coverage: float, fleet: int, battery: float, traffic: float,
                     cloud: float, bays: int, season: str, seed: int,
                     tariff: float, surcharge: float, diesel_price: float,
                     data_mode: str = "simulated", network_mode: str = "standard") -> Dict[str, Any]:
    """Project economics with each self-consumed kilowatt-hour credited exactly once.

    Split out from the Tab 3 rendering so the no-double-counting property is directly
    testable rather than buried in a Streamlit script.

    Self-consumed solar is apportioned to traction and municipal load in proportion to
    their demand, then each share is valued once against what it actually displaces:

    * solar serving traction displaces the diesel an all-diesel baseline would have
      burned, so it is valued at the implied fuel cost per kWh;
    * solar serving municipal load displaces grid purchase, so it is valued at the retail
      tariff plus the open-access surcharge.

    The total benefit therefore can never exceed
    ``self_consumed_kwh * max(diesel_value, retail_value)``, which the test suite asserts.

    Returns every intermediate quantity, so the dashboard can show its working rather
    than a single opaque number.
    """
    cfg = _config_from(coverage, fleet, battery, traffic, cloud, bays, data_mode, network_mode)
    annual = cached_annual(coverage, fleet, battery, traffic, cloud, bays, season, seed, data_mode, network_mode)

    direct_kwh = annual["annual_direct_use_gwh"] * 1e6
    bus_kwh = annual["annual_bus_demand_gwh"] * 1e6
    municipal_kwh = annual["annual_municipal_demand_gwh"] * 1e6
    total_kwh = bus_kwh + municipal_kwh

    traction_share_of_solar = bus_kwh / total_kwh if total_kwh > 0 else 0.0
    solar_to_traction_kwh = direct_kwh * traction_share_of_solar
    solar_to_municipal_kwh = direct_kwh - solar_to_traction_kwh

    diesel_litres = (
        fleet * cfg.DIESEL_KM_PER_BUS_PER_DAY / cfg.DIESEL_KM_PER_LITRE * 365
        if cfg.DIESEL_KM_PER_LITRE > 0 else 0.0
    )
    baseline_diesel_inr = diesel_litres * diesel_price
    diesel_value_per_kwh = baseline_diesel_inr / bus_kwh if bus_kwh > 0 else 0.0
    # In hybrid mode the engine accrues revenue hour by hour at the ToD tariff,
    # so the municipal share is valued at the realized blend the annual model
    # reports (rate + surcharge included). In simulated mode the blend equals
    # the flat input tariff by construction, so both paths agree exactly.
    if cfg.DATA_MODE == "hybrid":
        retail_value_per_kwh = annual["annual_blended_retail_inr_per_kwh"]
        retail_basis_note = "realized ToD blend (engine-accrued)"
    else:
        retail_value_per_kwh = tariff + surcharge
        retail_basis_note = "input tariff + surcharge"

    tariff_revenue_inr = solar_to_municipal_kwh * retail_value_per_kwh
    diesel_revenue_inr = solar_to_traction_kwh * diesel_value_per_kwh
    benefit_inr = tariff_revenue_inr + diesel_revenue_inr

    traction_solar_fraction = solar_to_traction_kwh / bus_kwh if bus_kwh > 0 else 0.0
    baseline_fuel_co2_t = diesel_litres * cfg.CO2_DIESEL_KG_PER_LITRE / 1000.0
    avoided_fuel_co2_t = baseline_fuel_co2_t * traction_solar_fraction

    capacity_mwp = cfg.capacity_mwp(coverage)
    annual_opex_inr = capacity_mwp * cfg.OPEX_PER_MWP_CR_YEAR * 1e7

    return {
        "annual": annual,
        "capacity_mwp": capacity_mwp,
        "self_consumed_kwh": direct_kwh,
        "bus_demand_kwh": bus_kwh,
        "municipal_demand_kwh": municipal_kwh,
        "solar_to_traction_kwh": solar_to_traction_kwh,
        "solar_to_municipal_kwh": solar_to_municipal_kwh,
        "diesel_litres_per_year": diesel_litres,
        "baseline_diesel_inr": baseline_diesel_inr,
        "diesel_value_per_kwh": diesel_value_per_kwh,
        "retail_value_per_kwh": retail_value_per_kwh,
        "retail_basis_note": retail_basis_note,
        "tariff_revenue_inr": tariff_revenue_inr,
        "diesel_revenue_inr": diesel_revenue_inr,
        "benefit_inr": benefit_inr,
        "upper_bound_benefit_inr": direct_kwh * max(diesel_value_per_kwh, retail_value_per_kwh),
        "traction_solar_fraction": traction_solar_fraction,
        "baseline_fuel_co2_t": baseline_fuel_co2_t,
        "avoided_fuel_co2_t": avoided_fuel_co2_t,
        "annual_opex_inr": annual_opex_inr,
        "net_cashflow_inr": benefit_inr - annual_opex_inr,
    }


@st.cache_data(show_spinner=False)
def cached_trajectory(coverage: float, fleet: int, battery: float, traffic: float,
                      cloud: float, bays: int, season: str, seed: int,
                      data_mode: str = "simulated", network_mode: str = "standard") -> List[Dict[str, Any]]:
    """Pre-compute the 48-frame map trajectory for an isolated engine copy."""
    cfg = _config_from(coverage, fleet, battery, traffic, cloud, bays, data_mode, network_mode)
    engine = SimulationEngine(cfg, coverage_pct=coverage, season=season, seed=seed)
    return build_frame_trajectory(engine, cfg)


@st.cache_data(show_spinner=False)
def cached_daily_run(coverage: float, fleet: int, battery: float, traffic: float,
                     cloud: float, bays: int, season: str, seed: int,
                     data_mode: str = "simulated", network_mode: str = "standard"
                     ) -> Dict[str, Any]:
    """Run a complete reference day and return its summary, frame and telemetry."""
    cfg = _config_from(coverage, fleet, battery, traffic, cloud, bays, data_mode, network_mode)
    engine = SimulationEngine(cfg, coverage_pct=coverage, season=season, seed=seed)
    engine.run(STEPS)
    return {
        "summary": engine.daily_summary(),
        "frame": engine.to_dataframe(),
        "stations": engine.get_station_telemetry(),
        "do_series": [s.do_level for s in engine.history],
    }


@st.cache_data(show_spinner=False)
def cached_annual(coverage: float, fleet: int, battery: float, traffic: float,
                  cloud: float, bays: int, season: str, seed: int,
                  data_mode: str = "simulated", network_mode: str = "standard") -> Dict[str, Any]:
    """Run the 365-day, 8,760-hour annual model."""
    cfg = _config_from(coverage, fleet, battery, traffic, cloud, bays, data_mode, network_mode)
    engine = SimulationEngine(cfg, coverage_pct=coverage, season=season, seed=seed)
    return engine.run_annual_simulation()


@st.cache_data(show_spinner=False)
def cached_scenarios(coverage_options: tuple, season: str, seed: int,
                     ambient_c: float, shade_effectiveness: float) -> Dict[float, Dict[str, Any]]:
    """Steady-state ecological summary for each coverage option.

    Both the live engine and this comparison integrate at the same 30-minute
    resolution and both discard a spin-up transient, so the two views agree.
    """
    params = EcologyParams(shade_effectiveness=shade_effectiveness)
    out: Dict[float, Dict[str, Any]] = {}
    for cov in coverage_options:
        summary = coverage_status_summary(
            cov, params, initial_do=5.5, seed=seed, ambient_temp_c=ambient_c
        )
        series = simulate_do_24h(5.5, cov, params, seed=seed, ambient_temp_c=ambient_c)
        out[cov] = {"summary": summary, "series": series}
    return out


@st.cache_data(show_spinner=False)
def cached_sensitivity(season: str, seed: int, ambient_c: float,
                       shade_effectiveness: float) -> List[Dict[str, Any]]:
    """One-at-a-time sensitivity of minimum dissolved oxygen at 15% coverage.

    The dissolved-oxygen response to coverage is proportional to
    :attr:`EcologyParams.shade_effectiveness`, which is the single calibrated
    coefficient in the ecological model. This table quantifies exactly how much the
    headline conclusion depends on it.
    """
    rows: List[Dict[str, Any]] = []
    for factor in (0.6, 0.8, 1.0, 1.2, 1.4):
        params = EcologyParams(shade_effectiveness=shade_effectiveness * factor)
        row: Dict[str, Any] = {
            "shade_effectiveness": round(shade_effectiveness * factor, 3),
            "factor": factor,
            "factor_label": f"{factor:.1f}×",
        }
        for cov in (10.0, 15.0, 20.0, 25.0):
            summary = coverage_status_summary(
                cov, params, initial_do=5.5, seed=seed, ambient_temp_c=ambient_c
            )
            row[f"min_do_{int(cov)}pct"] = summary["min_do"]
        row["15pct_compliant"] = row["min_do_15pct"] >= 4.0
        rows.append(row)
    return rows


@st.cache_data(show_spinner=False)
def cached_solar_validation(coverage: float, fleet: int, battery: float, traffic: float,
                            cloud: float, bays: int, season: str, seed: int,
                            data_mode: str = "simulated", network_mode: str = "standard") -> List[Dict[str, Any]]:
    """Compare the embedded clear-sky profile against an independent irradiance source."""
    from src.services.nasa_power import MONTH_NAMES, MONTHLY_TEMP_C, monthly_ghi_series

    cfg = _config_from(coverage, fleet, battery, traffic, cloud, bays, data_mode, network_mode)
    annual = SimulationEngine(cfg, coverage_pct=coverage, season=season, seed=seed).run_annual_simulation()
    ghi = monthly_ghi_series(allow_fetch=False)
    days = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
            7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
    capacity_kwp = cfg.capacity_mwp(coverage) * 1000.0

    rows = []
    for month in range(1, 13):
        modelled_mwh = annual["monthly_generation_mwh"][month]
        reference = ghi[month]["value"]
        # Installed capacity is zero at 0% coverage, so the implied specific yield is
        # undefined rather than infinite; report it as None instead of dividing.
        implied = (
            modelled_mwh * 1000.0 / (days[month] * capacity_kwp)
            if capacity_kwp > 0 else None
        )
        rows.append({
            "Month": MONTH_NAMES[month - 1],
            "Air temp (°C)": MONTHLY_TEMP_C[month],
            "Reference GHI (kWh/m²/day)": reference,
            "Reference source": ghi[month]["source"],
            "Modelled yield (kWh/kWp/day)": round(implied, 2) if implied else None,
            "Modelled monthly (MWh)": modelled_mwh,
            "Ratio model/reference": (
                round(implied / reference, 3) if implied and reference else None
            ),
        })
    return rows


# ─────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────

def init_session() -> None:
    ss = st.session_state
    if "config" not in ss:
        ss.config = Config(network_mode="expanded")
    if "engine" not in ss:
        ss.engine = SimulationEngine(ss.config, coverage_pct=15.0, season="summer")
    ss.setdefault("running", False)
    ss.setdefault("step_idx", 0)
    if "last_state" not in ss:
        ss.last_state = ss.engine.step()
        ss.step_idx = 1
    ss.setdefault("history_df", ss.engine.to_dataframe())
    ss.setdefault("sim_speed", 2.0)
    ss.setdefault("map_step", -1)

    # Parameter memory, so a change can be detected and the run restarted.
    for key, default in (
        ("_coverage", 15.0), ("_season", "summer"), ("_fleet", 260),
        ("_battery", 300.0), ("_traffic", 1.0), ("_cloud", 1.0), ("_bays", 4),
        ("_seed", 42), ("_stress", False), ("_data_mode", "simulated"),
        ("_network_mode", "expanded"),
        # Scalar scenario tuple (coverage, fleet, battery, traffic, cloud, bays,
        # season, seed, data_mode, network_mode), kept in step with the controls
        # above so that a fresh session can start a new run without waiting for
        # a control to change.
        ("_scenario", (15.0, 260, 300.0, 1.0, 1.0, 4, "summer", 42, "simulated", "expanded")),
    ):
        ss.setdefault(key, default)


def start_new_run(coverage: float, season: str, seed: int) -> Config:
    """Begin a fresh run on a newly constructed engine and reset every derived view.

    The engine constructor already performs a full reset (including the 10-day
    ecological spin-up and the fleet build), so this deliberately does **not** reset
    again: doing so would repeat all of that work on every sidebar interaction.

    Returns the new :class:`Config`. Callers must rebind their local ``cfg``
    reference, otherwise the scenario table rendered below the sidebar would keep
    quoting the previous configuration's capacity and service window.
    """
    ss = st.session_state
    data_mode = ss.get("_data_mode", "simulated")
    network_mode = ss.get("_network_mode", "expanded")
    cfg = _config_from(*ss._scenario[:6], data_mode=data_mode, network_mode=network_mode)
    ss.config = cfg
    ss.engine = SimulationEngine(cfg, coverage_pct=coverage, season=season, seed=seed)
    ss.step_idx = 0
    ss.last_state = ss.engine.step()
    ss.step_idx = 1
    ss.history_df = ss.engine.to_dataframe()
    ss.running = False
    ss.map_step = -1
    return cfg


def restart_engine(cfg: Config, coverage: float, season: str, seed: int) -> None:
    """Reset the existing engine in place, keeping its identity and config."""
    ss = st.session_state
    ss.engine.reset(coverage_pct=coverage, season=season, seed=seed)
    ss.step_idx = 0
    ss.last_state = ss.engine.step()
    ss.step_idx = 1
    ss.history_df = ss.engine.to_dataframe()
    ss.running = False
    ss.map_step = -1


init_session()
cfg: Config = st.session_state.config
engine: SimulationEngine = st.session_state.engine

# ─────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ Scenario Controls")

    stress_mode = st.toggle(
        "🧪 Stress test (coverage above the 15% cap)",
        value=st.session_state._stress,
        help="Adds non-compliant coverage options. Above roughly 40% in summer the "
             "model crosses the 3.0 mg/L emergency threshold and the mechanical "
             "aerator engages, which demonstrates the mitigation path.",
    )
    coverage_options = cfg.SCENARIO_COVERAGE_OPTIONS + (
        cfg.STRESS_COVERAGE_OPTIONS if stress_mode else []
    )
    coverage_pct = st.select_slider(
        "🌊 Lake surface coverage (%)",
        options=coverage_options,
        value=st.session_state._coverage if st.session_state._coverage in coverage_options else 15.0,
        help="Percentage of Ukkadam Lake surface covered by PV. 15% is the ecological policy cap.",
    )
    fleet_size = st.slider("🚌 Fleet size", 50, 500, st.session_state._fleet, step=10,
                           help="Allocated across the four corridors in proportion to their design share.")
    battery_capacity = st.slider("🔋 Battery pack capacity (kWh)", 150, 450,
                                 int(st.session_state._battery), step=10,
                                 help="Larger packs drain more slowly in percentage terms per step.")
    traffic_factor = st.select_slider(
        "🚦 Traffic / passenger load", options=[0.8, 1.0, 1.25, 1.5],
        value=st.session_state._traffic,
        format_func=lambda v: {0.8: "Light (0.8×)", 1.0: "Normal (1.0×)",
                               1.25: "Heavy (1.25×)", 1.5: "Congested (1.5×)"}[v],
        help="Scales traction energy and reduces corridor speed. Daily consumption of "
             f"{cfg.DAILY_CONSUMPTION_KWH:.0f} kWh per bus applies at 1.0×.",
    )
    cloud_multiplier = st.slider("☁️ Cloud cover multiplier", 0.5, 1.5,
                                 st.session_state._cloud, step=0.25,
                                 help="Scales the seeded daily cloud schedule.")
    charger_bays = st.slider("🔌 Charger bays per hub", 1, 12, st.session_state._bays, step=1,
                             help="Limits how many depleted packs charge concurrently at each hub.")
    season = st.radio("🌦️ Season", ["summer", "monsoon"],
                      index=0 if st.session_state._season == "summer" else 1,
                      format_func=lambda s: "☀️ Summer (high solar)" if s == "summer"
                      else "🌧️ Monsoon (heavy cloud)")
    seed = st.number_input("🎲 Random seed", min_value=1, max_value=9999,
                           value=int(st.session_state._seed), step=1,
                           help="Fixes the weather and mixing-noise realisations so a run is reproducible.")

    st.markdown("##### 🔬 Data & Calibration Mode")
    data_mode_choice = st.radio(
        "Data Source Mode",
        ["simulated", "hybrid"],
        index=0 if st.session_state.get("_data_mode", "simulated") == "simulated" else 1,
        format_func=lambda m: "🧮 FABRICATED — fully generated by this project" if m == "simulated"
        else "📡 AUTHENTIC — gathered data + generated remainder",
        help="🧮 FABRICATED (simulated): every input is generated by this project — "
             "an embedded clear-sky solar curve, a seeded synthetic cloud schedule, "
             "a flat ₹8.00/kWh tariff and the 0.82 kg CO₂/kWh CEA baseline constant. "
             "Nothing here is measured.\n\n"
             "📡 AUTHENTIC (hybrid): the inputs that have a published source are read "
             "live from data/raw/ — NASA POWER satellite irradiance and temperature, "
             "the TNERC Order No. 6 of 2023 time-of-day EV tariff "
             "(₹6.00 / ₹9.00 / ₹7.50), and the CEA FY2023-24 emission factor "
             "(0.727 kg CO₂/kWh). What has no published source stays generated or "
             "remains an explicit assumption — chiefly the synthetic cloud schedule, "
             "the diesel counterfactual and the capital-cost inputs.\n\n"
             "Expand 'Show data provenance' for a per-input breakdown.",
    )

    network_mode_choice = st.radio(
        "Corridor & Station Network",
        ["expanded", "standard"],
        index=0 if st.session_state.get("_network_mode", "expanded") == "expanded" else 1,
        format_func=lambda n: "🚀 Expanded Network (6 Corridors, 6 Hubs)" if n == "expanded"
        else "🏛️ Core Network (4 Corridors, 4 Hubs)",
        help="Expanded: Adds Route 45 (Sathy Rd IT SEZ), Route 70 (Western Vadavalli), "
             "Saravanampatti IT Hub Station, and Vadavalli-RS Puram West Hub. "
             "Core: Shipped baseline (4 corridors, 4 hubs).",
    )

    if st.checkbox("Show data provenance", value=False,
                   help="List every input the model consumes and state whether it is "
                        "authentic (traced to a published source), generated by this "
                        "project, or an unverified engineering assumption."):
        from src.services.hybrid_data import data_provenance_summary

        rows = data_provenance_summary()
        counts = {"authentic": 0, "generated": 0, "assumption": 0, "not available": 0}
        for row in rows:
            counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        active = sum(1 for r in rows if r["mode"] in ("both", "hybrid", "display + vetting"))
        live_authentic = sum(1 for r in rows
                             if r["kind"] == "authentic" and r["mode"] in ("both", "hybrid"))
        st.markdown(f"""
| Data mode | Authentic inputs live in this mode | Generated | Assumptions |
|---|---:|---:|---:|
| 🧮 **Simulated** (fully generated) | 0 of {len(rows)} | {len(rows)} | all fallbacks |
| 📡 **Hybrid** (gathered + simulated) | **{live_authentic}** | {counts['generated']} | {counts['assumption']} |
""")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    speed_map = {"0.5×": 0.5, "1×": 1.0, "2×": 2.0, "5×": 5.0, "10×": 10.0}
    st.session_state.sim_speed = speed_map[
        st.select_slider("⏩ Playback speed", options=list(speed_map), value="2×")
    ]

    changed = (
        coverage_pct != st.session_state._coverage
        or season != st.session_state._season
        or fleet_size != st.session_state._fleet
        or battery_capacity != st.session_state._battery
        or traffic_factor != st.session_state._traffic
        or cloud_multiplier != st.session_state._cloud
        or charger_bays != st.session_state._bays
        or seed != st.session_state._seed
        or stress_mode != st.session_state._stress
        or data_mode_choice != st.session_state.get("_data_mode", "simulated")
        or network_mode_choice != st.session_state.get("_network_mode", "expanded")
    )
    if changed:
        st.session_state.update({
            "_coverage": coverage_pct, "_season": season, "_fleet": fleet_size,
            "_battery": float(battery_capacity), "_traffic": traffic_factor,
            "_cloud": cloud_multiplier, "_bays": charger_bays, "_seed": int(seed),
            "_stress": stress_mode,
            "_data_mode": data_mode_choice,
            "_network_mode": network_mode_choice,
        })
        st.session_state._scenario = (
            float(coverage_pct), int(fleet_size), float(battery_capacity),
            float(traffic_factor), float(cloud_multiplier), int(charger_bays),
            season, int(seed), data_mode_choice, network_mode_choice,
        )
        cfg = start_new_run(float(coverage_pct), season, int(seed))
        engine = st.session_state.engine

    st.markdown("---")
    st.markdown("#### 🎮 Playback")
    b1, b2, b3 = st.columns(3)
    if b1.button("▶ Play", width="stretch"):
        st.session_state.running = True
    if b2.button("⏸ Pause", width="stretch"):
        st.session_state.running = False
    if b3.button("🔄 Reset", width="stretch"):
        restart_engine(cfg, coverage_pct, season, int(seed))
        st.rerun()

    progress = min(1.0, st.session_state.step_idx / STEPS)
    sim_h = int((st.session_state.step_idx * 0.5) % 24)
    sim_m = 30 if ((st.session_state.step_idx * 0.5) % 1.0) >= 0.5 else 0
    st.progress(progress, text=f"Simulation clock {sim_h:02d}:{sim_m:02d} / 23:30 "
                               f"({st.session_state.step_idx}/{STEPS} steps)")

    st.markdown("---")
    st.markdown("#### 📐 Scenario definition")
    st.markdown(f"""
| Parameter | Value |
|---|---|
| Data mode | **{'Pure Simulated (Synthetic)' if cfg.DATA_MODE == 'simulated' else 'Gathered + Simulated (Hybrid NASA/TNPCB)'}** |
| Network scope | **{'Core (4 corridors, 4 hubs)' if cfg.NETWORK_MODE == 'standard' else 'Expanded (6 corridors, 6 hubs)'}** |
| Lake area | 1.295 km² |
| Lake mean depth | {cfg.LAKE_DEPTH_M:.2f} m (volume ≈ {cfg.lake_volume_m3() / 1e6:.2f} M m³) |
| Installed capacity at {coverage_pct:.0f}% | **{cfg.capacity_mwp(coverage_pct):.2f} MWp** |
| Fleet | **{fleet_size} buses** across {len(cfg.ROUTES)} corridors |
| Pack capacity | {battery_capacity:.0f} kWh |
| Service window | {cfg.SERVICE_START_HOUR:.0f}:00 – {cfg.SERVICE_END_HOUR:.0f}:00 |
| Daily traction per bus | {cfg.DAILY_CONSUMPTION_KWH:.0f} kWh at 1.0× traffic |
| Swap hubs | {len(cfg.SWAP_STATIONS)} · {charger_bays} bays each · {cfg.CHARGE_RATE_KW:.0f} kW per bay |
| Swap time | ~{cfg.SWAP_TIME_MIN} min (inside one 30-min step) |
| Ecological cap | {cfg.ECOLOGICAL_CAP_PCT:.0f}% surface coverage |
| DO threshold | ≥ {cfg.DO_MIN_OK:.1f} mg/L, aeration below {cfg.DO_AERATION_TRIGGER:.1f} |
| Seed | {int(seed)} |
""")
    st.markdown("#### ℹ️ Data sources")
    data_mode_note = (
        "Hybrid mode scales the profile by the NASA monthly deviation and accrues "
        "revenue at the TNERC ToD tariff, both read live from data/raw/."
        if cfg.DATA_MODE == "hybrid" else
        "Simulated mode uses the embedded profile, a flat tariff and the 0.82 factor."
    )
    st.caption(
        "Solar profile: embedded SRRA/MNRE-style curve (NASA monthly deviation applied "
        "in hybrid mode) with a −0.4 %/°C cell-temperature derate and an 8 °C floating "
        "cell-temperature rise. Weather: seeded synthetic cloud, cross-checked against "
        "an irradiance reference. Routes: street-accurate OSRM waypoints for the core "
        "network; expanded corridors are hand-digitized pending OSRM verification. "
        "Map tiles: OpenStreetMap, browser-side Leaflet, no API key. Lake DO: "
        "mass-conserving budget model benchmarked against compiled TNPCB/CCMC secondary "
        "data (not fitted to it) — see docs/HYBRID_DATA_MODES.md. " + data_mode_note
    )

# Scenario tuple used as the cache key for every derived computation. The config is
# rebuilt from this tuple so that the live engine and every cached view are guaranteed
# to describe exactly the same scenario. The engine itself is **not** reset here: it
# owns the step-by-step simulation state, and the sidebar has already restarted it if
# any control changed.
SCENARIO = (
    float(coverage_pct), int(fleet_size), float(battery_capacity),
    float(traffic_factor), float(cloud_multiplier), int(charger_bays),
    season, int(seed), data_mode_choice, network_mode_choice,
)
cfg = _config_from(*SCENARIO[:6], data_mode=SCENARIO[8], network_mode=SCENARIO[9])
st.session_state.config = cfg
engine.config = cfg

state: SimulationState = st.session_state.last_state
fleet = engine.get_fleet()

# ─────────────────────────────────────────────────────────
# HEADER
# ─────────────────────────────────────────────────────────

st.markdown("""
<div class="hero-banner">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
    <div>
      <h2 style="margin:0;color:#38BDF8;font-size:22px;font-weight:800;">⚡ Coimbatore Smart City Simulation</h2>
      <p style="margin:4px 0 0;color:#94A3B8;font-size:13px;">
        Floating solar on Ukkadam Lake · electric bus fleet with battery swapping ·
        lake ecological reserve
      </p>
    </div>
    <div style="text-align:right;color:#CBD5E1;font-size:12px;">
      <b>Course:</b> BCSE355L Smart Cities (VIT Vellore)<br>
      <b>Team:</b> Aditya Saxena (24BCE1633) &amp; Narayanan Subramanian (24BCE1699)
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

h_int = int(state.hour)
am_pm = "AM" if h_int < 12 else "PM"
civic = 12 if h_int % 12 == 0 else h_int % 12
minute = 30 if (state.hour - h_int) > 0.25 else 0
time_label = f"{civic:02d}:{minute:02d} {am_pm}"

top1, top2 = st.columns([3, 1])
with top1:
    st.markdown(f"""
    <div class="clock-strip">
      <div>
        <span style="font-size:10px;text-transform:uppercase;color:#94A3B8;letter-spacing:1px;">Simulation clock</span><br>
        <span style="font-size:23px;font-weight:800;color:#38BDF8;">{time_label}</span>
        <span style="color:#94A3B8;font-size:12px;margin-left:8px;">
          Representative day · {cfg.capacity_mwp(coverage_pct):.1f} MWp · {fleet_size} buses</span>
      </div>
      <div style="text-align:right;max-width:60%;">
        <span style="font-size:10px;text-transform:uppercase;color:#94A3B8;letter-spacing:1px;">Phase</span><br>
        <span style="font-size:12px;font-weight:600;color:#FDE047;">{state.phase_name}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)
with top2:
    if st.button("⏭ Step +30 min", width="stretch"):
        if st.session_state.step_idx >= STEPS:
            restart_engine(cfg, coverage_pct, season, int(seed))
        st.session_state.last_state = engine.step()
        st.session_state.step_idx += 1
        st.session_state.history_df = engine.to_dataframe()
        st.rerun()

# ─────────────────────────────────────────────────────────
# ENERGY FLOW
# ─────────────────────────────────────────────────────────

gen, muni = state.generation_kw, state.municipal_load_kw
bus_kw, swap_kw = state.demand_kw, state.swapping_load_kw
surplus, grid = state.surplus_kw, state.grid_import_kw
balance_colour = "#22C55E" if surplus >= 0 else "#EF4444"
balance_text = (f"+{surplus:,.0f} kW surplus" if surplus >= 0 else f"{grid:,.0f} kW from grid")

st.markdown(f"""
<div class="flow-container">
  <div class="flow-card" style="border-color:#F59E0B;">
    <h4>☀️ Floating solar</h4><div class="flow-val" style="color:#FCD34D;">{gen:,.0f} kW</div></div>
  <div class="flow-arrow">➔</div>
  <div class="flow-card" style="border-color:#38BDF8;">
    <h4>🏛️ Municipal</h4><div class="flow-val" style="color:#38BDF8;">{muni:,.0f} kW</div></div>
  <div class="flow-arrow">➔</div>
  <div class="flow-card" style="border-color:#A855F7;">
    <h4>🚌 Traction</h4><div class="flow-val" style="color:#C084FC;">{bus_kw:,.0f} kW</div></div>
  <div class="flow-arrow">➔</div>
  <div class="flow-card" style="border-color:#10B981;">
    <h4>🔋 Pack charging</h4><div class="flow-val" style="color:#34D399;">{swap_kw:,.0f} kW</div></div>
  <div class="flow-arrow">➔</div>
  <div class="flow-card" style="border-color:{balance_colour};">
    <h4>⚖️ Balance</h4>
    <div class="flow-val" style="color:{balance_colour};font-size:14px;">{balance_text}</div></div>
</div>
""", unsafe_allow_html=True)

ticker = [
    ("☀️ Solar output", f"{gen:,.0f} kW"),
    ("🏛️ Municipal", f"{muni:,.0f} kW"),
    ("🚌 Traction", f"{bus_kw:,.0f} kW"),
    ("🔋 Charging", f"{swap_kw:,.0f} kW"),
    ("⚡ Swaps", f"{state.total_swaps}"),
    ("☀️ Solar-funded", f"{state.solar_swaps}"),
    ("🔌 Grid-assisted", f"{state.grid_swaps}"),
    ("💧 Dissolved O₂", f"{state.do_level:.2f} mg/L"),
    ("📊 Avg SOC", f"{state.avg_soc:.0f}%"),
    ("🌿 CO₂ avoided", f"{state.cumulative_co2_avoided_kg / 1000:.2f} t"),
    ("💰 Cost saved", f"₹{state.cumulative_cost_saved_inr / 1000:.1f}k"),
    ("🌡️ Lake temp", f"{state.water_temp_c:.1f}°C"),
    ("☁️ Cloud", f"{state.cloud_cover:.0f}%"),
    ("♻️ Curtailed", f"{state.cumulative_curtailed_kwh:,.0f} kWh"),
]
items = "".join(
    f'<span class="ticker-item">{label}: <b>{value}</b></span><span class="ticker-sep"> · </span>'
    for label, value in ticker
)
st.markdown(f'<div class="ticker-wrap"><div class="ticker-track">{items * 2}</div></div>',
            unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────

tab_live, tab_scenarios, tab_finance, tab_annual = st.tabs([
    "🗺️ Live simulation", "🌊 Coverage scenarios & sensitivity",
    "💰 Cost-benefit", "📅 Annual model & exports",
])

# ═══════════════════════════════════════════════════════════
# TAB 1 — LIVE SIMULATION
# ═══════════════════════════════════════════════════════════

with tab_live:
    df_hist = st.session_state.history_df

    if st.session_state.step_idx >= STEPS and not df_hist.empty:
        daily = cached_daily_run(*SCENARIO)
        s = daily["summary"]
        icon = "✅" if s["min_do"] >= 4.0 else "⚠️"
        st.markdown(f"""
        <div class="day-banner">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
            <div>
              <span style="font-size:17px;font-weight:800;color:#34D399;">🎉 Representative day complete</span><br>
              <span style="color:#94A3B8;font-size:12px;">All 48 half-hour steps integrated ·
                seed {int(seed)} · reproducible</span>
            </div>
            <div style="display:flex;gap:22px;flex-wrap:wrap;text-align:center;">
              <div class="kpi"><div class="k-lbl">Generated</div>
                <div class="k-val" style="color:#FCD34D;">{s['generation_kwh']:,.0f} kWh</div></div>
              <div class="kpi"><div class="k-lbl">Self-consumed</div>
                <div class="k-val" style="color:#34D399;">{s['direct_use_kwh']:,.0f} kWh</div></div>
              <div class="kpi"><div class="k-lbl">Swaps</div>
                <div class="k-val" style="color:#34D399;">{s['total_swaps']}</div></div>
              <div class="kpi"><div class="k-lbl">Min DO</div>
                <div class="k-val" style="color:#38BDF8;">{icon} {s['min_do']:.2f}</div></div>
              <div class="kpi"><div class="k-lbl">CO₂ avoided</div>
                <div class="k-val" style="color:#A78BFA;">{s['co2_kg'] / 1000:.2f} t</div></div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    # ── Map ────────────────────────────────────────────────────────────────
    map_col, metrics_col = st.columns([3, 2])
    with map_col:
        st.markdown("#### 🗺️ Coimbatore transit network and Ukkadam Lake")
        st.caption(
            "The map renders the step driven by the Streamlit clock. There is no "
            "independent animation timer, so the map and the metric cards cannot drift apart."
        )
        map_step = max(0, min(STEPS - 1, st.session_state.step_idx - 1))
        if st.session_state.map_step != map_step:
            with st.spinner("Preparing map trajectory…"):
                frames = cached_trajectory(*SCENARIO)
                st.session_state.map_html = build_interactive_map_html(
                    cfg, frames, initial_step=map_step,
                    fleet_size=fleet_size, max_kw=cfg.max_generation_kw(coverage_pct),
                )
            st.session_state.map_step = map_step
        components.html(st.session_state.map_html, height=520, scrolling=False)

        # Route operations board
        st.markdown("#### 🛣️ Route operations board")
        allocation = engine.coverage_capacity_map()
        route_rows = []
        for route_id, route in cfg.ROUTES.items():
            assigned = [b for b in fleet if b.route_id == route_id]
            in_service = [b for b in assigned if b.state not in ("depot", "swapping")]
            low = sum(b.soc < cfg.SOC_SWAP_THRESHOLD for b in assigned)
            diverting = sum(b.state == "heading_to_swap" for b in assigned)
            inbound = sum(b.direction < 0 for b in assigned)
            length = assigned[0].route_length_km if assigned else 0.0
            route_rows.append({
                "Corridor": route_id,
                "Route": route["name"],
                "Planned": allocation.get(route_id, 0),
                "Assigned": len(assigned),
                "In service": len(in_service),
                "Length (km)": round(length, 1),
                "Avg SOC": f"{sum(b.soc for b in assigned) / max(1, len(assigned)):.1f}%" if assigned else "—",
                "Swap priority": low,
                "Diverting": diverting,
                "Inbound / outbound": f"{inbound} / {len(assigned) - inbound}",
                "Energy used (kWh)": round(sum(b.energy_consumed_kwh for b in assigned), 1),
                "Stops": len(route.get("stop_names", [])),
            })
        st.dataframe(pd.DataFrame(route_rows), hide_index=True, width="stretch")

        route_fig = go.Figure()
        for route_id, colour in ((r, cfg.ROUTES[r]["color"]) for r in cfg.ROUTES):
            assigned = [b for b in fleet if b.route_id == route_id]
            if not assigned:
                continue
            route_fig.add_trace(go.Bar(
                x=[route_id],
                y=[sum(b.soc for b in assigned) / len(assigned)],
                name=f"{route_id} avg SOC", marker_color=colour,
                text=[f"{sum(b.soc for b in assigned) / len(assigned):.0f}%"],
                textposition="inside", textfont=dict(size=10, color="#0B1422"),
            ))
        route_fig.update_layout(
            title="Average state of charge by corridor", height=250, template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.5)",
            yaxis=dict(title="Avg SOC (%)", range=[0, 100]), showlegend=False,
            margin=dict(t=40, b=30, l=45, r=15),
        )
        st.plotly_chart(route_fig, width="stretch")

        # Fleet condition monitor
        st.markdown("#### 🚌 Fleet condition and stop monitor")
        st.caption("Sorted by operational risk. Diverting buses are steering toward the "
                   "nearest point on their own corridor that passes a swap hub.")
        f1, f2 = st.columns(2)
        selected_route = f1.selectbox("Filter by corridor", ["All", *cfg.ROUTES])
        # Built from the same labels ``Bus.condition`` returns, so renaming a status in
        # settings cannot silently filter every row out of the table.
        selected_status = f2.multiselect(
            "Filter by condition",
            list(BUS_CONDITION_LABELS),
        )
        rows = fleet
        if selected_route != "All":
            rows = [b for b in rows if b.route_id == selected_route]
        if selected_status:
            rows = [b for b in rows if b.condition in selected_status]
        risky = sorted(rows, key=lambda b: (b.soc >= 30, b.soc, b.bus_id))[:60]
        st.dataframe(pd.DataFrame([{
            "Bus": b.bus_id,
            "Corridor": b.route_id,
            "Condition": b.condition,
            "SOC": f"{b.soc:.1f}%",
            "Stored": f"{b.battery_energy_kwh(cfg.BATTERY_CAPACITY_KWH):.0f} kWh",
            "Current stop": b.current_stop,
            "Next stop": b.next_stop,
            "Speed": f"{(16 + (b.bus_id % 6)) / max(0.5, cfg.TRAFFIC_INTENSITY_FACTOR):.1f} km/h"
                     if b.state not in ("swapping", "depot") else "0 km/h",
            "Nearest hub": b.swap_station_name or b.nearest_station(cfg.SWAP_STATIONS)[0]["name"],
            "Hub distance": f"{b.nearest_station(cfg.SWAP_STATIONS)[1]:.2f} km",
            "Energy (kWh)": round(b.energy_consumed_kwh, 1),
            "Swaps": b.total_swaps,
            "Swap funded by": b.swap_energy_source or "—",
        } for b in risky]), hide_index=True, width="stretch")

        # Charging stations
        st.markdown("#### ⚡ Swap hub inventory and service zones")
        telemetry = {t["name"]: t for t in engine.get_station_telemetry()}
        st.dataframe(pd.DataFrame([{
            "Hub": station["name"],
            "Swaps this step": state.station_activity.get(station["name"], 0),
            "Ready packs": telemetry[station["name"]]["ready_packs"],
            "Charging": telemetry[station["name"]]["charging_packs"],
            "Queued": telemetry[station["name"]]["queued_packs"],
            "Free bays": telemetry[station["name"]]["free_bays"],
            "Bays": telemetry[station["name"]]["bay_capacity"],
            "Pack inventory": telemetry[station["name"]]["pack_capacity"],
            "Rated per bay": f"{cfg.CHARGE_RATE_KW:.0f} kW",
            "Service radius": f"{cfg.SWAP_STATION_RADIUS_KM:.1f} km",
            "Status": "CHARGING" if telemetry[station["name"]]["charging_packs"]
                      else ("QUEUED" if telemetry[station["name"]]["queued_packs"] else "STANDBY"),
        } for station in cfg.SWAP_STATIONS]), hide_index=True, width="stretch")

    with metrics_col:
        st.markdown("#### 📈 Real-time telemetry")
        badge = {"OK": "badge-ok", "WARNING": "badge-warn",
                 "EMERGENCY": "badge-emerg", "CRITICAL": "badge-crit"}.get(
                     state.eco_status, "badge-ok")
        st.markdown(f'<div class="badge {badge}"><b>Lake status: {state.eco_status}</b><br>'
                    f'{state.eco_message}</div>', unsafe_allow_html=True)

        m1, m2 = st.columns(2)
        m1.metric("☀️ Solar generation", f"{gen:,.0f} kW",
                  delta=f"{cfg.max_generation_kw(coverage_pct) / 1000:.1f} MWp installed")
        m2.metric("🚌 Traction demand", f"{bus_kw:,.0f} kW",
                  delta=f"{state.active_buses} buses in service")
        m3, m4 = st.columns(2)
        m3.metric("⚡ Swaps today", f"{state.total_swaps}",
                  delta=f"{state.solar_swaps} solar · {state.grid_swaps} grid")
        m4.metric("💧 Dissolved oxygen", f"{state.do_level:.2f} mg/L",
                  delta="≥ 4.0 mg/L ✅" if state.do_level >= 4.0 else "⚠️ BELOW 4.0",
                  delta_color="normal" if state.do_level >= 4.0 else "inverse")
        m5, m6 = st.columns(2)
        m5.metric("🌿 CO₂ avoided", f"{state.cumulative_co2_avoided_kg / 1000:.2f} t",
                  delta="self-consumed energy only")
        m6.metric("💰 Cost saved", f"₹{state.cumulative_cost_saved_inr / 1000:,.1f}k",
                  delta=f"fleet SOC {state.avg_soc:.0f}%")

        st.markdown(f"""
        <div class="wq">
          <b>Lake water quality</b><br>
          • Temperature <b>{state.water_temp_c:.1f} °C</b> &nbsp;|&nbsp; pH <b>{state.ph_level:.2f}</b>
          &nbsp;|&nbsp; Turbidity <b>{state.turbidity_ntu:.1f} NTU</b><br>
          • Aerator <b>{'🚨 ACTIVE' if state.aeration_on else 'standby'}</b> &nbsp;|&nbsp;
          Aeration events <b>{state.aeration_events}</b><br>
          • Cloud <b>{state.cloud_cover:.0f}%</b> &nbsp;|&nbsp; Air <b>{state.ambient_temp_c:.1f} °C</b>
          &nbsp;|&nbsp; Fleet distance <b>{state.fleet_km:,.0f} km</b>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(prov("modelled"), unsafe_allow_html=True)

    # ── Charts ─────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📊 Diurnal dynamics and energy balance")
    plot_df = df_hist if not df_hist.empty else cached_daily_run(*SCENARIO)["frame"]

    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        if not plot_df.empty:
            fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["Generation (kW)"],
                                     name="☀️ Generation", line=dict(color="#FCD34D", width=2.5),
                                     fill="tozeroy", fillcolor="rgba(252,211,77,0.12)"))
            fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["Total Demand (kW)"],
                                     name="🔌 Total load", line=dict(color="#F87171", width=2)))
            fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["Solar Curtailed (kW)"],
                                     name="⚠️ Curtailed", line=dict(color="#FB923C", width=1.5,
                                     dash="dot")))
        fig.add_vline(x=state.hour, line_width=2, line_dash="dash", line_color="#38BDF8",
                      annotation_text=f"▶ {time_label}", annotation_position="top")
        fig.update_layout(
            title="Generation, load and curtailment (kW)", height=300, template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.5)",
            xaxis=dict(title="Hour of day", dtick=2, range=[0, 24]),
            yaxis=dict(title="kW"), legend=dict(orientation="h", y=-0.3, font=dict(size=10)),
            margin=dict(t=35, b=55, l=45, r=15),
        )
        st.plotly_chart(fig, width="stretch")

    with c2:
        socs = [b.soc for b in fleet]
        buckets = {
            "🟢 ≥60%": sum(1 for v in socs if v >= 60),
            "🟡 30–60%": sum(1 for v in socs if 30 <= v < 60),
            "🔴 <30%": sum(1 for v in socs if v < 30),
            "⚡ Swapping": sum(1 for b in fleet if b.state == "swapping"),
            "↩ Diverting": sum(1 for b in fleet if b.state == "heading_to_swap"),
        }
        pie = go.Figure(go.Pie(
            labels=list(buckets), values=list(buckets.values()),
            marker=dict(colors=["#22C55E", "#EAB308", "#EF4444", "#38BDF8", "#F97316"]),
            hole=0.45, textinfo="label+value", textfont=dict(size=11)))
        pie.update_layout(
            title=f"Fleet state of charge ({fleet_size} buses · avg {state.avg_soc:.0f}%)",
            height=300, template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(t=35, b=20, l=20, r=20), showlegend=False)
        st.plotly_chart(pie, width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        do_fig = go.Figure()
        do_fig.add_hrect(y0=0, y1=3.0, fillcolor="rgba(239,68,68,0.14)", line_width=0,
                         annotation_text="Emergency (<3.0)", annotation_position="top left")
        do_fig.add_hrect(y0=3.0, y1=4.0, fillcolor="rgba(234,179,8,0.12)", line_width=0,
                         annotation_text="Warning (<4.0)", annotation_position="top left")
        do_fig.add_hline(y=4.0, line_dash="dot", line_color="#EAB308",
                         annotation_text="4.0 mg/L threshold", annotation_position="bottom right")
        if not plot_df.empty:
            do_fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["DO Level (mg/L)"],
                                        name="Dissolved oxygen", line=dict(color="#38BDF8", width=2.5),
                                        fill="tozeroy", fillcolor="rgba(56,189,248,0.08)"))
        do_fig.add_vline(x=state.hour, line_width=2, line_dash="dash", line_color="#38BDF8")
        do_fig.update_layout(
            title="Lake dissolved oxygen (mg/L)", height=300, template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.5)",
            xaxis=dict(title="Hour of day", dtick=2, range=[0, 24]),
            yaxis=dict(title="mg/L", range=[2.0, 9.0]), showlegend=False,
            margin=dict(t=35, b=45, l=45, r=15))
        st.plotly_chart(do_fig, width="stretch")

    with c4:
        swap_fig = go.Figure()
        if not plot_df.empty:
            swap_fig.add_trace(go.Bar(x=plot_df["Hour"], y=plot_df["Solar-Funded Swaps"],
                                      name="☀️ Solar-funded", marker_color="#34D399"))
            swap_fig.add_trace(go.Bar(x=plot_df["Hour"], y=plot_df["Grid-Assisted Swaps"],
                                      name="🔌 Grid-assisted", marker_color="#F97316"))
            swap_fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["Swapping Load (kW)"],
                                          name="Charging load (kW)", line=dict(color="#FCD34D", width=2),
                                          yaxis="y2"))
        swap_fig.update_layout(
            title="Swaps by funding source, locked to solar surplus", barmode="stack",
            height=300, template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.5)",
            xaxis=dict(title="Hour of day", dtick=2, range=[0, 24]),
            yaxis=dict(title="Swaps"), yaxis2=dict(title="kW", overlaying="y", side="right",
                                                  showgrid=False),
            legend=dict(orientation="h", y=-0.3, font=dict(size=10)),
            margin=dict(t=35, b=55, l=45, r=45))
        st.plotly_chart(swap_fig, width="stretch")

    if st.session_state.step_idx >= STEPS and not plot_df.empty:
        st.markdown("---")
        st.markdown("#### 🧾 Full-day result")
        s = cached_daily_run(*SCENARIO)["summary"]
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Solar generated", f"{s['generation_kwh']:,.0f} kWh",
                  delta=f"peak {s['peak_generation_kw']:,.0f} kW")
        r2.metric("Self-consumed", f"{s['direct_use_kwh']:,.0f} kWh",
                  delta=f"{s['direct_use_kwh'] / s['generation_kwh'] * 100:.0f}% of output")
        r3.metric("Curtailed", f"{s['curtailed_kwh']:,.0f} kWh",
                  delta=f"{s['curtailed_kwh'] / s['generation_kwh'] * 100:.0f}% of output")
        r4.metric("DO range", f"{s['min_do']:.2f} – {s['max_do']:.2f} mg/L",
                  delta="✅ above 4.0" if s["min_do"] >= 4.0 else "⚠️ below 4.0")
        st.info(
            "**Interpretation.** Solar-funded swaps cluster in the midday surplus window; "
            "grid-assisted swaps occur in the evening deficit. That split is the honest "
            "result: at this array size the surplus funds roughly "
            f"{s['solar_swaps']} of {s['total_swaps']} daily swaps, and the remainder must "
            "come from the grid. Curtailed energy is excluded from both the carbon and "
            "tariff figures, because curtailed output displaces nothing."
        )

# ═══════════════════════════════════════════════════════════
# TAB 2 — COVERAGE SCENARIOS & SENSITIVITY
# ═══════════════════════════════════════════════════════════

with tab_scenarios:
    st.markdown("### 🌊 Lake coverage: ecological and energy trade-off")
    st.markdown(
        f"Each coverage is evaluated at the **diurnal steady state** for the "
        f"{season} climatology, discarding a 10-day spin-up. Both this table and the "
        f"live trace integrate at the same 30-minute resolution, so they agree to "
        f"within {0.05:.2f} mg/L."
    )
    ambient = cfg.MONTHLY_TEMP_C[4 if season == "summer" else 7]
    scenarios = cached_scenarios(
        tuple(float(c) for c in coverage_options), season, int(seed), ambient,
        cfg.DO_SHADE_EFFECTIVENESS,
    )

    rows = []
    for cov, data in scenarios.items():
        summary = data["summary"]
        over_cap = cov > cfg.ECOLOGICAL_CAP_PCT
        rows.append({
            "Coverage": f"{cov:.0f}%",
            "Capacity": f"{cfg.capacity_mwp(cov):.2f} MWp",
            "Min DO (mg/L)": summary["min_do"],
            "Max DO (mg/L)": summary["max_do"],
            "Aeration (h)": summary["aeration_hours"],
            "DO status": "EMERGENCY" if summary["min_do"] < 3.0
                         else ("WARNING" if summary["min_do"] < 4.0 else "OK"),
            "Policy": "⛔ exceeds cap" if over_cap else "✅ within cap",
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    left, right = st.columns(2)
    with left:
        ladder = go.Figure()
        colours = {c: ("#EF4444" if c > cfg.ECOLOGICAL_CAP_PCT else
                       "#22C55E" if scenarios[c]["summary"]["min_do"] >= 4.0 else "#EAB308")
                   for c in scenarios}
        for cov, data in scenarios.items():
            series = data["series"]
            ladder.add_trace(go.Scatter(
                x=[r["hour"] for r in series], y=[r["do_level"] for r in series],
                name=f"{cov:.0f}%", line=dict(color=colours[cov], width=2.5)))
        ladder.add_hline(y=4.0, line_dash="dot", line_color="#EAB308",
                         annotation_text="4.0 mg/L minimum")
        ladder.add_hline(y=3.0, line_dash="dot", line_color="#EF4444",
                         annotation_text="3.0 mg/L aeration trigger")
        ladder.update_layout(
            title="Steady-state dissolved oxygen by coverage", height=330,
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(title="Hour of day", dtick=3), yaxis=dict(title="mg/L", range=[2.4, 9.0]),
            legend=dict(orientation="h", y=-0.25, font=dict(size=10)), margin=dict(t=40, b=50))
        st.plotly_chart(ladder, width="stretch")

    with right:
        caps = [c for c in scenarios]
        energy = go.Figure()
        energy.add_trace(go.Bar(
            x=[f"{c:.0f}%" for c in caps],
            y=[cfg.annual_generation_mwh(c) / 1000.0 for c in caps],
            name="Analytic planning estimate (GWh)", marker_color="#38BDF8"))
        energy.add_trace(go.Scatter(
            x=[f"{c:.0f}%" for c in caps],
            y=[scenarios[c]["summary"]["min_do"] for c in caps],
            name="Minimum DO (mg/L)", mode="lines+markers+text",
            text=[f"{scenarios[c]['summary']['min_do']:.2f}" for c in caps],
            textposition="top center", yaxis="y2",
            line=dict(color="#FCD34D", width=2.5), marker=dict(size=9)))
        energy.update_layout(
            title="Energy yield against oxygen minimum", height=330, template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(title="Analytic yield (GWh/yr)", range=[0, 30]),
            yaxis2=dict(title="Min DO (mg/L)", overlaying="y", side="right", range=[2.5, 9.0],
                        showgrid=False),
            legend=dict(orientation="h", y=-0.25, font=dict(size=10)), margin=dict(t=40, b=50, r=55))
        st.plotly_chart(energy, width="stretch")

    # ── Sensitivity ────────────────────────────────────────────────────────
    st.markdown("#### 🔬 Sensitivity of the dissolved-oxygen conclusion")
    st.markdown(
        "The response of lake oxygen to panel coverage is **proportional to "
        f"`shade_effectiveness` = {cfg.DO_SHADE_EFFECTIVENESS:.2f}**, which is the only "
        "calibrated coefficient in the ecological model. It is not measured. The table "
        "below varies it and reports the consequence, so the dependence of the headline "
        "conclusion on that single assumption is explicit rather than hidden."
    )
    sens = cached_sensitivity(season, int(seed), ambient, cfg.DO_SHADE_EFFECTIVENESS)
    sens_df = pd.DataFrame(sens)[
        ["factor_label", "shade_effectiveness", "min_do_10pct", "min_do_15pct",
         "min_do_20pct", "min_do_25pct", "15pct_compliant"]
    ]
    st.dataframe(sens_df.round(3), hide_index=True, width="stretch")

    tornado = go.Figure()
    baseline = next(r for r in sens if abs(r["factor"] - 1.0) < 1e-9)
    for col, colour in (("min_do_10pct", "#38BDF8"), ("min_do_15pct", "#22C55E"),
                        ("min_do_20pct", "#EF4444")):
        deviations = [r[col] - baseline[col] for r in sens]
        tornado.add_trace(go.Bar(
            x=deviations, y=[r["factor_label"] for r in sens], orientation="h",
            name=col.replace("min_do_", "").replace("pct", "% coverage"),
            marker_color=colour, text=[f"{r[col]:.2f}" for r in sens], textposition="auto"))
    tornado.add_vline(x=0, line_color="#94A3B8", line_dash="dash")
    tornado.update_layout(
        title="Change in minimum DO relative to the shipped calibration",
        height=280, template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.5)", barmode="group",
        xaxis=dict(title="Δ minimum DO (mg/L) vs. calibrated value"),
        yaxis=dict(title="shade_effectiveness vs. calibrated"), margin=dict(t=40, b=45, l=70, r=15))
    st.plotly_chart(tornado, width="stretch")

    compliant = [r["factor_label"] for r in sens if r["15pct_compliant"]]
    st.info(
        f"**Reading.** At 15% coverage the minimum dissolved oxygen stays above the "
        f"4.0 mg/L threshold for shade_effectiveness multipliers {', '.join(compliant) if compliant else 'none'} "
        f"of the calibrated value. The 15% conclusion is therefore "
        f"{'robust across the tested range' if len(compliant) >= 4 else 'sensitive to this coefficient — see the table'}. "
        "Note that 20% coverage also fails the 15% policy cap outright, independently "
        "of what the oxygen model predicts."
    )

    st.markdown(prov("calibrated", "the coverage/oxygen trade-off"), unsafe_allow_html=True)

    export_rows = []
    for cov, data in scenarios.items():
        for record in data["series"]:
            export_rows.append({
                "Coverage (%)": cov, "Time": record["time_str"],
                "DO (mg/L)": record["do_level"], "Water temp (°C)": record["water_temp_c"],
                "pH": record["ph_level"], "Turbidity (NTU)": record["turbidity_ntu"],
                "Aeration": record["aeration_on"], "Status": record["status"],
                "Capacity (MWp)": round(cfg.capacity_mwp(cov), 3),
            })
    st.download_button("📥 Download scenario comparison CSV",
                       pd.DataFrame(export_rows).to_csv(index=False),
                       f"scenarios_{int(seed)}_{season}.csv", "text/csv", width="stretch")

# ═══════════════════════════════════════════════════════════
# TAB 3 — COST-BENEFIT
# ═══════════════════════════════════════════════════════════

with tab_finance:
    st.markdown("### 💰 Financial model")
    st.markdown(
        "Every self-consumed solar kilowatt-hour is credited **exactly once**, at the "
        "value of what it actually displaces:\n\n"
        "- solar serving **fleet traction** displaces the diesel an all-diesel baseline "
        "would have burned, so it is valued at the implied fuel cost per kWh;\n"
        "- solar serving **municipal load** displaces grid purchase, so it is valued at "
        "the retail tariff plus the open-access surcharge.\n\n"
        "The simulated fleet is **entirely electric**; diesel appears only as the "
        "counterfactual baseline this project replaces, and as such is the dominant "
        "benefit term. Swap-hub capital cost is included. Figures are academic estimates, "
        "not a financial forecast."
    )

    f1, f2, f3 = st.columns(3)
    with f1:
        capex = st.number_input("Floating PV CAPEX (Rs Cr / MWp)", 3.0, 15.0,
                                cfg.CAPEX_PER_MWP_CR, 0.25)
        years = st.slider("Economic lifetime (years)", 10, 30, cfg.SYSTEM_LIFETIME_YEARS)
        pack_cost = st.number_input("Battery pack (Rs Lakh / pack)", 5.0, 60.0,
                                    cfg.BATTERY_PACK_COST_LAKH, 1.0)
        hub_cost = st.number_input("Swap hub civil + electrical (Rs Lakh / hub)", 0.0, 500.0,
                                   cfg.STATION_CAPEX_LAKH, 5.0)
    with f2:
        tariff = st.number_input("Grid tariff (Rs / kWh)", 2.0, 15.0,
                                 cfg.GRID_TARIFF_PER_KWH, 0.25)
        surcharge = st.number_input("Open-access surcharge (Rs / kWh)", 0.0, 3.0,
                                    cfg.OA_SURCHARGE_PER_KWH, 0.02)
        discount = st.slider("Discount rate (%)", 2.0, 16.0,
                             cfg.DISCOUNT_RATE * 100, 0.5) / 100.0
    with f3:
        diesel_price = st.number_input(
            "Displaced diesel price (Rs / litre)", 40.0, 160.0,
            cfg.DIESEL_COST_PER_LITRE, 1.0,
            help="Price of the diesel an all-diesel baseline fleet would have burned. "
                 "The simulated fleet is fully electric, so this sets the value of the "
                 "fuel the project displaces rather than a cost the system incurs.")
        repack_years = st.number_input("Pack replacement interval (years)", 2, 15,
                                       cfg.PACK_REPLACEMENT_YEARS, 1)

    # The priced streams come from the tested helper, so what the dashboard displays and
    # what the test suite verifies are the same arithmetic.
    econ = cached_economics(
        *SCENARIO[:8], float(tariff), float(surcharge), float(diesel_price),
        data_mode=SCENARIO[8], network_mode=SCENARIO[9],
    )
    annual = econ["annual"]

    # ── Capital cost ───────────────────────────────────────────────────────
    capacity_mwp = econ["capacity_mwp"]
    station_packs = sum(int(x["capacity_buses"]) for x in cfg.SWAP_STATIONS)
    num_packs = fleet_size + station_packs
    pv_capex_cr = capacity_mwp * capex
    battery_capex_cr = num_packs * pack_cost / 100.0
    hub_capex_cr = len(cfg.SWAP_STATIONS) * hub_cost / 100.0
    initial_capex_cr = pv_capex_cr + battery_capex_cr + hub_capex_cr
    initial_capex_inr = initial_capex_cr * 1e7

    solar_to_traction_kwh = econ["solar_to_traction_kwh"]
    solar_to_municipal_kwh = econ["solar_to_municipal_kwh"]
    annual_direct_kwh = econ["self_consumed_kwh"]
    diesel_value_per_kwh = econ["diesel_value_per_kwh"]
    retail_value_per_kwh = econ["retail_value_per_kwh"]
    baseline_diesel_inr = econ["baseline_diesel_inr"]
    diesel_litres = econ["diesel_litres_per_year"]
    tariff_revenue_inr = econ["tariff_revenue_inr"]
    diesel_revenue_inr = econ["diesel_revenue_inr"]
    annual_benefit_inr = econ["benefit_inr"]
    traction_solar_fraction = econ["traction_solar_fraction"]
    avoided_fuel_co2_t = econ["avoided_fuel_co2_t"]
    annual_opex_inr = econ["annual_opex_inr"]
    net_cashflow_inr = econ["net_cashflow_inr"]

    payback = initial_capex_inr / net_cashflow_inr if net_cashflow_inr > 0 else float("inf")

    # ── Cash flow ──────────────────────────────────────────────────────────
    repacks = list(range(int(repack_years), years + 1, int(repack_years)))
    repack_inr = battery_capex_cr * 1e7

    npv = -initial_capex_inr
    cumulative = [-initial_capex_inr]
    for yr in range(1, years + 1):
        flow = net_cashflow_inr - (repack_inr if yr in repacks else 0.0)
        npv += flow / (1 + discount) ** yr
        cumulative.append(cumulative[-1] + flow)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Initial CAPEX", f"Rs {initial_capex_cr:.1f} Cr",
              delta=f"PV {pv_capex_cr:.1f} + packs {battery_capex_cr:.1f} "
                     f"+ hubs {hub_capex_cr:.1f}")
    k2.metric("Net annual benefit", f"Rs {net_cashflow_inr / 1e7:.2f} Cr/yr",
              delta=f"payback {payback:.1f} yr" if payback != float("inf") else "no payback")
    k3.metric(f"{years}-year NPV", f"Rs {npv / 1e7:.1f} Cr",
              delta=f"{len(repacks)} repacks, Rs {len(repacks) * battery_capex_cr:.0f} Cr")
    k4.metric("Traction demand met by solar", f"{traction_solar_fraction * 100:.0f}%",
              delta=f"fuel displaced Rs {diesel_revenue_inr / 1e7:.1f} Cr/yr")

    st.markdown(
        f"""
| Annual benefit stream | Energy | Value applied | Amount |
|---|---:|---:|---:|
| Solar to fleet traction (displaces diesel) | {solar_to_traction_kwh / 1e6:.2f} GWh | Rs {diesel_value_per_kwh:.2f}/kWh | Rs {diesel_revenue_inr / 1e7:.2f} Cr |
| Solar to municipal load (displaces grid) | {solar_to_municipal_kwh / 1e6:.2f} GWh | Rs {retail_value_per_kwh:.2f}/kWh ({econ['retail_basis_note']}) | Rs {tariff_revenue_inr / 1e7:.2f} Cr |
| **Total benefit** | **{annual_direct_kwh / 1e6:.2f} GWh** | -- | **Rs {annual_benefit_inr / 1e7:.2f} Cr** |
| Less operations and maintenance | | | -Rs {annual_opex_inr / 1e7:.2f} Cr |
| **Net annual** | | | **Rs {net_cashflow_inr / 1e7:.2f} Cr** |

Each self-consumed kWh appears in exactly one row, so the total cannot exceed
`self-consumed x max(diesel value, retail value)` =
Rs {econ['upper_bound_benefit_inr'] / 1e7:.2f} Cr.
""")
    st.caption(
        f"The diesel baseline this project replaces would have cost "
        f"Rs {baseline_diesel_inr / 1e7:.2f} Cr and emitted "
        f"{econ['baseline_fuel_co2_t']:,.0f} t CO2 per year. The array covers "
        f"{traction_solar_fraction * 100:.0f}% of traction energy, so "
        f"{avoided_fuel_co2_t:,.0f} t of that combustion CO2 is avoided. Combustion CO2 and "
        f"grid-displacement CO2 are different scopes and are never added together."
    )

    cf = go.Figure()
    cf.add_trace(go.Scatter(x=list(range(years + 1)), y=[v / 1e7 for v in cumulative],
                            name="Cumulative net cash flow",
                            line=dict(color="#38BDF8", width=3),
                            fill="tozeroy", fillcolor="rgba(56,189,248,0.08)"))
    for yr in repacks:
        cf.add_vline(x=yr, line_color="#A855F7", line_dash="dot", line_width=1)
    cf.add_hline(y=0, line_dash="dash", line_color="#94A3B8", annotation_text="break-even",
                 annotation_position="bottom right")
    cf.update_layout(
        title=f"{years}-year cumulative cash flow (Rs Cr) - dotted lines are pack repacks",
        height=330, template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.5)", xaxis=dict(title="Operating year"),
        yaxis=dict(title="Rs Crore"), margin=dict(t=40, b=40))
    st.plotly_chart(cf, width="stretch")

    fuel_to_retail = diesel_value_per_kwh / retail_value_per_kwh if retail_value_per_kwh else 0.0
    double_credit_error = annual_direct_kwh * (diesel_value_per_kwh + retail_value_per_kwh) \
        - annual_benefit_inr

    with st.expander("Financial assumptions and provenance"):
        st.markdown(f"""
| Parameter | Value | Basis |
|---|---|---|
| Floating PV CAPEX | Rs {capex:.2f} Cr/MWp | {ASSUMED_BASIS} |
| Grid tariff | Rs {tariff:.2f}/kWh | TANGEDCO commercial rate |
| Open-access surcharge | Rs {surcharge:.2f}/kWh | {ASSUMED_BASIS} |
| Displaced diesel price | Rs {diesel_price:.0f}/L | {ASSUMED_BASIS} - values fuel avoided, not a system cost |
| Baseline distance per bus | {cfg.DIESEL_KM_PER_BUS_PER_DAY:.0f} km/day | TNSTC operational estimate |
| Baseline diesel economy | {cfg.DIESEL_KM_PER_LITRE:.1f} km/L | TNSTC bus average |
| Implied diesel value | Rs {diesel_value_per_kwh:.2f}/kWh | Derived: baseline fuel cost / baseline traction energy |
| Grid CO2 factor | {cfg.grid_emission_factor():.3f} kg/kWh | CEA India (0.727 hybrid, 0.82 simulated) |
| Diesel CO2 factor | {cfg.CO2_DIESEL_KG_PER_LITRE:.2f} kg/L | Stoichiometric |
| Operations and maintenance | Rs {cfg.OPEX_PER_MWP_CR_YEAR:.2f} Cr/MWp/yr | {ASSUMED_BASIS} |
| Battery packs | {num_packs} ({fleet_size} fleet + {station_packs} hub spares) | Pack inventory model |
| Pack unit cost | Rs {pack_cost:.1f} Lakh | {ASSUMED_BASIS} |
| Swap hub CAPEX | Rs {hub_cost:.1f} Lakh x {len(cfg.SWAP_STATIONS)} | {ASSUMED_BASIS} |
| Pack replacement | every {int(repack_years)} yr -> {len(repacks)} repacks | {ASSUMED_BASIS} |

**Double-counting note.** Two earlier revisions overstated the benefit. The first valued
*all* solar output at retail tariff **and** counted 100% of displaced diesel. The second
pro-rated only the diesel stream, which still credited solar serving traction twice, once
at retail (Rs {retail_value_per_kwh:.2f}/kWh) and again against displaced fuel
(Rs {diesel_value_per_kwh:.2f}/kWh). Because the fuel value is {fuel_to_retail:.1f}x the
retail tariff, that error was worth roughly Rs {double_credit_error / 1e7:.0f} Cr per year.
The present model splits self-consumed solar by the load it serves and credits each
kilowatt-hour exactly once.

**The simulated fleet is 100% electric.** No diesel vehicle exists anywhere in the
operational model. Diesel appears only as the counterfactual baseline this project
replaces.
""")
        st.markdown(prov("assumed"), unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# TAB 4 — ANNUAL MODEL & EXPORTS
# ═══════════════════════════════════════════════════════════

with tab_annual:
    st.markdown("### 📅 365-day climatological model")
    st.markdown(
        "Every hour of all 365 days is integrated with an independent daily weather "
        "realisation. Generation and demand are matched **hour by hour**, so "
        "self-consumption, curtailment and grid import are reported separately and "
        "carbon abatement is credited only against self-consumed energy."
    )
    annual = cached_annual(*SCENARIO)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Annual generation", f"{annual['annual_generation_gwh']:.2f} GWh",
              delta=f"analytic estimate {cfg.annual_generation_mwh(coverage_pct) / 1000:.2f} GWh")
    a2.metric("Annual self-consumed", f"{annual['annual_direct_use_gwh']:.2f} GWh",
              delta=f"{ratio(annual['annual_direct_use_gwh'], annual['annual_generation_gwh']) * 100:.0f}%"
                    if annual['annual_generation_gwh'] > 0 else "no array installed")
    a3.metric("Annual CO₂ avoided", f"{annual['annual_co2_tonnes']:,.0f} t",
              delta=f"self-consumed basis; {annual['annual_co2_tonnes_if_all_generation_credited']:,.0f} t if all output were credited")
    a4.metric("Annual tariff saving", f"₹{annual['annual_savings_inr_cr']:.2f} Cr",
              delta=f"grid import {annual['annual_grid_import_gwh']:.1f} GWh remains")

    analytic_gwh = cfg.annual_generation_mwh(coverage_pct) / 1000.0
    combined_load_gwh = (annual["annual_bus_demand_gwh"]
                         + annual["annual_municipal_demand_gwh"])
    reconciliation = (
        f"The hourly model gives {annual['annual_generation_gwh']:.2f} GWh against an "
        f"analytic specific-yield estimate of {analytic_gwh:.2f} GWh, a difference of "
        f"{abs(annual['annual_generation_gwh'] - analytic_gwh) / analytic_gwh * 100:.1f}%. "
        if analytic_gwh > 0 else
        "At 0% coverage no array is installed, so the analytic estimate is also zero and "
        "a percentage difference is undefined. "
    )
    st.info(
        f"**Model reconciliation.** {reconciliation}"
        "The residual is expected: the analytic method uses a measured specific yield, "
        "while the hourly model applies a synthetic monsoon cloud realisation. "
        "**The fleet needs "
        f"{annual['annual_bus_demand_gwh']:.1f} GWh plus {annual['annual_municipal_demand_gwh']:.1f} GWh "
        f"of municipal load = {combined_load_gwh:.1f} GWh, "
        f"against {annual['annual_generation_gwh']:.1f} GWh generated. The array therefore "
        f"covers about {ratio(annual['annual_direct_use_gwh'], combined_load_gwh) * 100:.0f}% "
        "of the combined load on an energy basis, and the remainder, plus all charging "
        "beyond the midday surplus, is grid-supplied.**"
    )

    m1, m2 = st.columns(2)
    with m1:
        bar = go.Figure()
        bar.add_trace(go.Bar(x=months, y=[annual["monthly_generation_mwh"][m] for m in range(1, 13)],
                             name="☀️ Generated", marker_color="#FCD34D"))
        bar.add_trace(go.Bar(x=months, y=[annual["monthly_direct_use_mwh"][m] for m in range(1, 13)],
                             name="⚡ Self-consumed", marker_color="#34D399"))
        bar.add_trace(go.Bar(x=months, y=[annual["monthly_bus_consumption_mwh"][m] for m in range(1, 13)],
                             name="🚌 Fleet demand", marker_color="#38BDF8"))
        bar.add_trace(go.Bar(x=months, y=[annual["monthly_grid_import_mwh"][m] for m in range(1, 13)],
                             name="🔌 Grid import", marker_color="#F87171"))
        bar.update_layout(
            title="Monthly energy balance (MWh)", barmode="group", height=340,
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.5)", xaxis=dict(title="Month"),
            yaxis=dict(title="MWh"), legend=dict(orientation="h", y=-0.25, font=dict(size=9)),
            margin=dict(t=40, b=55))
        st.plotly_chart(bar, width="stretch")

    with m2:
        co2 = go.Figure(go.Bar(
            x=months, y=[annual["monthly_co2_t"][m] for m in range(1, 13)],
            name="CO₂ avoided (t)", marker_color="#34D399",
            text=[f"{annual['monthly_co2_t'][m]:.0f}" for m in range(1, 13)],
            textposition="outside"))
        co2.update_layout(
            title="Monthly CO₂ avoided (t) — self-consumed energy only", height=340,
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(15,23,42,0.5)", yaxis=dict(title="Tonnes"),
            showlegend=False, margin=dict(t=40, b=35))
        st.plotly_chart(co2, width="stretch")

    # ── Solar profile validation ───────────────────────────────────────────
    st.markdown("#### 🔍 Solar resource cross-check")
    st.markdown(
        "The generation model uses an embedded clear-sky profile. This compares its "
        "implied plane-of-array yield against an independent irradiance reference for "
        "the same site, month by month. A ratio consistently below 1 is expected and "
        "healthy: it is the combined cloud and cell-temperature derate."
    )
    validation = cached_solar_validation(*SCENARIO)
    val_df = pd.DataFrame(validation)
    st.dataframe(val_df, hide_index=True, width="stretch")

    vfig = go.Figure()
    vfig.add_trace(go.Bar(x=val_df["Month"], y=val_df["Reference GHI (kWh/m²/day)"],
                          name="Reference irradiance", marker_color="#FCD34D"))
    vfig.add_trace(go.Scatter(x=val_df["Month"], y=val_df["Modelled yield (kWh/kWp/day)"],
                              name="Modelled yield", mode="lines+markers",
                              line=dict(color="#38BDF8", width=2.5), marker=dict(size=8)))
    vfig.update_layout(
        title="Embedded profile versus irradiance reference", height=300,
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.5)", yaxis=dict(title="kWh per m² per day"),
        legend=dict(orientation="h", y=-0.25, font=dict(size=10)), margin=dict(t=40, b=50))
    st.plotly_chart(vfig, width="stretch")
    source_label = val_df["Reference source"].iloc[0]
    ref_kind = "measured" if "NASA" in str(source_label) else "assumed"
    st.markdown(prov(ref_kind, f"Irradiance reference source: {source_label}."),
                unsafe_allow_html=True)

    # ── Exports ────────────────────────────────────────────────────────────
    st.markdown("---")
    st.markdown("#### 📥 Data, manifest and downloads")

    daily = cached_daily_run(*SCENARIO)
    active_ambient = cfg.MONTHLY_TEMP_C[4 if season == "summer" else 7]
    manifest = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "scenario": cfg.scenario_signature(),
        "seed": int(seed),
        "season": season,
        "ambient_temp_c": active_ambient,
        "steps_per_day": STEPS,
        "time_step_hours": 0.5,
        "python": sys.version.split()[0],
        "library_versions": {
            "streamlit": st.__version__,
            "pandas": pd.__version__,
            "plotly": __import__("plotly").__version__,
        },
        "headline": {
            "daily_generation_kwh": daily["summary"]["generation_kwh"],
            "daily_self_consumed_kwh": daily["summary"]["direct_use_kwh"],
            "daily_curtailed_kwh": daily["summary"]["curtailed_kwh"],
            "daily_swaps_total": daily["summary"]["total_swaps"],
            "daily_swaps_solar_funded": daily["summary"]["solar_swaps"],
            "daily_swaps_grid_assisted": daily["summary"]["grid_swaps"],
            "daily_min_do_mg_l": daily["summary"]["min_do"],
            "annual_generation_gwh": annual["annual_generation_gwh"],
            "annual_self_consumed_gwh": annual["annual_direct_use_gwh"],
            "annual_curtailed_gwh": annual["annual_curtailed_gwh"],
            "annual_co2_tonnes_self_consumed": annual["annual_co2_tonnes"],
            "min_do_at_active_coverage_mg_l": cached_scenarios(
                (float(coverage_pct),), season, int(seed), active_ambient,
                cfg.DO_SHADE_EFFECTIVENESS,
            )[float(coverage_pct)]["summary"]["min_do"],
        },
        "provenance": PROVENANCE,
    }

    e1, e2, e3, e4 = st.columns(4)
    e1.download_button("📥 24-hour telemetry CSV", daily["frame"].to_csv(index=False),
                       f"telemetry_{int(coverage_pct)}pct_{season}_seed{seed}.csv",
                       "text/csv", width="stretch")
    e2.download_button("📥 Annual model CSV", pd.DataFrame([{
        "Month": months[i],
        "Generated (MWh)": annual["monthly_generation_mwh"][i + 1],
        "Self-consumed (MWh)": annual["monthly_direct_use_mwh"][i + 1],
        "Curtailed (MWh)": annual["monthly_curtailed_mwh"][i + 1],
        "Grid import (MWh)": annual["monthly_grid_import_mwh"][i + 1],
        "Fleet demand (MWh)": annual["monthly_bus_consumption_mwh"][i + 1],
        "CO₂ avoided (t)": annual["monthly_co2_t"][i + 1],
    } for i in range(12)]).to_csv(index=False),
        f"annual_{int(coverage_pct)}pct_seed{seed}.csv", "text/csv", width="stretch")
    e3.download_button("📄 Run manifest (JSON)", json.dumps(manifest, indent=2, default=str),
                       f"manifest_{int(coverage_pct)}pct_seed{seed}.json", "application/json",
                       width="stretch")
    e4.download_button("📋 Assumptions table", val_df.to_csv(index=False),
                       f"solar_validation_seed{seed}.csv", "text/csv", width="stretch")

    st.dataframe(daily["frame"], width="stretch", height=300)

# ─────────────────────────────────────────────────────────
# PLAYBACK LOOP — the single clock that drives everything
# ─────────────────────────────────────────────────────────

if st.session_state.running and st.session_state.step_idx < STEPS:
    # Guard the divisor: a zero or negative speed would otherwise raise, and the app
    # must still advance the step rather than stall.
    time.sleep(max(0.3, 1.0 / max(0.1, st.session_state.sim_speed)))
    st.session_state.last_state = engine.step()
    st.session_state.step_idx += 1
    st.session_state.history_df = engine.to_dataframe()
    if st.session_state.step_idx >= STEPS:
        st.session_state.running = False
    st.rerun()
elif st.session_state.running and st.session_state.step_idx >= STEPS:
    st.session_state.running = False
