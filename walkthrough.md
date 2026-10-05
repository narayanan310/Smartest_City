# Coimbatore Smart City Simulation — Project Walkthrough

**Course:** BCSE355L Smart Cities · VIT Vellore
**Team:** Aditya Saxena (24BCE1633) · Narayanan Subramanian (24BCE1699)

A Streamlit dashboard that simulates one representative day (48 × 30-minute steps) of a
floating-solar, electric-bus and battery-swapping system on Ukkadam Lake, Coimbatore.

---

## Run it

```bash
cd coimbatore_smart_city_sim
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Open the URL Streamlit prints (normally <http://localhost:8501>). No API key is needed.

```bash
python scripts/validate_results.py   # model + invariant checks, exits non-zero on failure
python -m pytest -q                  # 342 unit and integration tests
python -m src.simulation.engine      # standalone 24-hour and 365-day self-check
```

---

## Project layout

```text
coimbatore_smart_city_sim/
├── app.py                       Streamlit dashboard (4 tabs, sidebar, exports)
├── src/
│   ├── config/settings.py       Every constant; the whole scenario is one dataclass
│   ├── simulation/engine.py     48-step daily model + 365-day annual model
│   ├── models/bus.py            Vehicle geometry, route position, SOC, diversion
│   ├── models/ecology.py        Mass-conserving lake dissolved-oxygen budget
│   ├── algorithms/solar.py      PV profile, cloud attenuation, cell-temperature derate
│   ├── algorithms/scheduler.py  Fleet construction, traction energy, swap scheduling
│   ├── visualization/map.py     Self-contained Leaflet component
│   └── services/nasa_power.py   Optional irradiance cross-check (cached, offline-safe)
├── scripts/validate_results.py  Command-line validation gate
├── tests/                       342 tests across engine, scheduler, ecology, solar, bus,
│                                 map and regression suites
└── docs/                        Design notes and presentation material
```

---

## Dashboard tabs

| Tab | Contents |
|---|---|
| 🗺️ **Live simulation** | Animated Leaflet map, energy-flow cards, live KPI ticker, route operations board, fleet condition monitor, hub inventory, four charts, full-day summary |
| 🌊 **Coverage scenarios & sensitivity** | Steady-state DO ladder for 10/15/20% (plus stress coverages), sensitivity analysis over the calibrated ecological coefficient, scenario CSV |
| 💰 **Cost-benefit** | CAPEX/OPEX, blended savings basis, NPV, payback, cumulative cash flow, assumptions table |
| 📅 **Annual model & exports** | 8,760-hour annual model, monthly energy and carbon, solar-resource cross-check, telemetry/annual/manifest downloads |

Sidebar controls: coverage (with optional stress range), fleet size, pack capacity,
traffic intensity, cloud multiplier, charger bays per hub, season, random seed, and
playback speed.

---

## Headline results (15% coverage, summer, seed 42)

| Quantity | Value |
|---|---|
| Installed capacity | 12.95 MWp |
| Daily generation | 68,227 kWh |
| Self-consumed | 48,685 kWh (71.4%) |
| Curtailed | 19,542 kWh (28.6%) |
| Grid import | 53,154 kWh |
| Fleet traction energy | 54,716 kWh over 80,218 km |
| Battery swaps | 168 — **77 solar-funded**, 91 grid-assisted |
| CO₂ avoided | 39.9 t/day · 10,881 t/yr (self-consumed basis) |
| Tariff saving | ₹415.8k/day · ₹11.33 Cr/yr |
| Dissolved oxygen | 4.29 – 6.61 mg/day (min above the 4.0 threshold) |
| Annual generation | 21.36 GWh (analytic planning estimate 21.96 GWh) |

> Carbon and tariff figures are credited **only against self-consumed energy**.
> Crediting total generation would report 17,517 t/yr instead of 10,881 t/yr, because
> curtailed output displaces nothing.

---

## Three things the model actually demonstrates

1. **Swaps follow the sun.** Solar-funded swaps occur only between 10:00 and 15:30,
   when generation exceeds load. Everything outside that window is grid-assisted. The
   split is an *output* of the energy balance, not an assumption.
2. **The array cannot power the whole system.** Fleet demand is 20.79 GWh/yr and
   municipal load 9.09 GWh/yr, against 21.36 GWh/yr generated. The array self-consumes
   about 64% of fleet traction energy; the balance is grid-supplied.
3. **Oxygen falls with coverage, but for a specific reason.** Minimum DO goes
   5.49 → 4.72 → 4.33 → 3.94 mg/L at 0/10/15/20%. The 20% figure sits *at* the
   regulatory threshold and 20% also breaches the 15% policy cap outright.

---

## Honest limitations

Read `docs/ASSUMPTIONS_AND_LIMITATIONS.md` before quoting any number. The most important:

- Lake ecology is a **heuristic budget model**, never calibrated against Ukkadam Lake
  measurements. Its two calibrated coefficients, `shade_effectiveness = 2.1` and
`gross_photosynthesis_mg_l_per_hr`, are varied
  explicitly in the dashboard's sensitivity analysis.
- Traffic, demand, arrivals and weather are synthetic; there is no GTFS, GPS or
  traffic feed.
- Routes, stops and station positions are embedded static geometry, not a live
  routing service.
- Economics are academic estimates. Each self-consumed kilowatt-hour is credited exactly
  once, at the displaced-fuel or retail value depending on which load it serves, so
  the same energy is never counted twice. Annual benefit Rs 29.94 Cr.
---

## Addendum — hybrid data modes & expanded network

Two scenario axes extend the baseline above:

- **Data mode** (`simulated` vs `hybrid`). Hybrid scales the solar profile by
  the NASA monthly deviation, accrues revenue at the TNERC ToD tariff and
  applies the CEA 0.727 factor — all read live from `data/raw/` with identical
  fallbacks when files are absent. The ecology model is unchanged in both
  modes (benchmarked against compiled TNPCB/CCMC secondary data, not fitted).
- **Network mode** (`standard`: 4 corridors / 4 hubs vs `expanded`: 6 / 6,
  adding Route 45, Route 70 and two hubs). The dashboard opens on
  simulated/expanded: 175 swaps (71 solar / 104 grid), 49,542 kWh
  self-consumed, 40.62 t CO₂/day at 15% / summer / seed 42.
- `python scripts/ingest_and_vet_data.py` vets the raw snapshots (78 checks)
  and regenerates `data/processed/`; the test suite does this automatically
  on a bare clone. Full detail: `docs/HYBRID_DATA_MODES.md`.
