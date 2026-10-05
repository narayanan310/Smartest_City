# Data sources

| Data | Source | Real or synthetic | In the live path | Cached | API key |
|---|---|---|---|---|---|
| Solar clear-sky profile | SRRA / MNRE | Real, hardcoded | Yes | No | No |
| Specific yield for planning | SRRA / MNRE (1,600 kWh/kWp/yr) | Real, hardcoded | Analytic estimate only | No | No |
| Weather / cloud cover | Synthetic, seeded | Synthetic | Yes | No | No |
| Bus corridors | OSRM waypoints | Real, hardcoded | Yes | No | No |
| Named stops | Curated operational labels | Real, hardcoded | Yes | No | No |
| Lake boundary and array footprint | OpenStreetMap | Real, hardcoded | Yes | No | No |
| Swap hub locations | Project definition | Assumed | Yes | No | No |
| Grid CO2 factor | CEA India (0.82 kg/kWh) | Real, hardcoded | Yes | No | No |
| Tariffs and diesel price | TANGEDCO / TNERC | Real, hardcoded | Yes | No | No |
| Map tiles | OpenStreetMap | Real | Yes, browser side | Browser | No |
| Irradiance cross-check | NASA POWER, else SRRA/MNRE | Real | No, validation only | Yes, on disk | No |
| Lake dissolved oxygen | This model | Modelled | Yes | No | No |

## Notes

The dashboard runs fully offline. The NASA POWER helper is imported only by the
validation panel, is used only to cross-check the embedded profile, and falls back to
SRRA/MNRE normals when the network or the cache is unavailable. It writes its cache to
`data/raw/nasa_cache.json`.
