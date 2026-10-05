# Map architecture

## Why hand-written Leaflet rather than Folium

The map must be re-rendered as the simulation advances, and a rebuilt Folium figure
re-instantiates the whole Leaflet map, which flickers. `visualization/map.py` therefore
emits the HTML document directly, and `src/visualization/map.py::build_interactive_map_html`
returns a self-contained component that draws exactly one step.

`folium`, `streamlit-folium` and `numpy` were declared as dependencies by earlier
revisions and are no longer imported anywhere. They have been removed from
`requirements.txt`.

## Payload

The trajectory is emitted columnar with quantised integers rather than as one dictionary
per bus per frame: latitude and longitude to 1e-4 (about 11 m, far finer than the map
zoom) and state of charge to 0.1%. For 260 buses over 48 frames this is about 0.91 MB
instead of 4.10 MB, a 4.5x reduction, and it is what makes per-step rebuilds affordable.

Frames are padded to a constant bus count so a bus always keeps its marker identity.

## Rendering

`render(step)` updates the HUD, the array's radiance, the lake's ecological alert colour,
each hub's charging halo, and every bus marker's position, colour, radius and popup. Step
indices are clamped, so an out-of-range request is safe.

## Viewport

The opening view is **derived, not hand-picked**. `Config.map_bounds()` collects every
point the map must keep on screen — the lake outline, the array footprint, all four swap
stations and all 232 route waypoints — and returns them as a Leaflet
`(south, west), (north, east)` box. The template hands that box to `map.fitBounds()` with
28 px of padding, and **Leaflet derives the zoom from the rendered container size**, which
is the only thing that knows how wide the map actually is.

This replaced a hard-coded `MAP_CENTER = (11.0020, 76.9850)` at zoom 13. That point is
Coimbatore's city centre, about **4.0 km north-east of Ukkadam Lake**, so the array this
project sites on the lake sat near the edge of the opening viewport, and every corridor
east of roughly 77.02 deg E was cropped. Because the route network genuinely spans
**7.4 km north-south by 17.0 km east-west**, no single centre can both frame the lake and
show every bus; `fitBounds` guarantees nothing is cropped, and the *Focus lake* control
below handles the close-up.

The *Focus lake* button in the top-left flies to `Config.lake_view()` (the mean
solar-array polygon vertex at zoom 15) and brings both polygons to the front. It is
keyboard reachable via Enter/Space.

Because the bounds are computed from the same geometry that is drawn, editing a route or
moving a station can never push anything off-screen. There is a regression test for
exactly that, plus one asserting no `Config` field reintroduces a hand-picked centre.

## Tiles

Standard OpenStreetMap raster tiles from `tile.openstreetmap.org`, fetched by the
browser. No key, no watermark, no account.

## Geometry

Corridor polylines, named stops, the lake polygon, the array footprint, hub positions
and municipal buildings all come from `config/settings.py` as embedded static
coordinates. There is no live routing call at runtime.

The lake polygon is the real OpenStreetMap outline of Ukkadam Periyakulam
(1.295 km2, mean depth 5.82 m). `Config.LAKE_LAT`/`LAKE_LON` carry its geographic centre
and agree with the published coordinate to within about 215 m; the map view is fitted
from the polygon itself, so those two fields are reference values rather than the
positioning input.
