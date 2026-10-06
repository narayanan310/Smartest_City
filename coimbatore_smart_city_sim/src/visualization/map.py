"""
map.py — Self-contained Leaflet map component for the Coimbatore simulation.

Design notes
------------
**Why hand-written Leaflet HTML rather than Folium.** The map must be rebuilt on
every simulation step so that it stays in lockstep with the Python clock, but a
rebuilt Folium figure re-instantiates the whole Leaflet map and visibly flickers.
Emitting the HTML directly lets the trajectory be cached as compact data and the
browser to draw a single stable map. ``st.components.v1.html`` is still the only
Streamlit API that hosts an isolated document from an HTML string (``st.iframe``
accepts a URL, not markup), so it remains in use despite its deprecation notice;
the Streamlit version is pinned in ``requirements.txt``.

**Single clock.** Earlier revisions ran a ``setInterval`` inside the iframe *and* a
``time.sleep`` loop in Python, each advancing one 30-minute step. Two independent
timers drifted apart as soon as a Streamlit rerun took longer than the interval, so
the map clock silently disagreed with the metric cards. The animation loop in this
module has been removed: Python owns the clock and passes the authoritative step in.

**Compact payload.** The trajectory is emitted columnar with quantised integers
(latitude and longitude to 1e-4, about 11 m, and state of charge to 0.1%) instead
of as one dictionary per bus per frame. That is roughly a 15x reduction, from about
4.1 MB to 0.3 MB for 260 buses over 48 frames, which is what makes per-step rebuilds
affordable.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from src.config.settings import (
    BUS_STATE_LABELS,
    SOC_COLOUR_HIGH_PCT,
    SOC_COLOUR_LOW_PCT,
    SOC_COLOUR_MID_PCT,
    SOC_COLOUR_SCALE,
    SOC_SWAP_PCT,
    Config,
)
from src.models.bus import Bus, soc_status_label

#: Coordinate quantisation used in the browser payload.
COORD_SCALE = 1e4      # ~11 m resolution, far finer than the map zoom level
SOC_SCALE = 10         # 0.1 % state-of-charge resolution

#: Single-character bus state codes used in the payload, decoded in the browser.
STATE_CODES = {"depot": "d", "running": "r", "swapping": "s", "heading_to_swap": "h"}
STATE_NAMES = {v: k for k, v in STATE_CODES.items()}


def _fmt_pct(value: float) -> str:
    """Format a SOC percentage for the legend, dropping a pointless ``.0``."""
    return f"{value:g}"


def soc_legend_html() -> str:
    """Build the state-of-charge legend from the configured band boundaries.

    Generated rather than written out by hand: the labels are derived from
    :data:`SOC_COLOUR_LOW_PCT` / :data:`SOC_COLOUR_MID_PCT` / :data:`SOC_COLOUR_HIGH_PCT`,
    which are the same numbers ``Bus.soc_color`` and the JS colour scale use. The
    hand-written legend previously disagreed with the code at exactly 20%, 40% and 70%.
    """
    low = SOC_COLOUR_LOW_PCT
    mid = SOC_COLOUR_MID_PCT
    high = SOC_COLOUR_HIGH_PCT
    # The band range and its name are separate spans so the range keeps a fixed
    # width and the names align in a column. Keeping the text in a shrinkable span
    # is what stops the longest label overflowing the legend box.
    labels = (
        (f"&lt;{_fmt_pct(low)}%", "critical"),
        (f"{_fmt_pct(low)}–{_fmt_pct(mid)}%", "swap priority"),
        (f"{_fmt_pct(mid)}–{_fmt_pct(high)}%", "moderate"),
        (f"{_fmt_pct(high)}%+", "healthy"),
    )
    # Colours run high to low in the legend, matching the original reading order.
    pairs = list(zip(reversed(SOC_COLOUR_SCALE), reversed(labels), strict=True))
    rows = "".join(
        f'<span><span class="swatch" style="background:{colour}"></span>'
        f'<span class="band">{band}</span><span class="word">{word}</span></span>'
        for colour, (band, word) in pairs
    )
    # One wrapper of flex rows rather than a grid of cells. The previous markup was
    # a grid whose container also declared two columns, so consecutive rows flowed
    # into the second column and the four corridors read as two merged pairs.
    return f'<span class="legend-rows">{rows}</span>'


def solar_style(gen_kw: float, max_kw: float, hour: float) -> Dict[str, Any]:
    """Return fill colour, opacity, border and label for the array at this instant."""
    if hour < 6.0 or hour >= 19.0 or gen_kw <= 0.0:
        return {"fill": "#1C2833", "opacity": 0.35, "border": "#34495E",
                "label": f"Dormant (night, {gen_kw:,.0f} kW)"}
    ratio = (gen_kw / max_kw) if max_kw > 0 else 0.0
    if ratio >= 0.75:
        return {"fill": "#27AE60", "opacity": 0.85, "border": "#2ECC71",
                "label": f"Peak generation ({gen_kw:,.0f} kW)"}
    if ratio >= 0.40:
        return {"fill": "#F1C40F", "opacity": 0.72, "border": "#F39C12",
                "label": f"High output ({gen_kw:,.0f} kW)"}
    if ratio >= 0.15:
        return {"fill": "#E67E22", "opacity": 0.60, "border": "#D35400",
                "label": f"Moderate output ({gen_kw:,.0f} kW)"}
    return {"fill": "#BDC3C7", "opacity": 0.45, "border": "#7F8C8D",
            "label": f"Sunrise / sunset ({gen_kw:,.0f} kW)"}


def build_payload(
    config: Config,
    frames: Sequence[Dict[str, Any]],
    fleet_size: int,
    max_kw: float,
) -> Dict[str, Any]:
    """Build the columnar browser payload for a precomputed trajectory.

    Args:
        config:    Active configuration, for static geometry.
        frames:    Per-step dictionaries from the trajectory builder. Each must carry
                   ``hour``, ``gen_kw``, ``demand_kw``, ``tot_swaps``, ``solar_swaps``,
                   ``grid_swaps``, ``do_level``, ``coverage_pct``, ``phase``,
                   ``activity`` and ``buses``.
        fleet_size: Number of buses, used to pad frames if the fleet changed size.
        max_kw:    Installed capacity, used to scale the array's radiance.

    Returns:
        A JSON-serialisable dictionary of quantised integer arrays plus the static
        geometry the browser needs.
    """
    bus_count = len(frames[0]["buses"]) if frames else 0

    lat: List[List[int]] = []
    lon: List[List[int]] = []
    soc: List[List[int]] = []
    state: List[List[str]] = []
    condition: List[List[str]] = []
    stops: List[List[str]] = []

    for frame in frames:
        buses = frame["buses"]
        # Pad or truncate so every frame has an identical length; the browser indexes
        # buses by position, so a ragged payload would shift marker identities.
        padded = list(buses[:bus_count])
        while len(padded) < bus_count:
            padded.append(buses[-1] if buses else {"lat": 0, "lon": 0, "soc": 0, "state": "depot"})
        lat.append([int(round(b["lat"] * COORD_SCALE)) for b in padded])
        lon.append([int(round(b["lon"] * COORD_SCALE)) for b in padded])
        soc.append([max(0, min(1000, int(round(b["soc"] * SOC_SCALE)))) for b in padded])
        state.append([STATE_CODES.get(b["state"], "d") for b in padded])
        condition.append([b.get("condition", "")[:28] for b in padded])
        stops.append([f"{b.get('current_stop', '')}→{b.get('next_stop', '')}"[:44] for b in padded])

    return {
        "steps": len(frames),
        "busCount": bus_count,
        "hour": [round(f["hour"], 2) for f in frames],
        "genKw": [round(f["gen_kw"], 0) for f in frames],
        "demandKw": [round(f["demand_kw"], 0) for f in frames],
        "totSwaps": [int(f["tot_swaps"]) for f in frames],
        "solarSwaps": [int(f.get("solar_swaps", 0)) for f in frames],
        "gridSwaps": [int(f.get("grid_swaps", 0)) for f in frames],
        "doLevel": [round(f["do_level"], 2) for f in frames],
        "coverage": [round(f["coverage_pct"], 1) for f in frames],
        "phase": [f.get("phase", "") for f in frames],
        "activity": [f.get("activity", {}) for f in frames],
        "lat": lat, "lon": lon, "soc": soc, "state": state,
        "condition": condition, "stops": stops,
        "busId": [b["id"] for b in frames[0]["buses"]] if frames else [],
        "busRoute": [b.get("route", "") for b in frames[0]["buses"]] if frames else [],
        "busEnergy": [round(b.get("energy_kwh", 0.0), 1) for b in frames[0]["buses"]] if frames else [],
        "busStation": [b.get("nearest_station", "") for b in frames[0]["buses"]] if frames else [],
        "busStationKm": [round(b.get("station_distance_km", 0.0), 2) for b in frames[0]["buses"]] if frames else [],
        "fleetSize": fleet_size,
        "maxKw": round(max_kw, 1),
        "lakePolygon": config.LAKE_POLYGON,
        "solarPolygon": config.SOLAR_ARRAY_POLYGON,
        "stations": config.SWAP_STATIONS,
        "buildings": config.MUNICIPAL_BUILDINGS,
        "routes": {
            rid: {
                "name": info["name"],
                "color": info["color"],
                "waypoints": info["waypoints"],
                "stopNames": info.get("stop_names", []),
                "stopIndices": info.get("stop_waypoint_indices", []),
                "buses": info.get("buses_allocated", 0),
            }
            for rid, info in config.ROUTES.items()
        },
        "chargeRateKw": config.CHARGE_RATE_KW,
        # So the popup can convert a SOC percentage into real stored energy
        # instead of assuming the default pack size.
        "batteryKwh": config.BATTERY_CAPACITY_KWH,
    }


def build_interactive_map_html(
    config: Config,
    frames: Sequence[Dict[str, Any]],
    initial_step: int = 0,
    fleet_size: int | None = None,
    max_kw: float | None = None,
    theme: str = "dark",
) -> str:
    """Render the complete Leaflet document for the given trajectory.

    The returned HTML is a single self-contained component that renders exactly the
    step given by ``initial_step``. It does not animate on its own; the caller
    re-renders it as the simulation advances.

    ``theme`` selects the palette the embed paints itself with. The document is an
    iframe, so it cannot read the host page's theme; the caller passes the active
    Streamlit theme down so the map tracks the rest of the dashboard instead of
    staying dark on a light page.
    """
    if not frames:
        raise ValueError("build_interactive_map_html requires at least one trajectory frame")

    resolved_fleet = fleet_size if fleet_size is not None else config.NUM_BUSES
    resolved_max_kw = max_kw if max_kw is not None else config.max_generation_kw(
        config.DEFAULT_COVERAGE_PCT
    )
    payload = build_payload(config, frames, resolved_fleet, resolved_max_kw)
    data_json = json.dumps(payload, separators=(",", ":"))
    step = max(0, min(len(frames) - 1, int(initial_step)))

    (south, west), (north, east) = config.map_bounds()
    lake_lat, lake_lon, lake_zoom = config.lake_view()

    return _TEMPLATE.format(
        data=data_json,
        initial_step=step,
        theme="light" if str(theme).lower() == "light" else "dark",
        # Flattened: str.format cannot resolve ``mapping['key']`` inside a field
        # name, so each palette token is passed under its own name.
        **{f"map_{theme_name}_{k}": v for theme_name, pv in MAP_THEMES.items()
           for k, v in pv.items()},
        south=south,
        west=west,
        north=north,
        east=east,
        pad_px=config.MAP_VIEW_PADDING_PX,
        zoom_min=config.MAP_ZOOM_MIN,
        zoom_max=config.MAP_ZOOM_MAX,
        lake_lat=lake_lat,
        lake_lon=lake_lon,
        lake_zoom=lake_zoom,
        coord_scale=COORD_SCALE,
        soc_scale=SOC_SCALE,
        soc_low=_fmt_pct(SOC_COLOUR_LOW_PCT),
        soc_mid=_fmt_pct(SOC_COLOUR_MID_PCT),
        soc_high=_fmt_pct(SOC_COLOUR_HIGH_PCT),
        soc_swap=_fmt_pct(SOC_SWAP_PCT),
        tile_url=config.tile_provider()["url"],
        tile_attribution=config.tile_provider()["attribution"],
        tile_max_zoom=config.tile_provider()["max_zoom"],
        colour_0=SOC_COLOUR_SCALE[0],
        colour_1=SOC_COLOUR_SCALE[1],
        colour_2=SOC_COLOUR_SCALE[2],
        colour_3=SOC_COLOUR_SCALE[3],
        soc_legend=soc_legend_html(),
        state_names=json.dumps(STATE_NAMES),
        capacity_mwp=config.capacity_mwp(config.DEFAULT_COVERAGE_PCT),
        charge_rate=config.CHARGE_RATE_KW,
    )


def build_frame_trajectory(engine, config: Config) -> List[Dict[str, Any]]:
    """Run an isolated full-day simulation and return per-step frames for the map.

    This deliberately uses a **separate** engine instance built from the same
    configuration. Stepping or resetting the live session engine here would desync the
    map from the sidebar clock, the metric cards and the charts, which all read the
    live engine.
    """
    from src.simulation.engine import SimulationEngine

    sim = SimulationEngine(
        config, coverage_pct=engine.coverage_pct, season=engine.season, seed=engine.seed
    )
    frames: List[Dict[str, Any]] = []
    for _ in range(48):
        state = sim.step()
        buses = []
        for bus in sim.get_fleet():
            station, distance_km = bus.nearest_station(config.SWAP_STATIONS)
            speed = 0.0 if bus.state in ("swapping", "depot") else (
                16.0 + (bus.bus_id % 6)
            ) / max(0.5, config.TRAFFIC_INTENSITY_FACTOR)
            buses.append({
                "id": bus.bus_id,
                "lat": bus.lat,
                "lon": bus.lon,
                "soc": bus.soc,
                "state": bus.state,
                "route": bus.route_id,
                "condition": _bus_condition(bus),
                "current_stop": bus.current_stop,
                "next_stop": bus.next_stop,
                "energy_kwh": bus.energy_consumed_kwh,
                "nearest_station": station.get("name", ""),
                "station_distance_km": distance_km,
                "speed_kmph": speed,
            })
        frames.append({
            "hour": state.hour,
            "time": state.time_str,
            "gen_kw": state.generation_kw,
            "demand_kw": state.total_demand_kw,
            "tot_swaps": state.total_swaps,
            "solar_swaps": state.solar_swaps,
            "grid_swaps": state.grid_swaps,
            "do_level": state.do_level,
            "coverage_pct": state.coverage_pct,
            "phase": state.phase_name,
            "activity": state.station_activity,
            "buses": buses,
        })
    return frames


def _bus_condition(bus: Bus) -> str:
    """Read ``Bus.condition`` defensively.

    Streamlit keeps engine and bus objects in session state across code reloads, so an
    instance created before ``condition`` existed as a property would raise
    ``AttributeError``. Fall back to the same state and state-of-charge logic.
    """
    try:
        return bus.condition
    except AttributeError:
        state = getattr(bus, "state", "")
        soc = getattr(bus, "soc", 100.0)
        if state == "swapping":
            return BUS_STATE_LABELS[0]
        if state == "depot":
            return BUS_STATE_LABELS[1]
        if state == "heading_to_swap":
            return BUS_STATE_LABELS[2]
        # Same classifier ``Bus.condition`` uses, so the fallback cannot drift.
        return soc_status_label(soc)


#: Embed palettes, keyed by the theme the dashboard is currently showing. The map is
#: an iframe, so it cannot read the host theme from CSS; the caller passes it in.
#: The dark ramp mirrors the dashboard's :root tokens so the two agree, and the light
#: ramp is chosen for contrast on white rather than derived by inverting the dark one
#: (an inverted amber loses most of its luminance and stops reading as the accent).
MAP_THEMES = {
    "dark": {
        "bg": "#14161A",
        "surface": "#1B1E24",
        "line": "rgba(255,255,255,0.11)",
        "text": "#E6EBF2",
        "muted": "#9AA7B8",
        "dim": "#5B6B7E",
        "accent": "#F5B942",
        "accent_soft": "rgba(245,185,66,0.12)",
        "on_accent": "#1A1200",
        "shadow": "0 8px 26px rgba(0,0,0,0.45)",
    },
    "light": {
        "bg": "#F4F6F9",
        "surface": "#FFFFFF",
        "line": "rgba(15,23,42,0.14)",
        "text": "#10161F",
        "muted": "#475569",
        "dim": "#64748B",
        # 5.4:1 on --bg, so the clock and the button label both clear 4.5:1.
        "accent": "#8A5A08",
        "accent_soft": "rgba(180,120,10,0.12)",
        # Near-black, matching the dashboard: the light amber fill is far too light
        # to carry a white label (3.8:1), while black on it reaches 4.8:1.
        "on_accent": "#10161F",
        "shadow": "0 8px 22px rgba(15,23,42,0.14)",
    },
}

_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="theme-{theme}">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  /* ── Palette ────────────────────────────────────────────────────────────────
     Both themes are declared here and selected by the class on <html>, because
     this document is an iframe and cannot inherit the host page's theme. The
     caller passes the active Streamlit theme through, so the map always matches
     the dashboard it sits in. The dark values mirror the dashboard's :root
     tokens; the light values are their own ramp rather than inverted ones,
     because pure inversion of an amber accent loses its contrast on white. */
  :root {{
    --bg:{map_dark_bg};
    --surface:{map_dark_surface};
    --line:{map_dark_line};
    --text:{map_dark_text};
    --muted:{map_dark_muted};
    --dim:{map_dark_dim};
    --accent:{map_dark_accent};
    --accent-soft:{map_dark_accent_soft};
    --on-accent:{map_dark_on_accent};
    --shadow:{map_dark_shadow};
    color-scheme:dark;
  }}
  .theme-light {{
    --bg:{map_light_bg};
    --surface:{map_light_surface};
    --line:{map_light_line};
    --text:{map_light_text};
    --muted:{map_light_muted};
    --dim:{map_light_dim};
    --accent:{map_light_accent};
    --accent-soft:{map_light_accent_soft};
    --on-accent:{map_light_on_accent};
    --shadow:{map_light_shadow};
    color-scheme:light;
  }}

  html, body {{ margin:0; padding:0; height:100%; }}
  body {{ background:var(--bg); color:var(--text); overflow:hidden;
    font-family:'Outfit',-apple-system,BlinkMacSystemFont,Roboto,sans-serif; }}

  /* ── Shell ─────────────────────────────────────────────────────────────────
     Header, map and legend are three bands in a flex column rather than overlays
     on the map. As absolute overlays the legend and the lake button both sat on
     top of the network they were annotating, and the header title crowded the
     first row of map tiles. */
  .shell {{ display:flex; flex-direction:column; height:100vh; }}

  .hud {{ flex:0 0 auto; display:flex; align-items:center; justify-content:space-between;
    flex-wrap:wrap; gap:10px 18px; padding:11px 14px;
    background:var(--surface); border-bottom:1px solid var(--line); font-size:12px; }}
  .hud-main {{ min-width:0; flex:1 1 320px; }}
  .hud-line {{ display:flex; align-items:baseline; flex-wrap:wrap; gap:0 10px; }}
  .hud b {{ color:var(--text); font-weight:600; font-variant-numeric:tabular-nums; }}
  .hud-title {{ color:var(--muted); font-weight:600; letter-spacing:0.02em; }}
  /* Amber in dark, a darker amber in light: the same hue held at 4.5:1 on both
     grounds, so the clock reads as the accent without glowing on white. */
  .hud-time {{ color:var(--accent); font-weight:700; font-size:14px;
    font-variant-numeric:tabular-nums; }}
  .hud-phase {{ color:var(--dim); font-size:11px; display:block; margin-top:2px;
    max-width:70ch; line-height:1.4; }}
  /* Each stat is its own grid cell so the labels align under each other rather
     than floating at the width of their values. */
  .hud-stats {{ display:grid; grid-auto-flow:column; gap:18px; font-size:11px;
    color:var(--muted); }}
  .hud-stats > span {{ display:flex; flex-direction:column; gap:1px; white-space:nowrap; }}
  .hud-stats .k {{ color:var(--dim); font-size:9.5px; text-transform:uppercase;
    letter-spacing:0.08em; font-weight:600; }}
  .hud-stats b {{ font-size:12px; }}

  /* A real button in the header row, so it can no longer overlap the HUD: its
     position came from a hard-coded ``top`` offset that assumed a HUD height. */
  /* Both header buttons share one class. They sit in the header's flex row rather
     than at fixed offsets, which is what stops them landing on top of the HUD. */
  .map-btn {{ flex:0 0 auto; align-self:center; background:var(--accent-soft);
    color:var(--accent); border:1px solid var(--line); border-radius:8px;
    padding:7px 12px; font-size:11px; font-weight:600; cursor:pointer;
    font-family:inherit; letter-spacing:0.02em; white-space:nowrap;
    transition:background .14s ease, color .14s ease, border-color .14s ease; }}
  .map-btn:hover {{ background:var(--accent); color:var(--on-accent);
    border-color:var(--accent); }}
  .map-btn:active {{ transform:translateY(1px); }}
  /* Keyboard users get a visible focus ring; a mouse click does not show one. */
  .map-btn:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}

  #map {{ flex:1 1 auto; min-height:0; }}

  /* ── Legend ────────────────────────────────────────────────────────────────
     A band under the map. Rows are inline-flex with an explicit row *and*
     column gap, so a narrow embed wraps them into tidy columns instead of
     letting them run together or spill over the map. */
  .legend {{ flex:0 0 auto; display:flex; flex-wrap:wrap; align-items:center;
    gap:6px 22px; padding:9px 14px; background:var(--surface);
    border-top:1px solid var(--line); font-size:11px; color:var(--muted); }}
  .legend h4 {{ margin:0; font-size:9.5px; color:var(--dim); font-weight:600;
    text-transform:uppercase; letter-spacing:0.09em; }}
  /* `gap` sets both axes here: without a row gap the wrapped items touch, which is
     what made the four corridors look merged into two. */
  .legend-rows {{ display:flex; flex-wrap:wrap; align-items:center;
    gap:3px 15px; min-width:0; }}
  .legend-rows > span {{ display:flex; align-items:center; gap:6px; white-space:nowrap; }}
  /* A corridor row is clickable: it frames that corridor. Kept on the row span so the
     click target is the same box the legend lays out, rather than a block child that
     would reintroduce the two-column merge this layout exists to prevent. */
  .legend-rows .legend-route-row {{ cursor:pointer; padding:1px 4px; border-radius:4px;
    transition:background .15s ease, color .15s ease; }}
  .legend-rows .legend-route-row:hover {{ background:var(--accent-soft); color:var(--accent); }}
  .swatch {{ width:10px; height:10px; border-radius:50%; flex:0 0 auto; }}
  .line {{ width:14px; height:3px; border-radius:2px; flex:0 0 auto; }}
  /* Tabular figures keep the four ranges the same width so the labels line up. */
  .band {{ font-variant-numeric:tabular-nums; color:var(--text); }}
  .word, .rid {{ color:var(--muted); }}

  .leaflet-popup-content-wrapper {{ background:var(--surface); color:var(--text);
    border:1px solid var(--line); border-radius:10px; box-shadow:var(--shadow); }}
  .leaflet-popup-tip {{ background:var(--surface); border:1px solid var(--line); }}
  .leaflet-popup-content {{ margin:11px 13px; font-size:12px; line-height:1.65;
    font-variant-numeric:tabular-nums; color:var(--text); }}
  .leaflet-popup-content b {{ color:var(--muted); font-weight:500; }}
  .leaflet-container a.leaflet-popup-close-button {{ color:var(--dim); }}
  .leaflet-container a.leaflet-popup-close-button:hover {{ color:var(--text); }}
  /* Tooltips inherit the same surfaces, otherwise they render white-on-white in
     the light theme. */
  .leaflet-tooltip {{ background:var(--surface); color:var(--text);
    border:1px solid var(--line); box-shadow:var(--shadow); font-size:11px; }}
  .leaflet-tooltip-top:before {{ border-top-color:var(--line); }}
  .leaflet-control-attribution {{ background:var(--surface) !important;
    color:var(--dim) !important; font-size:9.5px !important; }}
  .leaflet-control-attribution a {{ color:var(--muted) !important; }}
  .leaflet-bar a {{ background:var(--surface); color:var(--text);
    border-bottom-color:var(--line); }}
  .leaflet-bar a:hover {{ background:var(--bg); color:var(--accent); }}
  /* Respect a reader who has asked for less motion. */
  @media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style>
</head>
<body>
<div class="shell">
<div class="hud">
  <div class="hud-main">
    <span class="hud-line">
      <span class="hud-title">Coimbatore transit &amp; floating solar</span>
      <span class="hud-time" id="clock">--:--</span>
    </span>
    <span class="hud-phase" id="phase"></span>
  </div>
  <div class="hud-stats">
    <span><span class="k">Generation</span> <b id="gen">0</b> kW</span>
    <span><span class="k">Load</span> <b id="load">0</b> kW</span>
    <span><span class="k">Swaps</span> <b id="swaps">0</b></span>
    <span><span class="k">DO</span> <b id="do">--</b> mg/L</span>
  </div>
  <button id="focusLake" class="map-btn" type="button" title="Frame the Ukkadam Lake floating array">Focus lake</button>
  <button id="fitNetwork" class="map-btn" type="button" title="Fit all transit corridors and swap hubs across Coimbatore">Show all routes</button>
</div>
<div id="map"></div>
<div class="legend">
  <h4>Corridors</h4><span id="legendRoutes" class="legend-rows"></span>
  <h4>State of charge</h4>
{soc_legend}
</div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const D = {data};
const STATE_NAMES = {state_names};
const CHARGE_RATE = {charge_rate};
const CAPACITY_MWP = {capacity_mwp};

// Read a palette value out of CSS so the few canvas-drawn colours that must match
// the theme (marker outlines and fills) follow the same tokens the CSS uses,
// instead of being frozen to the dark ramp at template-build time.
const css = (name) => getComputedStyle(document.documentElement)
  .getPropertyValue(name).trim() || '#888';
const INK = css('--text'), PAPER = css('--surface');

function fmtClock(hour) {{
  const h = Math.floor(hour);
  const m = (hour - h > 0.25) ? '30' : '00';
  const ampm = h >= 12 ? 'PM' : 'AM';
  const c = (h % 12 === 0) ? 12 : (h % 12);
  return String(c).padStart(2,'0') + ':' + m + ' ' + ampm;
}}
function socColour(s) {{
  // Band boundaries and colours are injected from src/config/settings.py, the same
  // values ``Bus.soc_color`` uses, so the map and the fleet table cannot disagree.
  if (s < {soc_low}) return '{colour_0}';
  if (s < {soc_mid}) return '{colour_1}';
  if (s < {soc_high}) return '{colour_2}';
  return '{colour_3}';
}}

// Opening view is fitted to every piece of geometry (lake, array, stations, all route
// waypoints). Leaflet derives the zoom from the rendered container size, which is why
// no centre/zoom pair is hard-coded here.
const map = L.map('map', {{ zoomControl:true, minZoom:{zoom_min}, maxZoom:{zoom_max} }})
  .fitBounds([[{south}, {west}], [{north}, {east}]],
             {{ padding:[{pad_px}, {pad_px}, {pad_px}, {pad_px}] }});
L.tileLayer('{tile_url}',
  {{ maxZoom:{tile_max_zoom}, attribution:'{tile_attribution}' }}).addTo(map);

const lakeLayer = L.polygon(D.lakePolygon,
  {{ color:'#2980B9', weight:2.5, fillColor:'#3498DB', fillOpacity:0.32 }}).addTo(map)
  .bindPopup('<b>Ukkadam Lake (Periyakulam)</b><br>Area 1.295 km&sup2;<br>Ecological cap 15% coverage');
const solarLayer = L.polygon(D.solarPolygon,
  {{ color:'#F1C40F', weight:2.5, fillColor:'#1C2833', fillOpacity:0.4 }}).addTo(map)
  .bindPopup('<b>Floating solar array</b><br>' + CAPACITY_MWP.toFixed(1) + ' MWp at 15% coverage');

// The opening view is fitted to the whole network, which is ~17 km wide, so the lake
// is necessarily small at first. This control frames the floating array itself.
(function () {{
  var btn = document.getElementById('focusLake');
  if (!btn) return;
  function focus() {{
    map.flyTo([{lake_lat}, {lake_lon}], {lake_zoom}, {{ duration: 0.8 }});
    solarLayer.bringToFront();
    lakeLayer.bringToFront();
    btn.blur();
  }}
  btn.addEventListener('click', focus);
  btn.addEventListener('keydown', function (e) {{
    if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); focus(); }}
  }});
}})();

// Refits the whole network, undoing either control above. Present because "Focus
// lake" is destructive to the opening view and the reader needs a way back.
(function () {{
  var btn = document.getElementById('fitNetwork');
  if (!btn) return;
  function fit() {{
    map.fitBounds([[{south}, {west}], [{north}, {east}]],
                  {{ padding:[{pad_px}, {pad_px}, {pad_px}, {pad_px}] }});
    btn.blur();
  }}
  btn.addEventListener('click', fit);
  btn.addEventListener('keydown', function (e) {{
    if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); fit(); }}
  }});
}})();

const legendRoutes = document.getElementById('legendRoutes');
for (const [rid, info] of Object.entries(D.routes)) {{
  const poly = L.polyline(info.waypoints, {{ color:info.color, weight:4, opacity:0.85 }})
    .addTo(map)
    .bindTooltip('<b>' + rid + '</b>: ' + info.name + '<br>' + info.buses + ' buses &bull; '
                 + info.stopNames.length + ' named stops');
  const w = info.waypoints;
  L.circleMarker(w[0], {{ radius:4, color:info.color, fillColor:PAPER, fillOpacity:1 }}).addTo(map);
  L.circleMarker(w[w.length-1], {{ radius:4, color:info.color, fillColor:PAPER, fillOpacity:1 }}).addTo(map);
  info.stopNames.forEach((name, i) => {{
    const idx = info.stopIndices[i] ??
      Math.round(i * (w.length - 1) / Math.max(1, info.stopNames.length - 1));
    L.circleMarker(w[idx], {{ radius:5, color:info.color, weight:2,
      fillColor:INK, fillOpacity:1 }}).addTo(map)
      .bindTooltip('<b>' + rid + ' &middot; ' + name + '</b><br>Operational stop');
  }});
  // The corridor id is the legend's whole job; the long name is a tooltip. Inline
  // text at this size would wrap into the neighbouring row and read as a merge.
  // The row is a span, matching the SOC rows, so both sets share one flex layout.
  const row = document.createElement('span');
  row.className = 'legend-route-row';
  row.title = 'Click to zoom to ' + rid + ' (' + info.name + ')';
  row.tabIndex = 0;
  row.innerHTML = '<span class="line" style="background:' + info.color + '"></span>'
    + '<span class="rid">' + rid + '</span>';
  function zoomToRoute() {{
    map.fitBounds(poly.getBounds(), {{ padding: [60, 60] }});
    poly.openTooltip();
  }}
  row.addEventListener('click', zoomToRoute);
  // Keyboard parity: a focusable row that only responds to a mouse is a trap for
  // anyone tabbing through the legend.
  row.addEventListener('keydown', function (e) {{
    if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); zoomToRoute(); }}
  }});
  legendRoutes.appendChild(row);
}}

for (const b of D.buildings) {{
  const icon = L.divIcon({{ className:'', iconSize:[22,22], iconAnchor:[11,11],
    html:'<div style="background:#2563EB;color:#fff;border-radius:50%;width:22px;height:22px;'
       + 'display:flex;align-items:center;justify-content:center;border:2px solid #fff;'
       + 'box-shadow:0 2px 6px rgba(0,0,0,.4);font-size:11px;">&#127959;</div>' }});
  L.marker([b.lat, b.lon], {{ icon }}).addTo(map)
    .bindPopup('<b>' + b.name + '</b><br>Captive load: ' + b.load_kw + ' kW');
}}

const halos = {{}};
for (const st of D.stations) {{
  halos[st.name] = L.circle([st.lat, st.lon],
    {{ radius:400, color:'#10B981', fillColor:'#10B981', fillOpacity:0, weight:2 }}).addTo(map);
  L.circle([st.lat, st.lon], {{ radius:2000, color:'#F59E0B', weight:1, dashArray:'6,6',
    fill:false, interactive:false }}).addTo(map).bindTooltip('Charging catchment: 2 km');
  const icon = L.divIcon({{ className:'', iconSize:[24,24], iconAnchor:[12,12],
    html:'<div style="background:#F59E0B;color:#fff;border-radius:50%;width:24px;height:24px;'
       + 'display:flex;align-items:center;justify-content:center;border:2px solid #fff;'
       + 'box-shadow:0 2px 8px rgba(0,0,0,.5);font-size:12px;">&#9889;</div>' }});
  L.marker([st.lat, st.lon], {{ icon }}).addTo(map)
    .bindPopup('<b>' + st.name + '</b><br>' + st.desc + '<br>Spares: ' + st.capacity_buses
      + ' packs<br>Service zone: 2 km');
}}

function popupHtml(i, step) {{
  const soc = D.soc[step][i] / {soc_scale};
  const st = STATE_NAMES[D.state[step][i]] || 'depot';
  let badge = '<span style="color:#22C55E">Running in service</span>';
  if (st === 'swapping') badge = '<span style="color:#F5B942;font-weight:700">Swapping battery</span>';
  else if (st === 'heading_to_swap') badge = '<span style="color:#F97316;font-weight:700">Diverting to swap hub</span>';
  else if (st === 'depot') badge = '<span style="color:#94A3B8">At depot</span>';
  else if (soc < {soc_swap}) badge = '<span style="color:#EF4444;font-weight:700">Low SOC &mdash; swap priority</span>';
  return '<b style="font-size:13px;color:#F5B942">Bus #' + D.busId[i] + '</b><br>'
    + '<b>Corridor:</b> ' + D.busRoute[i] + '<br>'
    + '<b>SOC:</b> <b style="color:' + socColour(soc) + '">' + soc.toFixed(1) + '%</b><br>'
    + '<b>Stored:</b> ' + (soc / 100 * D.batteryKwh).toFixed(0) + ' kWh<br>'
    + '<b>Condition:</b> ' + D.condition[step][i] + '<br>'
    + '<b>Status:</b> ' + badge + '<br>'
    + '<b>Stops:</b> ' + D.stops[step][i] + '<br>'
    + '<b>Energy used:</b> ' + D.busEnergy[i] + ' kWh<br>'
    + '<b>Nearest hub:</b> ' + D.busStation[i] + ' (' + D.busStationKm[i] + ' km)';
}}

// Popups are bound here, after `popupHtml` is defined. `render` then refreshes the
// content of whichever popup is open, rather than rewriting all of them every frame.
const markers = [];
for (let i = 0; i < D.busCount; i++) {{
  const m = L.circleMarker([D.lat[0][i] / {coord_scale}, D.lon[0][i] / {coord_scale}],
    {{ radius:5.5, color:INK, weight:1.2, fillColor:'#22C55E', fillOpacity:0.92 }}).addTo(map);
  m.bindPopup(popupHtml(i, 0));
  markers.push(m);
}}

function render(step) {{
  const s = Math.max(0, Math.min(D.steps - 1, step));
  document.getElementById('clock').textContent = fmtClock(D.hour[s]);
  document.getElementById('phase').textContent = D.phase[s] || '';
  document.getElementById('gen').textContent = Math.round(D.genKw[s]).toLocaleString();
  document.getElementById('load').textContent = Math.round(D.demandKw[s]).toLocaleString();
  document.getElementById('swaps').textContent = D.totSwaps[s]
    + ' (' + D.solarSwaps[s] + ' solar / ' + D.gridSwaps[s] + ' grid)';
  document.getElementById('do').textContent = D.doLevel[s].toFixed(2);

  const gen = D.genKw[s], hour = D.hour[s];
  let fill = '#1C2833, 0.35, #34495E';
  if (hour >= 6 && hour < 19 && gen > 0) {{
    const ratio = gen / (D.maxKw || 1);
    if (ratio >= 0.75) fill = '#27AE60, 0.85, #2ECC71';
    else if (ratio >= 0.40) fill = '#F1C40F, 0.72, #F39C12';
    else if (ratio >= 0.15) fill = '#E67E22, 0.60, #D35400';
    else fill = '#BDC3C7, 0.45, #7F8C8D';
  }}
  const p = fill.split(', ');
  solarLayer.setStyle({{ fillColor:p[0], fillOpacity:parseFloat(p[1]), color:p[2] }});

  const critical = D.doLevel[s] < 4.0 || D.coverage[s] > 15.0;
  lakeLayer.setStyle(critical
    ? {{ color:'#EF4444', weight:4, fillColor:'#DC2626', fillOpacity:0.40 }}
    : {{ color:'#2980B9', weight:2.5, fillColor:'#3498DB', fillOpacity:0.32 }});

  const act = D.activity[s] || {{}};
  for (const [name, halo] of Object.entries(halos)) {{
    const n = act[name] || 0;
    if (n > 0) {{
      halo.setStyle({{ fillOpacity:0.32, color:'#2ECC71', weight:3 }});
      halo.bindTooltip('Active charging: ' + n + ' swaps (~' + Math.round(n * CHARGE_RATE) + ' kW)');
    }} else {{
      halo.setStyle({{ fillOpacity:0, color:'#10B981', weight:1 }});
      halo.unbindTooltip();
    }}
  }}

  // Hoisting the repeated per-bus scale divisions out of the loop halves the
  // arithmetic the renderer does per frame.
  const invCoord = 1 / {coord_scale};
  const invSoc = 1 / {soc_scale};
  for (let i = 0; i < markers.length; i++) {{
    const soc = D.soc[s][i] * invSoc;
    const st = D.state[s][i];
    const radius = (st === 's') ? 7.5 : (soc < {soc_swap} ? 6.5 : 5.5);
    markers[i].setLatLng([D.lat[s][i] * invCoord, D.lon[s][i] * invCoord]);
    markers[i].setStyle({{ fillColor: socColour(soc), radius }});
    // Only rewrite the popup when it is open, so clicking a bus does not rebuild
    // the HTML of all 260 markers on every frame.
    const m = markers[i];
    if (m.isPopupOpen()) m.setPopupContent(popupHtml(i, s));
  }}
}}

render({initial_step});
</script>
</body>
</html>"""
