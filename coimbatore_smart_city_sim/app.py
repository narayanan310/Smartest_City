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
from src.visualization.theme import (
    Palette,
    resolve_theme,
    sentinel_html,
    tokens_css,
)

# ``st.components.v1.html`` is the only Streamlit API that can host an isolated
# document from an HTML string (``st.iframe`` takes a URL, not markup), so it stays
# in use despite its deprecation notice. The Streamlit version is pinned.
STEPS = STEPS_PER_DAY

#: Epistemic status tags attached to headline figures, so a reader can tell at a
#: glance which numbers rest on data and which rest on judgement.
#: The ``tag`` strings are plain text because this whole dictionary is exported
#: verbatim into the run manifest, where an emoji would be noise in a JSON file.
#: The corresponding glyph is applied by :func:`prov` when rendering in the UI.
PROVENANCE = {
    "measured": {
        "tag": "Measured",
        "icon": "ruler",
        "note": "Taken from a cited published, regulatory or satellite source.",
    },
    "calibrated": {
        "tag": "Calibrated",
        "icon": "sliders",
        "note": "One coefficient was tuned so the model reproduces the stated result; "
                "it is not an independent measurement.",
    },
    "assumed": {
        "tag": "Assumed",
        "icon": "pencil",
        "note": "An editable engineering assumption, not measured.",
    },
    "modelled": {
        "tag": "Modelled",
        "icon": "cpu",
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
    glyph = icon(entry["icon"], 12)
    return (f'<div class="prov">{glyph}<b>{entry["tag"]}</b> — '
            f'{entry["note"]}{suffix}</div>')


#: Inline SVG icon set. Icons are drawn rather than typed as emoji so they inherit
#: the text colour, scale with the type, and stay legible at every size. Emoji also
#: render inconsistently across platforms and cannot be recoloured.
ICON_PATHS = {
    "solar": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4'
             'm11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4m11.4-11.4 1.4-1.4"/>',
    "bus": '<rect x="4" y="4" width="16" height="13" rx="2"/><path d="M4 11h16M7 17v3'
           'm10-3v3"/><circle cx="8" cy="14" r="1"/><circle cx="16" cy="14" r="1"/>',
    "building": '<path d="M4 21V6l7-3v18M11 21h9V10l-9-3"/><path d="M7 9h.01M7 13h.01'
                'M15 12h.01M15 16h.01"/>',
    "battery": '<rect x="2" y="8" width="16" height="8" rx="2"/><path d="M22 11v2"/>'
               '<path d="M6 10v4m4-4v4"/>',
    "balance": '<path d="M12 4v16M7 20h10M5 8h14M5 8l-2 5a3 3 0 0 0 6 0L7 8m12 0-2 5a3 3 0 0 0 6 0'
               'l-2-5"/>',
    "leaf": '<path d="M4 20c0-8 5-13 16-13 0 9-4 13-11 13-3 0-5-1-5-1z"/><path d="M9 15c2-3 5-5 8-6"/>',
    "gauge": '<path d="M12 21a9 9 0 1 1 9-9"/><path d="M12 12l4-4"/>',
    "map": '<path d="M9 4 3 7v13l6-3 6 3 6-3V4l-6 3-6-3z"/><path d="M9 4v13M15 7v13"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "flask": '<path d="M9 3h6M10 3v6L5 19a2 2 0 0 0 1.7 3h10.6A2 2 0 0 0 19 19l-5-10V3"/>',
    "download": '<path d="M12 3v12m0 0 4-4m-4 4-4-4"/><path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0'
                ' 2-2v-2"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-8h.01"/>',
    "play": '<path d="M6 4l14 8-14 8V4z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "ruler": '<path d="M3 15 15 3l6 6L9 21z"/><path d="M7 11l2 2M11 7l2 2M11 15l2 2"/>',
    "sliders": '<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/>'
               '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/>'
               '<circle cx="18" cy="18" r="2"/>',
    "pencil": '<path d="M4 20h4L20 8l-4-4L4 16v4z"/><path d="M14 6l4 4"/>',
    "cpu": '<rect x="7" y="7" width="10" height="10" rx="1.5"/>'
           '<path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>',
    "grid": '<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',
}


def icon(name: str, size: int = 15, cls: str = "") -> str:
    """Return an inline SVG icon string.

    Injected through ``unsafe_allow_html`` next to the text it labels. Stroke
    inherits ``currentColor``, so an icon takes the colour of whatever contains it
    instead of needing a colour per usage.
    """
    path = ICON_PATHS[name]
    klass = f' class="{cls}"' if cls else ""
    return (
        f'<svg{klass} width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="1.75" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true" style="vertical-align:-2px;'
        f'flex:0 0 auto">{path}</svg>'
    )


def sec(label: str, icon_name: str = "", tone: str = "") -> None:
    """Render a section heading.

    One component for every section, so spacing and type are identical across all
    four tabs instead of drifting with whichever markdown level was used.

    ``tone`` tints the leading icon so a section's glyph carries meaning rather than
    repeating one accent colour everywhere: ``accent`` for the solar/energy subject,
    ``ok`` for ecology, ``muted`` for structural or supporting sections.
    """
    glyph = f"{icon(icon_name, 14)}&nbsp;&nbsp;{label}" if icon_name else label
    tone_class = f"sec tone-{tone or 'accent'}"
    st.markdown(f'<div class="{tone_class}">{glyph}</div>', unsafe_allow_html=True)
# ─────────────────────────────────────────────────────────
# PAGE CONFIGURATION & STYLING
# ─────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Coimbatore Smart City Simulation",
    # The one place an emoji is correct: the browser tab favicon, where the OS
    # renders it and it never sits inline with body text.
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
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
    ss.setdefault("map_step", None)

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
    ss.map_step = None
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
    ss.map_step = None


# ─────────────────────────────────────────────────────────
# SHARED CHART CHROME
# ─────────────────────────────────────────────────────────

#: Shared Plotly chrome. Charts previously each declared their own paper colour,
#: plot colour and margins, so identical plots rendered at slightly different
#: heights and left uneven whitespace beneath the axis titles.
#:
#: Built per call from the active palette rather than frozen at import: Plotly draws
#: client-side and cannot read CSS custom properties, so a hard-coded ``plotly_dark``
#: kept a dark plot rectangle inside a light-themed page.
CHART_HEIGHT = 300
CHART_MARGIN = dict(t=34, b=30, l=8, r=8)
#: Horizontal legend sitting just under the axis, rather than far below it. The
#: previous y=-0.3 pushed the legend outside the plotting area and reserved a band
#: of empty space under every chart.
#:
#: ``entrywidth`` fixes each entry's width so the legend wraps at a row count we can
#: predict and reserve a bottom margin for. Without it the legend wrapped at whatever
#: the browser felt like, which is how it ended up sitting on top of the axis title.
CHART_LEGEND_ENTRY_PX = 96
CHART_LEGEND_ROW_PX = 17
CHART_LEGEND_PER_ROW_WIDE = 9
CHART_LEGEND_PER_ROW_NARROW = 4


def chart(fig: go.Figure, title: str, *, height: int = CHART_HEIGHT,
          legend: bool = False, bottom: int | None = None,
          narrow: bool = False, **kwargs) -> go.Figure:
    """Apply the shared chart chrome, then render the figure.

    One place for the surface colours, type and margins, so every chart in the
    dashboard shares a baseline and none of them ends in a band of dead space.

    ``narrow`` marks a chart rendered in a half-width column, and it is load-bearing
    rather than cosmetic. One fixed margin set cannot serve both widths: at full
    width 52px of left margin is generous, at half width it is narrower than the
    y-axis title and the legend wraps onto a second row that the bottom margin has
    no room for. So the margins here are floors, ``automargin`` lets Plotly grow the
    left and bottom to fit the real tick labels and titles, and the legend's row count
    is computed from its own entry count rather than assumed.
    """
    axis_size = 10 if narrow else 11
    title_size = 11 if narrow else 13
    per_row = (CHART_LEGEND_PER_ROW_NARROW if narrow
               else CHART_LEGEND_PER_ROW_WIDE)
    # Count what the legend will actually show, so a three-entry legend on a narrow
    # chart reserves one row and a four-entry one reserves two.
    entries = (sum(1 for tr in fig.data if getattr(tr, "showlegend", True))
               if legend else 0)
    rows = -(-entries // per_row) if entries else 0
    reserved_bottom = 28 + rows * CHART_LEGEND_ROW_PX

    layout = dict(
        template=C.chart_template,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor=C.chart_plot_bg,
        font=dict(family="Outfit, sans-serif", color=C.text_muted, size=axis_size),
        margin=dict(CHART_MARGIN, b=reserved_bottom, t=title_size + 20),
        title=dict(text=title, font=dict(size=title_size, color=C.chart_title), x=0,
                   xanchor="left", y=0.97, yanchor="top"))
    if legend:
        layout["showlegend"] = True
        layout["legend"] = dict(
            orientation="h", x=0, xanchor="left", y=-0.16, yanchor="top",
            font=dict(size=axis_size), entrywidth=CHART_LEGEND_ENTRY_PX,
            entrywidthmode="pixels")
    if bottom is not None:
        reserved_bottom = bottom
        layout["margin"] = dict(CHART_MARGIN, b=reserved_bottom,
                                t=title_size + 20)
    layout["height"] = height
    # A caller's ``margin`` merges over the shared one instead of colliding with it:
    # passing both would raise "got multiple values for keyword argument".
    if "margin" in kwargs:
        layout["margin"] = {**layout["margin"], **kwargs.pop("margin")}

    # ``automargin`` is what stops a long y-axis title landing on its own tick labels
    # in a narrow column: Plotly measures the axis and grows the margin to fit, which
    # a fixed number cannot do.
    axis_default = dict(gridcolor=C.chart_grid, color=C.text_dim, linecolor=C.line,
                        automargin=True)
    for axis in ("xaxis", "yaxis"):
        spec = dict(axis_default, **(kwargs.pop(axis, None) or {}))
        # Callers pass a plain string title; restate it with themed type so the axis
        # title shrinks with everything else instead of staying a size larger.
        label = spec.get("title")
        if isinstance(label, str) and label:
            spec["title"] = dict(text=label, font=dict(size=axis_size,
                                                        color=C.text_dim))
        kwargs[axis] = spec
    # A twin axis overlays the primary one, so it reserves no margin of its own;
    # giving it automargin would steal width from the axis it sits on.
    if "yaxis2" in kwargs:
        kwargs["yaxis2"] = dict(kwargs.pop("yaxis2") or {})

    fig.update_layout(**layout, **kwargs)
    st.plotly_chart(fig, width="stretch")
    return fig


# Mounted here rather than beside ``set_page_config`` so it sits outside the
# helper regions the regression tests slice and exec standalone. Streamlit applies
# an injected stylesheet wherever it is declared, so position is free.
THEME = resolve_theme()
#: Colour access for this run's theme. Plotly renders client-side and cannot read CSS
#: custom properties, so every chart colour comes from here instead of a literal.
C = Palette(THEME)

#: The stylesheet is a plain string, not an f-string: it is full of CSS braces, which
#: an f-string would try to read as expressions. The generated token block is
#: substituted in afterwards through the ``{tokens}`` placeholder.
STYLESHEET = """

<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');

/* ══════════════════════════════════════════════════════════════════════════════
   DESIGN SYSTEM

   One restrained palette, one type scale, one spacing rhythm. Amber is spent
   on the solar subject and the primary action; green/red appear only where they
   encode status. Everything structural is neutral, so the meaningful colours
   actually mean something.

   The palette itself is generated by :func:`src.visualization.theme.tokens_css`,
   which emits a dark set and a light set from one Python source. That is what lets
   the page follow the Streamlit theme: a hand-written dark-only :root block left the
   cards dark while Streamlit repainted only the widgets it owns.
   ══════════════════════════════════════════════════════════════════════════════ */
:root {
  /* Geometry and rhythm. One radius, one gap scale. These carry no colour, so
     they are declared once and are not part of the generated palette. */
  --r-sm:6px;  --r-md:10px;  --r-lg:14px;
  --sp-1:4px;  --sp-2:8px;  --sp-3:12px;  --sp-4:16px;  --sp-5:24px;
}
{tokens}

html, body, [class*="css"] { font-family:'Outfit',-apple-system,sans-serif !important; }

.stApp { background:var(--bg-app); color:var(--text); }
section[data-testid="stSidebar"] { background:var(--bg-elevated);
    border-right:1px solid var(--line); }
[data-testid="stSidebar"] h3 { font-size:0.95rem; letter-spacing:0.3px; }
/* Sidebar headings carry an inline SVG, so they are emitted as HTML with
   unsafe_allow_html rather than as markdown: markdown escapes the SVG and prints
   it as literal markup text. */
h3.sb-title, h4.sb-title { display:flex; align-items:center; gap:7px; margin:0 0 var(--sp-3);
    font-size:0.72rem; font-weight:700; letter-spacing:0.09em; text-transform:uppercase;
    color:var(--text-muted); }
h3.sb-title { font-size:0.78rem; }

/* ── Type scale ───────────────────────────────────────────────────────────
   Section headings share one component (see `.sec`), so hierarchy is carried by
   size and weight rather than by whichever markdown level each block happened to
   use. */
h2, h3, h4 { letter-spacing:0.1px; color:var(--text); }
div[data-testid="stMarkdownContainer"] h3 { font-size:1.15rem; margin:0 0 var(--sp-2); }
div[data-testid="stMarkdownContainer"] p { color:var(--text-muted); line-height:1.6; }
div[data-testid="stMarkdownContainer"] code { color:var(--accent);
    background:var(--accent-wash); border-radius:var(--r-sm); padding:1px 5px; }

/* Section heading: uppercase label, accent tick, consistent spacing. One class
   instead of scattered emoji-prefixed markdown headings. */
.sec { display:flex; align-items:center; gap:var(--sp-2); margin:var(--sp-5) 0 var(--sp-3);
    font-size:0.72rem; font-weight:700; letter-spacing:0.09em; text-transform:uppercase;
    color:var(--text-muted); }
/* The tick and the icon share a tone, so a heading reads as one coloured unit. */
.sec::before { content:""; width:3px; height:13px; border-radius:2px;
    background:var(--tone,var(--accent)); flex:0 0 auto; }
.sec svg { color:var(--tone,var(--accent)); }
.tone-accent { --tone:var(--accent); }
.tone-ok { --tone:var(--ok); }
.tone-muted { --tone:var(--text-dim); }
.sec:first-child { margin-top:var(--sp-3); }

/* ── Header card ─────────────────────────────────────────────────────────────
   Hero, energy flow and clock live in one card so the page reads as a single
   header rather than four stacked bands with a seam between each. */
.hero-banner { background:linear-gradient(110deg,var(--bg-elevated) 0%,var(--bg-hero) 100%);
    border:1px solid var(--line); border-left:3px solid var(--accent);
    border-radius:var(--r-lg); padding:var(--sp-4) var(--sp-5);
    margin-bottom:var(--sp-4); box-shadow:var(--shadow); }
.hero-top { display:flex; justify-content:space-between; align-items:flex-start;
    flex-wrap:wrap; gap:var(--sp-4); }
.hero-banner h2 { display:flex; align-items:center; gap:9px; margin:0;
    color:var(--text) !important; font-size:1.3rem; font-weight:700;
    letter-spacing:-0.2px; }
.hero-banner h2 svg { color:var(--accent); }
.hero-banner .sub { margin:6px 0 0; color:var(--text-muted); font-size:0.83rem;
    line-height:1.5; }
.hero-banner .meta { text-align:right; color:var(--text-dim); font-size:0.76rem;
    line-height:1.75; }
.hero-banner .meta b { display:block; color:var(--text-muted); font-weight:600;
    font-size:0.64rem; text-transform:uppercase; letter-spacing:0.08em;
    margin-top:var(--sp-2); }

/* ── Energy flow strip ───────────────────────────────────────────────────────
   A left accent rule encodes each stream's identity, so the card fill and border
   stay identical across all five and the row reads as one component. */
.flow-container { display:flex; gap:var(--sp-3); margin-bottom:var(--sp-4);
    flex-wrap:wrap; align-items:stretch; }
/* Inside the header card the strip is separated from the title row by a rule
   rather than by a bottom margin, so the card has one internal gap. */
.flow-container.inset { margin:var(--sp-4) 0 var(--sp-3); }
.flow-card { flex:1; min-width:132px; background:var(--surface);
    border:1px solid var(--line); border-left:3px solid var(--stream,var(--accent));
    border-radius:var(--r-md); padding:var(--sp-3) var(--sp-4); text-align:left;
    transition:background .14s ease, border-color .14s ease; }
.flow-card:hover { background:var(--surface-hover); border-color:var(--line-strong);
    border-left-color:var(--stream,var(--accent)); }
.flow-card h4 { margin:0 0 6px; font-size:0.63rem; color:var(--text-dim);
    text-transform:uppercase; letter-spacing:0.08em; font-weight:600; }
.flow-card .flow-val { font-size:1.2rem; font-weight:600; color:var(--text);
    font-variant-numeric:tabular-nums; line-height:1.15; }
/* The unit is de-emphasised so the number carries the reading. */
.flow-card .u { font-size:0.68rem; font-weight:400; color:var(--text-dim);
    margin-left:2px; }
.flow-arrow { display:flex; align-items:center; color:var(--text-dim); font-size:0.9rem; }
/* Below this width the five cards plus four arrows no longer fit on one line, and a
   separator stranded at the end of a wrapped row reads as a missing element. */
@media (max-width:1150px) {
  .flow-arrow { display:none; }
  .flow-card { min-width:150px; }
}

/* ── Status badge ───────────────────────────────────────────────────────────
   Colour and text both encode the state, so it survives greyscale and
   colour-vision differences. */
.badge { display:flex; flex-direction:column; gap:6px; padding:var(--sp-3) var(--sp-4);
    border-radius:var(--r-md); font-size:0.82rem; background:var(--surface);
    border:1px solid var(--line); border-left:3px solid var(--state,var(--ok)); }
/* Status word and measured reading share a baseline: the word says which state,
   the number says what the lake is actually doing. */
.badge .badge-row { display:flex; align-items:baseline; justify-content:space-between;
    gap:var(--sp-3); }
.badge .badge-head { font-weight:700; letter-spacing:0.09em; font-size:0.7rem;
    text-transform:uppercase; color:var(--state,var(--ok)); }
.badge .badge-value { font-size:1.05rem; font-weight:600; color:var(--text);
    font-variant-numeric:tabular-nums; }
.badge .badge-body { color:var(--text-muted); line-height:1.55; font-size:0.76rem; }
.badge-ok { --state:var(--ok); }
.badge-warn { --state:var(--warn); }
.badge-emerg { --state:var(--danger); }
.badge-crit { --state:var(--violet); }
.badge-emerg .badge-head { animation:pulse-emerg 1.6s ease-in-out infinite; }
@keyframes pulse-emerg { 0%,100%{opacity:1;} 50%{opacity:0.55;} }

/* ── Metric tiles ─────────────────────────────────────────────────────────────
   Streamlit 1.65 renamed this node's test id from `metric-container` to `stMetric`;
   the old selector matched nothing, so these tiles rendered unstyled. */
[data-testid="stMetric"] { background:var(--surface); border:1px solid var(--line);
    border-radius:var(--r-md); padding:var(--sp-3) var(--sp-4); height:100%;
    transition:border-color .14s ease, background .14s ease; }
[data-testid="stMetric"]:hover { border-color:var(--line-strong);
    background:var(--surface-hover); }
[data-testid="stMetricLabel"] { color:var(--text-dim) !important; font-weight:600 !important;
    text-transform:uppercase; letter-spacing:0.06em; font-size:0.66rem !important; }
[data-testid="stMetricValue"] { color:var(--text) !important; font-weight:700 !important;
    font-size:1.45rem !important; font-variant-numeric:tabular-nums;
    letter-spacing:-0.2px; }
[data-testid="stMetricDelta"] { font-size:0.72rem !important; }
[data-testid="stMetricDeltaDescription"] { font-size:0.72rem !important;
    color:var(--text-dim) !important; }

/* ── Buttons ─────────────────────────────────────────────────────────────────
   Primary = the one forward action in a group. Secondary = everything else.
   Equal height and weight within a row so no single button reads as louder. */
.stButton > button, .stDownloadButton > button {
    font-family:inherit; font-weight:600; font-size:0.8rem; border-radius:var(--r-md);
    padding:0 var(--sp-4); min-height:2.35rem; transition:all .14s ease;
    border:1px solid var(--line-strong) !important; background:var(--surface) !important;
    color:var(--text-muted) !important; }
.stButton > button:hover, .stDownloadButton > button:hover {
    background:var(--surface-hover) !important; border-color:var(--accent) !important;
    color:var(--text) !important; transform:translateY(-1px); }
.stButton > button:active, .stDownloadButton > button:active { transform:none; }
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible {
    outline:2px solid var(--accent); outline-offset:2px; }
.stButton > button[kind="primaryButton"], .stDownloadButton > button[kind="primaryButton"] {
    background:var(--accent-strong) !important; border-color:var(--accent-strong) !important;
    color:var(--on-accent) !important; }
.stButton > button[kind="primaryButton"]:hover {
    background:var(--accent) !important; border-color:var(--accent) !important;
    color:var(--on-accent) !important; }

/* ── Sidebar controls ─────────────────────────────────────────────────────── */
[data-testid="stSidebar"] label p { font-size:0.78rem !important; color:var(--text-muted) !important; }
[data-testid="stSidebar"] [data-testid="stSlider"] [role="slider"] { background:var(--accent) !important; }
[data-testid="stSidebar"] hr { border-color:var(--line); margin:var(--sp-4) 0; }

/* ── Tabs: underline on the active tab, with explicit gaps ─────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap:var(--sp-5);
    background:transparent; border-bottom:1px solid var(--line); }
[data-testid="stTabs"] [data-baseweb="tab"] { background:transparent !important;
    font-size:0.85rem; font-weight:600; color:var(--text-dim);
    padding:var(--sp-3) 0; border-bottom:2px solid transparent; }
[data-testid="stTabs"] [data-baseweb="tab"]:hover { color:var(--text) !important;
    background:transparent !important; }
[data-testid="stTabs"] [aria-selected="true"] { color:var(--accent) !important;
    border-bottom-color:var(--accent); }
[data-testid="stTabs"] [data-baseweb="tab-highlight"],
[data-testid="stTabs"] [data-baseweb="tab-border"] { background-color:transparent; }

/* ── Data tables ───────────────────────────────────────────────────────────── */
[data-testid="stDataFrame"] { border:1px solid var(--line); border-radius:var(--r-md);
    overflow:hidden; }
[data-testid="stDataFrame"] *:focus { outline:none; }

/* ── Expander, callouts, progress ──────────────────────────────────────────── */
div[data-testid="stExpander"] details { border:1px solid var(--line);
    border-radius:var(--r-md); background:var(--surface); }
div[data-testid="stExpander"] summary { font-size:0.82rem; font-weight:600;
    color:var(--text-muted); }
div[data-testid="stExpander"] summary:hover { color:var(--accent); }
div[data-testid="stAlert"] { border-radius:var(--r-md); border:1px solid var(--line);
    background:var(--surface); color:var(--text-muted); font-size:0.84rem; }
div[data-testid="stAlert"] strong { color:var(--text); }
[data-testid="stProgress"] > div > div > div { background:var(--accent) !important; }
[data-testid="stProgress"] [data-testid="stProgressBarBackground"] { background:var(--surface-sunken) !important; }

/* ── Clock row ────────────────────────────────────────────────────────────── */
.clock-row { display:flex; justify-content:space-between; align-items:center;
    flex-wrap:wrap; gap:var(--sp-3); margin-top:var(--sp-4); padding-top:var(--sp-3);
    border-top:1px solid var(--line); }
.clock-row .time { font-size:1.2rem; font-weight:700; color:var(--accent);
    font-variant-numeric:tabular-nums; margin-left:var(--sp-2); }
.clock-row .meta, .clock-row .phase { color:var(--text-muted); font-size:0.78rem; }
.clock-row .phase { text-align:right; }
.clock-row .lbl, .clock-row .phase .lbl { color:var(--text-dim); font-size:0.63rem;
    text-transform:uppercase; letter-spacing:0.08em; font-weight:600; }

/* ── Water quality panel ──────────────────────────────────────────────────── */
.wq { background:var(--surface); border:1px solid var(--line);
    border-radius:var(--r-md); padding:var(--sp-3) var(--sp-4); font-size:0.78rem;
    line-height:1.95; }
.wq .wq-head { color:var(--text); font-weight:600; display:block;
    margin-bottom:var(--sp-1); font-size:0.68rem; text-transform:uppercase;
    letter-spacing:0.07em; }
.wq b { color:var(--text); font-weight:600; font-variant-numeric:tabular-nums; }

/* ── Day-complete banner ─────────────────────────────────────────────────── */
.day-banner { background:var(--surface); border:1px solid var(--ok);
    border-radius:var(--r-lg); padding:var(--sp-4) var(--sp-5);
    margin-bottom:var(--sp-4); }
.day-banner .done { display:flex; align-items:center; gap:7px; font-size:1rem;
    font-weight:700; color:var(--ok); }
.day-banner .sub { color:var(--text-dim); font-size:0.78rem; }
.kpi { text-align:left; }
.kpi-row { display:flex; gap:var(--sp-5); flex-wrap:wrap; }
.kpi .k-lbl { font-size:0.62rem; color:var(--text-dim); text-transform:uppercase;
    letter-spacing:0.07em; }
.kpi .k-val { font-size:1rem; font-weight:600; font-variant-numeric:tabular-nums; }

/* A short standing caveat on the current tab, set apart from body prose. */
.note { background:var(--surface-sunken); border:1px solid var(--line);
    border-left:2px solid var(--text-dim); border-radius:var(--r-sm);
    padding:var(--sp-3) var(--sp-4); margin:var(--sp-3) 0;
    font-size:0.78rem; line-height:1.65; color:var(--text-muted); }
.note b { color:var(--text); font-weight:600; }

.prov { font-size:0.7rem; color:var(--text-dim); margin:var(--sp-1) 0 var(--sp-3);
    line-height:1.5; }
.prov b { color:var(--text-muted); font-weight:600; }
.prov svg { vertical-align:-2px; margin-right:4px; }

/* Quiet divider, left-aligned with the content. */
.sect-rule { border:0; border-top:1px solid var(--line); margin:var(--sp-5) 0 0 0; }

.leaflet-container { border-radius:var(--r-md); }

/* Respect a reader who has asked for less motion. */
@media (prefers-reduced-motion: reduce) {
  .badge-emerg .badge-head, .day-banner { animation:none; }
  .stButton > button:hover, .stDownloadButton > button:hover { transform:none; }
  .flow-card:hover { background:var(--surface); }
}
</style>
"""

st.markdown(
    STYLESHEET.replace("{tokens}", tokens_css()),
    unsafe_allow_html=True)
# Publishes the resolved theme so the `:has()` rules above can select the matching
# palette. Deliberately outside the <style> block: it is the element those rules
# match on, not a rule itself.
st.markdown(sentinel_html(THEME), unsafe_allow_html=True)

init_session()
cfg: Config = st.session_state.config
engine: SimulationEngine = st.session_state.engine

# ─────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(f'<h3 class="sb-title">{icon("gauge", 16)}&nbsp; Scenario controls</h3>',
                unsafe_allow_html=True)

    stress_mode = st.toggle(
        "Stress test (coverage above 15% cap)",
        value=st.session_state._stress,
        help="Adds non-compliant coverage options. Above roughly 40% in summer the "
             "model crosses the 3.0 mg/L emergency threshold and the mechanical "
             "aerator engages, which demonstrates the mitigation path.")
    coverage_options = cfg.SCENARIO_COVERAGE_OPTIONS + (
        cfg.STRESS_COVERAGE_OPTIONS if stress_mode else []
    )
    coverage_pct = st.select_slider(
        "Lake surface coverage (%)",
        options=coverage_options,
        value=st.session_state._coverage if st.session_state._coverage in coverage_options else 15.0,
        help="Percentage of Ukkadam Lake surface covered by PV. 15% is the ecological policy cap.")
    fleet_size = st.slider("Fleet size", 50, 500, st.session_state._fleet, step=10,
                           help="Allocated across the four corridors in proportion to their design share.")
    battery_capacity = st.slider("Battery pack capacity (kWh)", 150, 450,
                                 int(st.session_state._battery), step=10,
                                 help="Larger packs drain more slowly in percentage terms per step.")
    traffic_factor = st.select_slider(
        "Traffic / passenger load", options=[0.8, 1.0, 1.25, 1.5],
        value=st.session_state._traffic,
        format_func=lambda v: {0.8: "Light (0.8×)", 1.0: "Normal (1.0×)",
                               1.25: "Heavy (1.25×)", 1.5: "Congested (1.5×)"}[v],
        help="Scales traction energy and reduces corridor speed. Daily consumption of "
             f"{cfg.DAILY_CONSUMPTION_KWH:.0f} kWh per bus applies at 1.0×.")
    cloud_multiplier = st.slider("Cloud cover multiplier", 0.5, 1.5,
                                 st.session_state._cloud, step=0.25,
                                 help="Scales the seeded daily cloud schedule.")
    charger_bays = st.slider("Charger bays per hub", 1, 12, st.session_state._bays, step=1,
                             help="Limits how many depleted packs charge concurrently at each hub.")
    season = st.radio("Season", ["summer", "monsoon"],
                      index=0 if st.session_state._season == "summer" else 1,
                      format_func=lambda s: "Summer (high solar)" if s == "summer"
                      else "Monsoon (heavy cloud)")
    seed = st.number_input("Random seed", min_value=1, max_value=9999,
                           value=int(st.session_state._seed), step=1,
                           help="Fixes the weather and mixing-noise realisations so a run is reproducible.")

    st.markdown(f'<h4 class="sb-title">{icon("sliders", 14)}&nbsp; Data &amp; calibration</h4>',
                unsafe_allow_html=True)
    data_mode_choice = st.radio(
        "Data Source Mode",
        ["simulated", "hybrid"],
        index=0 if st.session_state.get("_data_mode", "simulated") == "simulated" else 1,
        format_func=lambda m: "Generated — every input synthesised" if m == "simulated"
        else "Sourced — published inputs plus generated remainder",
        help="Generated (simulated): every input is synthesised by this project — "
             "an embedded clear-sky solar curve, a seeded synthetic cloud schedule, "
             "a flat ₹8.00/kWh tariff and the 0.82 kg CO₂/kWh CEA baseline constant. "
             "Nothing here is measured.\n\n"
             "Sourced (hybrid): the inputs that have a published source are read "
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
        format_func=lambda n: "Expanded network (6 corridors, 6 hubs)" if n == "expanded"
        else "Core network (4 corridors, 4 hubs)",
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
| **Simulated** (fully generated) | 0 of {len(rows)} | {len(rows)} | all fallbacks |
| **Hybrid** (gathered + generated) | **{live_authentic}** | {counts['generated']} | {counts['assumption']} |
""")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    speed_map = {"0.5×": 0.5, "1×": 1.0, "2×": 2.0, "5×": 5.0, "10×": 10.0}
    st.session_state.sim_speed = speed_map[
        st.select_slider("Playback speed", options=list(speed_map), value="2×")
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
    st.markdown(f'<h4 class="sb-title">{icon("play", 14)}&nbsp; Playback</h4>',
                unsafe_allow_html=True)
    b1, b2, b3 = st.columns(3)
    if b1.button("Play", width="stretch", type="primary"):
        st.session_state.running = True
    if b2.button("Pause", width="stretch"):
        st.session_state.running = False
    if b3.button("Reset", width="stretch"):
        restart_engine(cfg, coverage_pct, season, int(seed))
        st.rerun()

    progress = min(1.0, st.session_state.step_idx / STEPS)
    sim_h = int((st.session_state.step_idx * 0.5) % 24)
    sim_m = 30 if ((st.session_state.step_idx * 0.5) % 1.0) >= 0.5 else 0
    st.progress(progress, text=f"Simulation clock {sim_h:02d}:{sim_m:02d} / 23:30 "
                               f"({st.session_state.step_idx}/{STEPS} steps)")

    # Reference material, not controls: collapsed so the scenario sliders and the
    # playback controls stay visible without scrolling.
    with st.expander("Scenario definition & data sources"):
        sec("Scenario definition", "info", "muted")
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
        sec("Data sources", "info", "muted")
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

gen, muni = state.generation_kw, state.municipal_load_kw
bus_kw, swap_kw = state.demand_kw, state.swapping_load_kw
surplus, grid = state.surplus_kw, state.grid_import_kw
balance_colour = "var(--ok)" if surplus >= 0 else "var(--danger)"
balance_text = (f"+{surplus:,.0f} kW surplus" if surplus >= 0 else f"{grid:,.0f} kW from grid")

h_int = int(state.hour)
am_pm = "AM" if h_int < 12 else "PM"
civic = 12 if h_int % 12 == 0 else h_int % 12
minute = 30 if (state.hour - h_int) > 0.25 else 0
time_label = f"{civic:02d}:{minute:02d} {am_pm}"

# Hero, energy flow, clock and phase live in one card. They were four separate
# full-width blocks, each with its own margin, so the page read as four bands with
# a seam between them rather than as a single header. The scrolling ticker is gone:
# it duplicated the same figures as the flow strip and the telemetry row, and its
# motion competed with them.
hero_left, hero_right = st.columns([5, 1])
with hero_left:
    st.markdown(f"""
    <div class="hero-banner">
      <div class="hero-top">
        <div style="min-width:0;">
          <h2>{icon('solar', 20)}&nbsp; Coimbatore Smart City Simulation</h2>
          <p class="sub">Floating solar on Ukkadam Lake · electric bus fleet with battery
          swapping · lake ecological reserve</p>
        </div>
        <div class="meta">
          <b>Course</b> BCSE355L Smart Cities · VIT Vellore
          <b>Team</b> Aditya Saxena (24BCE1633) &amp; Narayanan Subramanian (24BCE1699)
        </div>
      </div>
      <div class="flow-container inset">
        <div class="flow-card" style="--stream:var(--c-solar);">
          <h4>{icon('solar', 12)} Floating solar</h4>
          <div class="flow-val">{gen:,.0f} <span class="u">kW</span></div></div>
        <div class="flow-arrow">→</div>
        <div class="flow-card" style="--stream:var(--c-muni);">
          <h4>{icon('building', 12)} Municipal</h4>
          <div class="flow-val">{muni:,.0f} <span class="u">kW</span></div></div>
        <div class="flow-arrow">→</div>
        <div class="flow-card" style="--stream:var(--c-traction);">
          <h4>{icon('bus', 12)} Traction</h4>
          <div class="flow-val">{bus_kw:,.0f} <span class="u">kW</span></div></div>
        <div class="flow-arrow">→</div>
        <div class="flow-card" style="--stream:var(--c-charge);">
          <h4>{icon('battery', 12)} Pack charging</h4>
          <div class="flow-val">{swap_kw:,.0f} <span class="u">kW</span></div></div>
        <div class="flow-arrow">→</div>
        <div class="flow-card" style="--stream:{balance_colour};">
          <h4>{icon('balance', 12)} Balance</h4>
          <div class="flow-val" style="color:{balance_colour};">{balance_text}</div></div>
      </div>
      <div class="clock-row">
        <div>
          <span class="lbl">Simulation clock</span>
          <span class="time">{time_label}</span>
          <span class="meta">· Representative day · {cfg.capacity_mwp(coverage_pct):.1f} MWp ·
          {fleet_size} buses</span>
        </div>
        <div class="phase"><span class="lbl">Phase</span>
        <span>{state.phase_name}</span></div>
      </div>
    </div>
    """, unsafe_allow_html=True)
with hero_right:
    if st.button("Step +30 min", width="stretch", type="primary"):
        if st.session_state.step_idx >= STEPS:
            restart_engine(cfg, coverage_pct, season, int(seed))
        st.session_state.last_state = engine.step()
        st.session_state.step_idx += 1
        st.session_state.history_df = engine.to_dataframe()
        st.rerun()

# ─────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────

tab_live, tab_scenarios, tab_finance, tab_annual = st.tabs([
    "Live simulation", "Coverage & sensitivity",
    "Cost-benefit", "Annual model & exports",
])

# ═══════════════════════════════════════════════════════════
# TAB 1 — LIVE SIMULATION
# ═══════════════════════════════════════════════════════════

with tab_live:
    df_hist = st.session_state.history_df

    if st.session_state.step_idx >= STEPS and not df_hist.empty:
        daily = cached_daily_run(*SCENARIO)
        s = daily["summary"]
        # The threshold state is spelled out in words, not left to a glyph or a
        # colour, so it survives greyscale and colour-vision differences.
        do_ok = s["min_do"] >= 4.0
        do_colour = "var(--ok)" if do_ok else "var(--danger)"
        do_state = "within threshold" if do_ok else "below threshold"
        st.markdown(f"""
        <div class="day-banner">
          <div style="display:flex;justify-content:space-between;align-items:center;
                      flex-wrap:wrap;gap:var(--sp-4);">
            <div>
              <div class="done">{icon('check', 16)} Representative day complete</div>
              <div class="sub">All 48 half-hour steps integrated · seed {int(seed)} ·
                reproducible</div>
            </div>
            <div class="kpi-row">
              <div class="kpi"><div class="k-lbl">Generated</div>
                <div class="k-val">{s['generation_kwh']:,.0f} kWh</div></div>
              <div class="kpi"><div class="k-lbl">Self-consumed</div>
                <div class="k-val">{s['direct_use_kwh']:,.0f} kWh</div></div>
              <div class="kpi"><div class="k-lbl">Swaps</div>
                <div class="k-val">{s['total_swaps']}</div></div>
              <div class="kpi"><div class="k-lbl">Min DO</div>
                <div class="k-val" style="color:{do_colour};">{s['min_do']:.2f}
                  <span style="font-size:0.62rem;font-weight:400;color:var(--text-dim);">
                  {do_state}</span></div></div>
              <div class="kpi"><div class="k-lbl">CO₂ avoided</div>
                <div class="k-val">{s['co2_kg'] / 1000:.2f} t</div></div>
            </div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    # ── Map: the centrepiece, full width ──────────────────────────────────────
    sec("Coimbatore transit network and Ukkadam Lake", "map", "muted")
    st.caption(
        "The map renders the step driven by the Streamlit clock. There is no independent "
        "animation timer, so the map and the metric cards cannot drift apart."
    )
    map_step = max(0, min(STEPS - 1, st.session_state.step_idx - 1))
    # The theme is part of the cache key, not just an input to the build: switching
    # Streamlit's theme re-runs the script but does not change the simulation step,
    # so keying on the step alone would leave the embed painted in the old palette.
    map_key = (map_step, THEME)
    if st.session_state.map_step != map_key:
        with st.spinner("Preparing map trajectory…"):
            frames = cached_trajectory(*SCENARIO)
            st.session_state.map_html = build_interactive_map_html(
                cfg, frames, initial_step=map_step, theme=THEME,
                fleet_size=fleet_size, max_kw=cfg.max_generation_kw(coverage_pct))
        st.session_state.map_step = map_key
    # The opening view is fitted to the whole ~17 km network, so the lake itself is
    # necessarily small. This toggle trades vertical space for a close read of the
    # array; it changes the frame height only, never the simulation step.
    map_gutter, map_toggle = st.columns([6, 1])
    with map_toggle:
        map_expanded = st.toggle(
            "Expand map", key="map_expanded",
            help="Tall map frame for inspecting the array and the four corridors closely.")
    components.html(
        st.session_state.map_html, height=880 if map_expanded else 640, scrolling=False)

    # ── Real-time telemetry: a horizontal row directly under the map ───────────
    sec("Real-time telemetry", "gauge", "ok")
    badge = {"OK": "badge-ok", "WARNING": "badge-warn",
             "EMERGENCY": "badge-emerg", "CRITICAL": "badge-crit"}.get(
                 state.eco_status, "badge-ok")
    telem_left, telem_right = st.columns([1, 2])
    with telem_left:
        st.markdown(
            f'<div class="badge {badge}">'
            f'<div class="badge-row">'
            f'<span class="badge-head">{state.eco_status}</span>'
            f'<span class="badge-value">{state.do_level:.2f} mg/L</span></div>'
            f'<span class="badge-body">{state.eco_message}</span></div>',
            unsafe_allow_html=True)
    with telem_right:
        st.markdown(f"""
        <div class="wq">
          <span class="wq-head">Lake water quality</span>
          <div>Temperature <b>{state.water_temp_c:.1f} °C</b> · pH
            <b>{state.ph_level:.2f}</b> · Turbidity <b>{state.turbidity_ntu:.1f} NTU</b> ·
            Cloud <b>{state.cloud_cover:.0f}%</b> · Air <b>{state.ambient_temp_c:.1f} °C</b></div>
          <div>Aerator
            <b style="color:{'var(--danger)' if state.aeration_on else 'var(--ok)'};">
            {'ACTIVE' if state.aeration_on else 'standby'}</b> · Aeration events
            <b>{state.aeration_events}</b> · Fleet distance <b>{state.fleet_km:,.0f} km</b></div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(prov("modelled"), unsafe_allow_html=True)

    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Solar generation", f"{gen:,.0f} kW",
              delta=f"{cfg.max_generation_kw(coverage_pct) / 1000:.1f} MWp installed")
    m2.metric("Traction demand", f"{bus_kw:,.0f} kW",
              delta=f"{state.active_buses} buses in service")
    m3.metric("Swaps today", f"{state.total_swaps}",
              delta=f"{state.solar_swaps} solar · {state.grid_swaps} grid")
    m4.metric("Dissolved oxygen", f"{state.do_level:.2f} mg/L",
              delta="at or above 4.0 mg/L" if state.do_level >= 4.0 else "below 4.0 mg/L",
              delta_color="normal" if state.do_level >= 4.0 else "inverse")
    m5.metric("CO₂ avoided", f"{state.cumulative_co2_avoided_kg / 1000:.2f} t",
              delta="self-consumed energy only")
    m6.metric("Cost saved", f"₹{state.cumulative_cost_saved_inr / 1000:,.1f}k",
              delta=f"fleet SOC {state.avg_soc:.0f}%")

    # ── Detail tables, full width, each travelling with its heading ────────────
    sec("Route operations board", "bus", "muted")
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
    st.dataframe(pd.DataFrame(route_rows), hide_index=True, width="stretch",
                 column_config={
                     "Corridor": st.column_config.TextColumn(width="small"),
                     "Route": st.column_config.TextColumn(width="medium"),
                     "Length (km)": st.column_config.NumberColumn(format="%.1f"),
                     "Avg SOC": st.column_config.TextColumn(width="small"),
                     "Inbound / outbound": st.column_config.TextColumn(width="small"),
                     "Energy used (kWh)": st.column_config.NumberColumn(format="%.1f"),
                 })

    assigned_by_route = {rid: [b for b in fleet if b.route_id == rid] for rid in cfg.ROUTES}
    route_soc = {
        rid: sum(b.soc for b in buses) / len(buses)
        for rid, buses in assigned_by_route.items() if buses
    }
    chart(
        go.Figure(go.Bar(
            x=list(route_soc), y=list(route_soc.values()),
            marker_color=[cfg.ROUTES[rid]["color"] for rid in route_soc],
            text=[f"{v:.0f}%" for v in route_soc.values()],
            textposition="inside", textfont=dict(size=10, color=C.bg_app),
            hovertemplate="%{x}<br>Avg SOC %{y:.1f}%<extra></extra>",
            showlegend=False)),
        "Average state of charge by corridor", height=240, showlegend=False, bargap=0.45,
        xaxis=dict(title="Corridor", showgrid=False),
        yaxis=dict(title="Avg SOC (%)", range=[0, 100]),
        margin=dict(t=40, b=44, l=52, r=18))

    sec("Fleet condition and stop monitor", "search", "muted")
    st.caption("Sorted by operational risk. Diverting buses are steering toward the "
               "nearest point on their own corridor that passes a swap hub.")
    f1, f2 = st.columns(2)
    selected_route = f1.selectbox("Filter by corridor", ["All", *cfg.ROUTES])
    # Built from the same labels ``Bus.condition`` returns, so renaming a status in
    # settings cannot silently filter every row out of the table.
    selected_status = f2.multiselect("Filter by condition", list(BUS_CONDITION_LABELS))
    rows = fleet
    if selected_route != "All":
        rows = [b for b in rows if b.route_id == selected_route]
    if selected_status:
        rows = [b for b in rows if b.condition in selected_status]
    risky = sorted(rows, key=lambda b: (b.soc >= 30, b.soc, b.bus_id))[:60]
    # The table is capped at 60 rows, so state how many are shown out of how many
    # matched. Without this a truncated table looks identical to a complete one.
    st.caption(
        f"Showing the {len(risky)} lowest-state-of-charge buses of {len(rows)} "
        f"matching the filters."
    )
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
    } for b in risky]), hide_index=True, width="stretch",
        column_config={
            "Bus": st.column_config.NumberColumn(width="small"),
            "Corridor": st.column_config.TextColumn(width="small"),
            "Condition": st.column_config.TextColumn(width="small"),
            "SOC": st.column_config.TextColumn(width="small"),
            "Stored": st.column_config.TextColumn(width="small"),
            "Current stop": st.column_config.TextColumn(width="small"),
            "Next stop": st.column_config.TextColumn(width="small"),
            "Speed": st.column_config.TextColumn(width="small"),
            "Nearest hub": st.column_config.TextColumn(width="medium"),
            "Hub distance": st.column_config.TextColumn(width="small"),
            "Energy (kWh)": st.column_config.NumberColumn(format="%.1f"),
            "Swap funded by": st.column_config.TextColumn(width="small"),
        })

    sec("Swap hub inventory and service zones", "battery", "muted")
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
    } for station in cfg.SWAP_STATIONS]), hide_index=True, width="stretch",
        column_config={
            "Hub": st.column_config.TextColumn(width="medium"),
            "Rated per bay": st.column_config.TextColumn(width="small"),
            "Service radius": st.column_config.TextColumn(width="small"),
        })

    # ── Charts ────────────────────────────────────────────────────────────────
    sec("Diurnal dynamics and energy balance", "chart", "accent")
    plot_df = df_hist if not df_hist.empty else cached_daily_run(*SCENARIO)["frame"]

    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        # An empty history means no step has been integrated yet; the traces are
        # omitted rather than plotted as zero, which would read as "no generation".
        if not plot_df.empty:
            fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["Generation (kW)"],
                                     name="Generation", line=dict(color=C.accent, width=2.2),
                                     fill="tozeroy", fillcolor=C.accent_wash))
            fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["Total Demand (kW)"],
                                     name="Total load", line=dict(color=C.danger, width=1.8)))
            fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["Solar Curtailed (kW)"],
                                     name="Curtailed", line=dict(color=C.text_dim, width=1.4,
                                     dash="dot")))
        fig.add_vline(x=state.hour, line_width=1.5, line_dash="dash", line_color=C.accent,
                      annotation_text=time_label, annotation_position="top")
        chart(
            fig, "Generation, load and curtailment (kW)", legend=True,
            xaxis=dict(title="Hour of day", dtick=3, range=[0, 24]),
            yaxis=dict(title="kW", rangemode="tozero"),
            narrow=True,
        )

    with c2:
        socs = [b.soc for b in fleet]
        # Bucket labels carry the threshold as text so the chart does not depend on
        # colour alone to distinguish them.
        buckets = {
            "≥ 60%": sum(1 for v in socs if v >= 60),
            "30–60%": sum(1 for v in socs if 30 <= v < 60),
            "< 30%": sum(1 for v in socs if v < 30),
            "Swapping": sum(1 for b in fleet if b.state == "swapping"),
            "Diverting": sum(1 for b in fleet if b.state == "heading_to_swap"),
        }
        chart(
            go.Figure(go.Pie(
                labels=list(buckets), values=list(buckets.values()),
                marker=dict(colors=[C.ok, C.accent, C.danger, C.c_muni, C.violet],
                            line=dict(color=C.bg_elevated, width=2)),
                hole=0.5, textinfo="label+value", textposition="outside",
                textfont=dict(size=11), sort=False, direction="clockwise",
                hovertemplate="%{label}: %{value} buses<extra></extra>")),
            f"Fleet state of charge ({fleet_size} buses · avg {state.avg_soc:.0f}%)",
            margin=dict(t=34, b=6, l=68, r=68), showlegend=False,
            narrow=True,
        )

    c3, c4 = st.columns(2)
    with c3:
        do_fig = go.Figure()
        do_fig.add_hrect(y0=0, y1=3.0, fillcolor="rgba(180,35,24,0.10)", line_width=0,
                         annotation_text="Emergency (<3.0)", annotation_position="top left")
        do_fig.add_hrect(y0=3.0, y1=4.0, fillcolor=C.accent_wash, line_width=0,
                         annotation_text="Warning (<4.0)", annotation_position="top left")
        do_fig.add_hline(y=4.0, line_dash="dot", line_color=C.accent,
                         annotation_text="4.0 mg/L threshold", annotation_position="bottom right")
        if not plot_df.empty:
            do_fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["DO Level (mg/L)"],
                                        name="Dissolved oxygen", line=dict(color=C.c_muni, width=2.2),
                                        fill="tozeroy", fillcolor="rgba(31,111,178,0.12)"))
        do_fig.add_vline(x=state.hour, line_width=1.5, line_dash="dash", line_color=C.accent)
        chart(
            do_fig, "Lake dissolved oxygen (mg/L)", showlegend=False,
            xaxis=dict(title="Hour of day", dtick=3, range=[0, 24]),
            yaxis=dict(title="mg/L", range=[2.0, 9.0], rangemode="tozero"),
            narrow=True,
        )

    with c4:
        swap_fig = go.Figure()
        if not plot_df.empty:
            swap_fig.add_trace(go.Bar(x=plot_df["Hour"], y=plot_df["Solar-Funded Swaps"],
                                      name="Solar-funded", marker_color=C.ok))
            swap_fig.add_trace(go.Bar(x=plot_df["Hour"], y=plot_df["Grid-Assisted Swaps"],
                                      name="Grid-assisted", marker_color=C.violet))
            swap_fig.add_trace(go.Scatter(x=plot_df["Hour"], y=plot_df["Swapping Load (kW)"],
                                          name="Charging load (kW)",
                                          line=dict(color=C.accent, width=1.8), yaxis="y2"))
        chart(
            swap_fig, "Swaps by funding source, locked to solar surplus",
            legend=True, barmode="stack",
            xaxis=dict(title="Hour of day", dtick=3, range=[0, 24]),
            yaxis=dict(title="Swaps", rangemode="tozero"),
            yaxis2=dict(title="kW", overlaying="y", side="right", showgrid=False,
                        rangemode="tozero"),
            margin=dict(r=46),
            narrow=True,
        )

    if st.session_state.step_idx >= STEPS and not plot_df.empty:
        sec("Full-day result", "check", "ok")
        s = cached_daily_run(*SCENARIO)["summary"]
        # ``ratio`` guards the 0% coverage case, where generation is legitimately zero
        # and dividing would raise rather than report "no array installed".
        self_consumed_pct = ratio(s["direct_use_kwh"], s["generation_kwh"]) * 100
        curtailed_pct = ratio(s["curtailed_kwh"], s["generation_kwh"]) * 100
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Solar generated", f"{s['generation_kwh']:,.0f} kWh",
                  delta=f"peak {s['peak_generation_kw']:,.0f} kW")
        r2.metric("Self-consumed", f"{s['direct_use_kwh']:,.0f} kWh",
                  delta=f"{self_consumed_pct:.0f}% of output")
        r3.metric("Curtailed", f"{s['curtailed_kwh']:,.0f} kWh",
                  delta=f"{curtailed_pct:.0f}% of output")
        r4.metric("DO range", f"{s['min_do']:.2f} – {s['max_do']:.2f} mg/L",
                  delta="at or above 4.0 mg/L" if s["min_do"] >= 4.0 else "below 4.0 mg/L")
        st.info(
            "**Interpretation.** Solar-funded swaps cluster in the midday surplus window; "
            "grid-assisted swaps occur in the evening deficit. That split is the honest "
            "result: at this array size the surplus funds "
            f"{s['solar_swaps']} of {s['total_swaps']} daily swaps, and the remaining "
            f"{s['grid_swaps']} must come from the grid. Curtailed energy is excluded from "
            "both the carbon and tariff figures, because curtailed output displaces nothing."
        )

# ═══════════════════════════════════════════════════════════
# TAB 2 — COVERAGE SCENARIOS & SENSITIVITY
# ═══════════════════════════════════════════════════════════

with tab_scenarios:
    st.markdown("### Lake coverage: ecological and energy trade-off")
    st.markdown(
        f"Each coverage is evaluated at the **diurnal steady state** for the "
        f"{season} climatology, discarding a 10-day spin-up. Both this table and the "
        f"live trace integrate at the same 30-minute resolution, so they agree to "
        f"within {0.05:.2f} mg/L."
    )
    ambient = cfg.MONTHLY_TEMP_C[4 if season == "summer" else 7]
    scenarios = cached_scenarios(
        tuple(float(c) for c in coverage_options), season, int(seed), ambient,
        cfg.DO_SHADE_EFFECTIVENESS)

    # The charts come before the table because the trade-off is a shape, not a list
    # of numbers: coverage buys energy monotonically and costs oxygen monotonically,
    # and the pair of curves below shows where the two cross. The table then confirms
    # the crossing with the exact values.
    st.info(
        "**How to read these two charts.** The bars are annual energy yield, which "
        "rises with coverage by construction. The line is the worst-hour dissolved "
        "oxygen, which falls with coverage as panel shade removes the lake's "
        "photosynthesis. Where the line crosses the 4.0 mg/L threshold is the real "
        "decision boundary: coverage beyond it buys kWh at the cost of an ecological "
        "reserve, which is what the 15% cap exists to prevent."
    )

    left, right = st.columns(2)
    with left:
        ladder = go.Figure()
        # Line colour carries the same meaning as the policy verdict: red once the
        # cap is breached, green while oxygen stays above threshold, amber in between.
        colours = {c: (C.danger if c > cfg.ECOLOGICAL_CAP_PCT else
                       C.ok if scenarios[c]["summary"]["min_do"] >= 4.0 else C.accent)
                   for c in scenarios}
        for cov, data in scenarios.items():
            series = data["series"]
            ladder.add_trace(go.Scatter(
                x=[r["hour"] for r in series], y=[r["do_level"] for r in series],
                name=f"{cov:.0f}%", line=dict(color=colours[cov], width=2.5)))
        ladder.add_hline(y=4.0, line_dash="dot", line_color=C.accent,
                         annotation_text="4.0 mg/L minimum")
        ladder.add_hline(y=3.0, line_dash="dot", line_color=C.danger,
                         annotation_text="3.0 mg/L aeration trigger")
        chart(
            ladder, "Steady-state dissolved oxygen by coverage", height=330, legend=True,
            xaxis=dict(title="Hour of day", dtick=3),
            yaxis=dict(title="mg/L", range=[2.4, 9.0]),
            margin=dict(r=18),
            narrow=True,
        )

    with right:
        caps = [c for c in scenarios]
        energy = go.Figure()
        energy.add_trace(go.Bar(
            x=[f"{c:.0f}%" for c in caps],
            y=[cfg.annual_generation_mwh(c) / 1000.0 for c in caps],
            name="Analytic yield (GWh/yr)", marker_color=C.c_muni))
        energy.add_trace(go.Scatter(
            x=[f"{c:.0f}%" for c in caps],
            y=[scenarios[c]["summary"]["min_do"] for c in caps],
            name="Minimum DO (mg/L)", mode="lines+markers+text",
            text=[f"{scenarios[c]['summary']['min_do']:.2f}" for c in caps],
            textposition="top center", yaxis="y2",
            line=dict(color=C.accent, width=2.5), marker=dict(size=9)))
        energy.add_hline(y=4.0, line_dash="dot", line_color=C.accent, line_width=1,
                         yref="y2")
        chart(
            energy, "Energy yield against oxygen minimum", height=330, legend=True,
            xaxis=dict(title="Lake surface coverage", showgrid=False),
            yaxis=dict(title="Analytic yield (GWh/yr)", range=[0, 30]),
            yaxis2=dict(title="Min DO (mg/L)", overlaying="y", side="right",
                        range=[2.5, 9.0], showgrid=False),
            margin=dict(r=55),
            narrow=True,
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
            "Policy": "Exceeds cap" if over_cap else "Within cap",
        })
    st.dataframe(
        pd.DataFrame(rows), hide_index=True, width="stretch",
        column_config={
            "Coverage": st.column_config.TextColumn(width="small"),
            "Capacity": st.column_config.TextColumn(width="small"),
            "Min DO (mg/L)": st.column_config.NumberColumn(format="%.2f"),
            "Max DO (mg/L)": st.column_config.NumberColumn(format="%.2f"),
            "DO status": st.column_config.TextColumn(width="small"),
            "Policy": st.column_config.TextColumn(width="small"),
        })
    st.markdown(
        f'<div class="note"><b>Cap.</b> {cfg.ECOLOGICAL_CAP_PCT:.0f}% surface coverage is '
        f'the ecological ceiling applied throughout. Coverage above it is computed and '
        f'shown so the cost of the cap is visible, not because it is permissible.</div>',
        unsafe_allow_html=True)

    # ── Sensitivity ────────────────────────────────────────────────────────
    sec("Sensitivity of the dissolved-oxygen conclusion", "flask", "ok")
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
    for col, colour in (("min_do_10pct", C.c_muni), ("min_do_15pct", C.ok),
                        ("min_do_20pct", C.danger)):
        deviations = [r[col] - baseline[col] for r in sens]
        tornado.add_trace(go.Bar(
            x=deviations, y=[r["factor_label"] for r in sens], orientation="h",
            name=col.replace("min_do_", "").replace("pct", "% coverage"),
            marker_color=colour, text=[f"{r[col]:.2f}" for r in sens], textposition="auto"))
    tornado.add_vline(x=0, line_color=C.text_dim, line_dash="dash")
    chart(
        tornado, "Change in minimum DO relative to the shipped calibration",
        height=280, barmode="group",
        xaxis=dict(title="Δ minimum DO (mg/L) vs. calibrated value"),
        yaxis=dict(title="shade_effectiveness vs. calibrated", showgrid=False),
        margin=dict(t=40, b=45, l=70, r=15))

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
    st.download_button("Download scenario CSV",
                       pd.DataFrame(export_rows).to_csv(index=False),
                       f"scenarios_{int(seed)}_{season}.csv", "text/csv", width="stretch")

# ═══════════════════════════════════════════════════════════
# TAB 3 — COST-BENEFIT
# ═══════════════════════════════════════════════════════════

with tab_finance:
    st.markdown("### Financial model")
    st.info(
        "**Each self-consumed kilowatt-hour is credited exactly once, at the value of "
        "what it actually displaces.** Solar serving **fleet traction** displaces the "
        "diesel an all-diesel baseline would have burned, so it is valued at the implied "
        "fuel cost per kWh. Solar serving **municipal load** displaces grid purchase, so "
        "it is valued at the retail tariff plus the open-access surcharge. The simulated "
        "fleet is entirely electric; diesel appears only as the counterfactual baseline "
        "this project replaces, and as such it is the dominant benefit term. Swap-hub "
        "capital cost is included. Figures are academic estimates, not a financial "
        "forecast."
    )

    sec("Editable assumptions", "sliders", "muted")
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

    # The magnitude of the earlier double-counting error is stated on the tab rather
    # than buried in the assumptions expander: it is the reason a reader should trust
    # the headline figure, so hiding it would remove the evidence for the claim.
    fuel_to_retail = diesel_value_per_kwh / retail_value_per_kwh if retail_value_per_kwh else 0.0
    double_credit_error = annual_direct_kwh * (diesel_value_per_kwh + retail_value_per_kwh) \
        - annual_benefit_inr

    sec("Headline economics", "chart", "accent")
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

    sec("Where the benefit comes from", "balance", "muted")
    st.markdown(
        f"""
| Annual benefit stream | Energy | Value applied | Amount |
|---|---:|---:|---:|
| Solar to fleet traction (displaces diesel) | {solar_to_traction_kwh / 1e6:.2f} GWh | Rs {diesel_value_per_kwh:.2f}/kWh | Rs {diesel_revenue_inr / 1e7:.2f} Cr |
| Solar to municipal load (displaces grid) | {solar_to_municipal_kwh / 1e6:.2f} GWh | Rs {retail_value_per_kwh:.2f}/kWh ({econ['retail_basis_note']}) | Rs {tariff_revenue_inr / 1e7:.2f} Cr |
| **Total benefit** | **{annual_direct_kwh / 1e6:.2f} GWh** | -- | **Rs {annual_benefit_inr / 1e7:.2f} Cr** |
| Less operations and maintenance | | | -Rs {annual_opex_inr / 1e7:.2f} Cr |
| **Net annual** | | | **Rs {net_cashflow_inr / 1e7:.2f} Cr** |
""")
    st.caption(
        f"Each self-consumed kWh appears in exactly one row, so the total cannot exceed "
        f"`self-consumed x max(diesel value, retail value)` = "
        f"Rs {econ['upper_bound_benefit_inr'] / 1e7:.2f} Cr. "
        f"The diesel baseline this project replaces would have cost "
        f"Rs {baseline_diesel_inr / 1e7:.2f} Cr and emitted "
        f"{econ['baseline_fuel_co2_t']:,.0f} t CO2 per year. The array covers "
        f"{traction_solar_fraction * 100:.0f}% of traction energy, so "
        f"{avoided_fuel_co2_t:,.0f} t of that combustion CO2 is avoided. Combustion CO2 and "
        f"grid-displacement CO2 are different scopes and are never added together."
    )

    sec("Cumulative cash flow", "chart", "muted")
    cf = go.Figure()
    cf.add_trace(go.Scatter(x=list(range(years + 1)), y=[v / 1e7 for v in cumulative],
                            name="Cumulative net cash flow",
                            line=dict(color=C.c_muni, width=3),
                            fill="tozeroy", fillcolor="rgba(31,111,178,0.12)"))
    for yr in repacks:
        cf.add_vline(x=yr, line_color=C.violet, line_dash="dot", line_width=1)
    cf.add_hline(y=0, line_dash="dash", line_color=C.text_dim, annotation_text="break-even",
                 annotation_position="bottom right")
    chart(
        cf, f"{years}-year cumulative cash flow (Rs Cr) — dotted lines are pack repacks",
        height=330,
        xaxis=dict(title="Operating year"),
        yaxis=dict(title="Rs Crore"))

    st.markdown(
        f'<div class="note"><b>On double-counting.</b> Two earlier revisions overstated '
        f'the benefit. The first valued <i>all</i> solar output at retail tariff <b>and</b> '
        f'counted 100% of displaced diesel. The second pro-rated only the diesel stream, '
        f'which still credited solar serving traction twice — once at retail '
        f'(Rs {retail_value_per_kwh:.2f}/kWh) and again against displaced fuel '
        f'(Rs {diesel_value_per_kwh:.2f}/kWh). Because the fuel value is '
        f'{fuel_to_retail:.1f}x the retail tariff, that error was worth roughly '
        f'Rs {double_credit_error / 1e7:.0f} Cr per year. The present model splits '
        f'self-consumed solar by the load it serves and credits each kilowatt-hour '
        f'exactly once.</div>',
        unsafe_allow_html=True)

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

**The simulated fleet is 100% electric.** No diesel vehicle exists anywhere in the
operational model. Diesel appears only as the counterfactual baseline this project
replaces.
""")
        st.markdown(prov("assumed"), unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════
# TAB 4 — ANNUAL MODEL & EXPORTS
# ═══════════════════════════════════════════════════════════

with tab_annual:
    st.markdown("### 365-day climatological model")
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

    sec("Monthly balance", "chart", "accent")
    m1, m2 = st.columns(2)
    with m1:
        bar = go.Figure()
        bar.add_trace(go.Bar(x=months, y=[annual["monthly_generation_mwh"][m] for m in range(1, 13)],
                             name="Generated", marker_color=C.accent))
        bar.add_trace(go.Bar(x=months, y=[annual["monthly_direct_use_mwh"][m] for m in range(1, 13)],
                             name="Self-consumed", marker_color=C.ok))
        bar.add_trace(go.Bar(x=months, y=[annual["monthly_bus_consumption_mwh"][m] for m in range(1, 13)],
                             name="Fleet demand", marker_color=C.c_muni))
        bar.add_trace(go.Bar(x=months, y=[annual["monthly_grid_import_mwh"][m] for m in range(1, 13)],
                             name="Grid import", marker_color=C.danger))
        chart(
            bar, "Monthly energy balance (MWh)", height=340, barmode="group",
            legend=True,
            xaxis=dict(title="Month", showgrid=False),
            yaxis=dict(title="MWh"),
            narrow=True,
        )

    with m2:
        co2 = go.Figure(go.Bar(
            x=months, y=[annual["monthly_co2_t"][m] for m in range(1, 13)],
            name="CO₂ avoided (t)", marker_color=C.ok,
            text=[f"{annual['monthly_co2_t'][m]:.0f}" for m in range(1, 13)],
            textposition="outside"))
        chart(
            co2, "Monthly CO₂ avoided (t) — self-consumed energy only", height=340,
            showlegend=False,
            xaxis=dict(title="Month", showgrid=False),
            yaxis=dict(title="Tonnes"),
            margin=dict(b=52),
            narrow=True,
        )

    # ── Solar profile validation ───────────────────────────────────────────
    sec("Solar resource cross-check", "solar", "accent")
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
                          name="Reference irradiance", marker_color=C.accent))
    vfig.add_trace(go.Scatter(x=val_df["Month"], y=val_df["Modelled yield (kWh/kWp/day)"],
                              name="Modelled yield", mode="lines+markers",
                              line=dict(color=C.c_muni, width=2.5), marker=dict(size=8)))
    chart(
        vfig, "Embedded profile versus irradiance reference", height=300, legend=True,
        xaxis=dict(title="Month", showgrid=False),
        yaxis=dict(title="kWh per m² per day"))
    source_label = val_df["Reference source"].iloc[0]
    ref_kind = "measured" if "NASA" in str(source_label) else "assumed"
    st.markdown(prov(ref_kind, f"Irradiance reference source: {source_label}."),
                unsafe_allow_html=True)

    # ── Exports ────────────────────────────────────────────────────────────
    sec("Data, manifest & downloads", "download", "muted")

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
                cfg.DO_SHADE_EFFECTIVENESS)[float(coverage_pct)]["summary"]["min_do"],
        },
        "provenance": PROVENANCE,
    }

    e1, e2, e3, e4 = st.columns(4)
    e1.download_button("24-hour telemetry CSV", daily["frame"].to_csv(index=False),
                       f"telemetry_{int(coverage_pct)}pct_{season}_seed{seed}.csv",
                       "text/csv", width="stretch")
    e2.download_button("Annual model CSV", pd.DataFrame([{
        "Month": months[i],
        "Generated (MWh)": annual["monthly_generation_mwh"][i + 1],
        "Self-consumed (MWh)": annual["monthly_direct_use_mwh"][i + 1],
        "Curtailed (MWh)": annual["monthly_curtailed_mwh"][i + 1],
        "Grid import (MWh)": annual["monthly_grid_import_mwh"][i + 1],
        "Fleet demand (MWh)": annual["monthly_bus_consumption_mwh"][i + 1],
        "CO₂ avoided (t)": annual["monthly_co2_t"][i + 1],
    } for i in range(12)]).to_csv(index=False),
        f"annual_{int(coverage_pct)}pct_seed{seed}.csv", "text/csv", width="stretch")
    e3.download_button("Run manifest (JSON)", json.dumps(manifest, indent=2, default=str),
                       f"manifest_{int(coverage_pct)}pct_seed{seed}.json", "application/json",
                       width="stretch")
    e4.download_button("Assumptions table", val_df.to_csv(index=False),
                       f"solar_validation_seed{seed}.csv", "text/csv", width="stretch")

    # The raw frame is 47 columns wide. Open, it pushed the export buttons and every
    # other control on the tab below the fold, so it is collapsed by default and the
    # same data is one download button above.
    with st.expander(f"Raw 24-hour telemetry frame ({daily['frame'].shape[1]} columns)"):
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
