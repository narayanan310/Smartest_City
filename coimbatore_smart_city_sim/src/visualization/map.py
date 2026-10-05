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
    labels = (
        f"&lt;{_fmt_pct(low)}% critical",
        f"{_fmt_pct(low)}-{_fmt_pct(mid)}% swap priority",
        f"{_fmt_pct(mid)}-{_fmt_pct(high)}% moderate",
        f"{_fmt_pct(high)}%+ healthy",
    )
    # Colours run high to low in the legend, matching the original reading order.
    pairs = list(zip(reversed(SOC_COLOUR_SCALE), reversed(labels), strict=True))
    return "\n".join(
        f'  <div><span class="swatch" style="background:{colour}"></span>{label}</div>'
        for colour, label in pairs
    )


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
) -> str:
    """Render the complete Leaflet document for the given trajectory.

    The returned HTML is a single self-contained component that renders exactly the
    step given by ``initial_step``. It does not animate on its own; the caller
    re-renders it as the simulation advances.
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


_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
  html, body, #map {{ margin:0; padding:0; width:100%; height:100%;
    background:#0B1422; font-family:'Segoe UI',-apple-system,BlinkMacSystemFont,Roboto,sans-serif;
    overflow:hidden; }}
  .hud {{ position:absolute; top:10px; left:10px; right:10px; z-index:1000;
    background:rgba(11,20,34,0.93); border:1px solid rgba(56,189,248,0.35); border-radius:8px;
    padding:8px 14px; color:#E2E8F0; display:flex; justify-content:space-between;
    align-items:center; gap:12px; box-shadow:0 4px 16px rgba(0,0,0,0.5); font-size:13px; }}
  .hud b {{ color:#38BDF8; }}
  .hud-time {{ color:#FCD34D; font-weight:700; font-size:15px; }}
  .hud-phase {{ color:#94A3B8; font-size:12px; display:block; margin-top:2px; }}
  .hud-stats {{ display:flex; gap:14px; white-space:nowrap; font-size:11px; color:#CBD5E1; }}
  .map-btn {{ position:absolute; top:62px; z-index:1000;
    background:rgba(15,23,42,0.92); color:#38BDF8; border:1px solid rgba(56,189,248,0.45);
    border-radius:8px; padding:6px 11px; font-size:11px; font-weight:600; cursor:pointer;
    font-family:inherit; letter-spacing:0.02em; box-shadow:0 2px 8px rgba(0,0,0,0.5); }}
  .map-btn:hover {{ background:rgba(56,189,248,0.22); color:#E0F2FE; }}
  .map-btn:active {{ transform:translateY(1px); }}
  #focusLake {{ left:14px; }}
  #fitNetwork {{ left:108px; }}
  .legend {{ position:absolute; bottom:14px; right:14px; z-index:1000;
    background:rgba(15,23,42,0.92); border:1px solid rgba(56,189,248,0.35); border-radius:8px;
    padding:10px 12px; color:#F8FAFC; font-size:11px; line-height:1.5; max-width:210px; max-height:360px; overflow-y:auto; }}
  .legend h4 {{ margin:0 0 6px; font-size:12px; color:#38BDF8; }}
  .legend div {{ display:flex; align-items:center; gap:6px; }}
  .legend-route-row {{ cursor:pointer; padding:2px 4px; border-radius:4px; transition:background 0.15s; }}
  .legend-route-row:hover {{ background:rgba(56,189,248,0.2); color:#38BDF8; }}
  .swatch {{ display:inline-block; width:11px; height:11px; border-radius:50%; flex-shrink:0; }}
  .line {{ display:inline-block; width:14px; height:3px; flex-shrink:0; }}
  .leaflet-popup-content-wrapper {{ background:#0F172A; color:#E2E8F0;
    border:1px solid rgba(56,189,248,0.4); border-radius:8px; }}
  .leaflet-popup-tip {{ background:#0F172A; }}
  .leaflet-popup-content {{ margin:10px 12px; font-size:12px; line-height:1.5; }}
</style>
</head>
<body>
<div id="map"></div>
<div class="hud">
  <div>
    <b>Coimbatore Transit &amp; Floating Solar</b>
    <span class="hud-time" id="clock">--:--</span>
    <span class="hud-phase" id="phase"></span>
  </div>
  <div class="hud-stats">
    <span>&#9728;&#65039; <b id="gen">0</b> kW</span>
    <span>&#128674; <b id="load">0</b> kW</span>
    <span>&#128267; <b id="swaps">0</b></span>
    <span>&#128167; <b id="do">--</b> mg/L</span>
  </div>
</div>
<button id="focusLake" class="map-btn" type="button" title="Frame the Ukkadam Lake floating array">Focus lake</button>
<button id="fitNetwork" class="map-btn" type="button" title="Fit all transit corridors and swap hubs across Coimbatore">Show all routes</button>
<div class="legend">
  <h4>Corridors &amp; fleet status</h4>
  <div id="legendRoutes"></div>
  <h4 style="margin-top:8px;">State of charge</h4>
{soc_legend}
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const D = {data};
const STATE_NAMES = {state_names};
const CHARGE_RATE = {charge_rate};
const CAPACITY_MWP = {capacity_mwp};

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
  const poly = L.polyline(info.waypoints, {{ color:info.color, weight:4, opacity:0.85 }}).addTo(map)
    .bindTooltip('<b>' + rid + '</b>: ' + info.name + '<br>' + info.buses + ' buses &bull; '
                 + info.stopNames.length + ' named stops');
  const w = info.waypoints;
  L.circleMarker(w[0], {{ radius:4, color:info.color, fillColor:'#fff', fillOpacity:1 }}).addTo(map);
  L.circleMarker(w[w.length-1], {{ radius:4, color:info.color, fillColor:'#fff', fillOpacity:1 }}).addTo(map);
  info.stopNames.forEach((name, i) => {{
    const idx = info.stopIndices[i] ??
      Math.round(i * (w.length - 1) / Math.max(1, info.stopNames.length - 1));
    L.circleMarker(w[idx], {{ radius:5, color:info.color, weight:2,
      fillColor:'#0F172A', fillOpacity:1 }}).addTo(map)
      .bindTooltip('<b>' + rid + ' &middot; ' + name + '</b><br>Operational stop');
  }});
  const row = document.createElement('div');
  row.className = 'legend-route-row';
  row.title = 'Click to zoom to ' + rid + ' (' + info.name + ')';
  row.innerHTML = '<span class="line" style="background:' + info.color + '"></span>'
    + '<span style="font-weight:600">' + rid + '</span>';
  row.addEventListener('click', () => {{
    map.fitBounds(poly.getBounds(), {{ padding: [60, 60] }});
    poly.openTooltip();
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

const markers = [];
for (let i = 0; i < D.busCount; i++) {{
  markers.push(L.circleMarker([D.lat[0][i] / {coord_scale}, D.lon[0][i] / {coord_scale}],
    {{ radius:5.5, color:'#fff', weight:1.2, fillColor:'#22C55E', fillOpacity:0.92 }}).addTo(map));
}}

function popupHtml(i, step) {{
  const soc = D.soc[step][i] / {soc_scale};
  const st = STATE_NAMES[D.state[step][i]] || 'depot';
  let badge = '<span style="color:#22C55E">Running in service</span>';
  if (st === 'swapping') badge = '<span style="color:#38BDF8;font-weight:700">Swapping battery</span>';
  else if (st === 'heading_to_swap') badge = '<span style="color:#F97316;font-weight:700">Diverting to swap hub</span>';
  else if (st === 'depot') badge = '<span style="color:#94A3B8">At depot</span>';
  else if (soc < {soc_swap}) badge = '<span style="color:#EF4444;font-weight:700">Low SOC &mdash; swap priority</span>';
  return '<b style="font-size:13px;color:#38BDF8">Bus #' + D.busId[i] + '</b><br>'
    + '<b>Corridor:</b> ' + D.busRoute[i] + '<br>'
    + '<b>SOC:</b> <b style="color:' + socColour(soc) + '">' + soc.toFixed(1) + '%</b><br>'
    + '<b>Stored:</b> ' + (soc / 100 * D.batteryKwh).toFixed(0) + ' kWh<br>'
    + '<b>Condition:</b> ' + D.condition[step][i] + '<br>'
    + '<b>Status:</b> ' + badge + '<br>'
    + '<b>Stops:</b> ' + D.stops[step][i] + '<br>'
    + '<b>Energy used:</b> ' + D.busEnergy[i] + ' kWh<br>'
    + '<b>Nearest hub:</b> ' + D.busStation[i] + ' (' + D.busStationKm[i] + ' km)';
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

  for (let i = 0; i < markers.length; i++) {{
    const soc = D.soc[s][i] / {soc_scale};
    const st = D.state[s][i];
    const radius = (st === 's') ? 7.5 : (soc < {soc_swap} ? 6.5 : 5.5);
    markers[i].setLatLng([D.lat[s][i] / {coord_scale}, D.lon[s][i] / {coord_scale}]);
    markers[i].setStyle({{ fillColor: socColour(soc), radius }});
    markers[i].setPopupContent(popupHtml(i, s));
  }}
}}

render({initial_step});
</script>
</body>
</html>"""
