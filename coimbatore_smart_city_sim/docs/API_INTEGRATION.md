# API integration

Every figure below is a runtime network call the application can make.

| Call | Made by | When | Failure behaviour |
|---|---|---|---|
| OpenStreetMap raster tiles | Browser, via Leaflet | While the map is visible | Basemap is blank; simulation unaffected |
| Leaflet 1.9.4 (CSS + JS) | Browser, from unpkg | While the map is visible | Map does not render; simulation unaffected |
| Font Awesome | No longer used | — | Removed with the Folium rewrite |
| Google Fonts (Outfit) | Browser | On page load | Falls back to the system sans stack |
| NASA POWER monthly point API | `src/services/nasa_power.py` | Only on a cache miss during the solar cross-check | Falls back to SRRA/MNRE normals |
| OSRM / Google Maps routing | Never | Never | Routes are embedded static geometry |
| Weather API | Never | Never | Cloud cover is synthetic and seeded |

## API keys

None. No service used by this project requires a key. If a future integration needs one,
put it in a `.env` file and load it with `python-dotenv`; never commit it.

## Caching

`src/services/nasa_power.py` caches its response to `data/raw/nasa_cache.json` and reads
that cache before attempting any request, so repeated dashboard sessions do not re-fetch.
The cache is disposable: deleting it only causes one subsequent fetch attempt.
