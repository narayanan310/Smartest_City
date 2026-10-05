# Coimbatore Smart City Simulation

An interactive academic simulation of a floating-solar, electric-bus and
battery-swapping system centred on Ukkadam Lake, Coimbatore. It exists to explore
operational and ecological trade-offs — **not** to dispatch a real fleet or certify an
engineering design.

**Course:** BCSE355L Smart Cities · VIT Vellore
**Team:** Aditya Saxena (24BCE1633) and Narayanan Subramanian (24BCE1699)

---

## What the project models

One representative day at 30-minute resolution (48 steps), coupling:

- a floating-PV array on Ukkadam Lake at 10%, 15% or 20% surface coverage;
- an electric bus fleet whose traction power is **derived from the fleet model** rather
  than from a separate demand curve;
- SOC-aware, **solar-surplus-gated** battery swapping at four hubs, reported in two
  tiers: swaps the array can fund, and swaps that must fall back to the grid;
- municipal captive load, with generation, self-consumption, curtailment and grid
  import forming a closed energy balance;
- lake dissolved oxygen, temperature, pH and turbidity from a mass-conserving budget;
- carbon abatement and tariff savings credited **only against self-consumed energy**.

The 15% coverage case is the ecological policy cap. The 20% option is shown as a
deliberately non-compliant comparison, and an optional stress range reaches the
mechanical-aeration mitigation path.

Two scenario axes extend the baseline: **data mode** (`simulated` vs `hybrid`,
where the NASA monthly deviation, TNERC ToD tariff and CEA 0.727 factor are
read live from `data/raw/`) and **network mode** (`standard` 4 corridors /
4 hubs vs `expanded` 6 corridors / 6 hubs). See
[Hybrid data modes](docs/HYBRID_DATA_MODES.md); the dashboard opens on
simulated/expanded.

---

## Quick start

Requires Python 3.12+.

```bash
cd coimbatore_smart_city_sim
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Open the URL Streamlit prints (normally <http://localhost:8501>). No API key required;
the dashboard runs fully offline.

### Validation and tests

```bash
python scripts/validate_results.py   # model run + 40 invariant checks; exit 1 on failure
python -m pytest -q                  # 383 tests
python -m src.simulation.engine      # standalone 24-hour and 365-day self-check
```

`scripts/validate_results.py` asserts the properties that must hold for any scenario:
energy balance closure, traction demand equal to energy the fleet actually drew, pack
inventory conservation, charger-bay limits, no stranded bus, solar-funded swaps
confined to the surplus window, carbon credited only on self-consumed energy, and
monotonic oxygen response to coverage.

---

## Dashboard guide

| Area | What it shows |
|---|---|
| Hero and clock strip | Scenario definition, simulated time, narrative phase |
| Energy-flow cards | Generation → municipal + traction + charging → surplus or grid import |
| Live KPI ticker | 14 continuously updated values including both swap tiers and curtailment |
| Animated map | Bus positions, SOC colour, corridor polylines, named stops, charging hubs with active halos, 2 km service rings, lake alert colour for ecological risk |
| Route operations board | Planned vs assigned vs in-service buses, length, average SOC, swap-priority and diverting counts, direction split, energy used |
| Fleet condition monitor | Highest-risk buses first with stored energy, current/next stop, speed, nearest hub and distance, swaps, and which tier funded the last swap |
| Charging-station table | Ready packs, charging, queued, free bays, inventory, service radius, status |
| Charts | Generation vs load vs curtailment · fleet SOC composition · dissolved-oxygen profile with threshold bands · swaps by funding source against charging load |
| Coverage scenarios | Steady-state DO ladder, yield-vs-oxygen trade-off, and the **sensitivity analysis** |
| Cost-benefit | CAPEX, blended savings, NPV, payback, cumulative cash flow, assumptions |
| Annual model | 8,760-hour integration, monthly energy and carbon, solar-resource cross-check |
| Provenance tags | Every headline figure labelled 📐 measured, 🔧 calibrated, 📝 assumed or 🧮 modelled |

### Fleet and route model

| Corridor | Route | Design share | Named stops |
|---|---|---:|---:|
| Route 69 | Ukkadam Depot → Pappampatti | 90 | 5 |
| Route 1 | Gandhipuram Hub → Ondipudur | 70 | 5 |
| Route 2 | Gandhipuram Hub → Saibaba Colony | 50 | 4 |
| Route 3 | Ukkadam Lake Depot ↔ Gandhipuram Hub | 50 | 4 |

Those are the design shares at the default fleet of 260. Changing the fleet-size slider
redistributes buses **proportionally** (largest-remainder method), so the allocation
always sums exactly to the fleet size and preserves the ordering above.

Each bus has deterministic route progress, a stable 16–21 km/h cohort speed, a fixed
per-vehicle efficiency factor, and battery-aware SOC depletion. Buses below the swap
threshold that are outside a hub's 2 km radius actively **divert** along their own
corridor toward the nearest point that passes that hub, rather than continuing their
route unchanged. A scenario replayed with the same seed follows the same timeline.

---

## Headline results (15% coverage, summer, seed 42)

| Quantity | Value |
|---|---|
| Installed capacity | 12.95 MWp |
| Daily generation | 68,227 kWh |
| Self-consumed / curtailed | 48,685 kWh (71.4%) / 19,542 kWh (28.6%) |
| Daily grid import | 53,154 kWh |
| Fleet traction energy | 54,716 kWh over 80,218 km |
| Battery swaps | 168 — 77 solar-funded, 91 grid-assisted |
| CO₂ avoided | 39.9 t/day · 10,881 t/yr |
| Annual generation | 21.36 GWh |
| Annual fleet + municipal demand | 20.79 + 9.09 = 29.88 GWh |
| Tariff saving | ₹11.33 Cr/yr |
| Annual benefit (each kWh credited once) | ₹29.94 Cr/yr |

Minimum dissolved oxygen by coverage (summer, steady state): **0% → 5.49**, **10% → 4.72**,
**15% → 4.33**, **20% → 3.94** mg/L. Emergency aeration engages at 40% coverage.

These are model outputs, not measured operating results. Swap counts depend on the
scheduler and scenario; read them from a run rather than assuming fixed values.

---

## Project layout

```text
coimbatore_smart_city_sim/
├── app.py                         # Streamlit dashboard and exports
├── src/
│   ├── config/settings.py         # Every constant; a scenario is one dataclass
│   ├── simulation/engine.py       # 48-step daily and 365-day annual models
│   ├── models/bus.py              # Route position, cached geometry, SOC, diversion
│   ├── models/ecology.py          # Lake dissolved-oxygen budget model
│   ├── algorithms/solar.py        # PV profile, cloud and cell-temperature modifiers
│   ├── algorithms/scheduler.py    # Fleet creation, traction energy, swap scheduling
│   ├── visualization/map.py       # Self-contained Leaflet map component
│   ├── services/nasa_power.py     # Optional irradiance cross-check (cached)
│   └── services/hybrid_data.py    # Runtime bridge: curated data files into the model
├── scripts/validate_results.py    # Command-line validation gate (40 checks)
├── scripts/ingest_and_vet_data.py # Data vetting pipeline (78 checks, 5 artefacts)
├── tests/                         # 383 tests
└── docs/                          # Design notes and presentation material
```

---

## Data and external services

- **Solar profile:** embedded SRRA/MNRE-style hourly curve with synthetic cloud cover
  and a −0.4 %/°C cell-temperature derate. Cross-checked in the dashboard against
  NASA POWER / SRRA-MNRE irradiance.
- **Weather:** seeded synthetic cloud realisation, reproducible from the sidebar seed.
- **Routes and stops:** embedded street-accurate OSRM waypoints plus curated operational
  stop labels. Not a live GTFS or routing feed.
- **Map tiles:** OpenStreetMap, fetched by the browser. No API key.
- **NASA POWER:** optional cached helper. Not in the live simulation path; used only to
  validate the embedded solar profile, and it degrades to offline normals.

---

## Important limitations

Lake ecology is a heuristic budget model, not a calibrated hydrodynamic or water-quality
simulation. Traffic, passenger demand, station arrivals and weather are synthetic.
Battery swapping models pack inventory, charger-bay contention and solar-funded
prioritisation, but not station-level electrical constraints. Energy, cost and carbon
figures are scenario estimates. **Read
[`docs/ASSUMPTIONS_AND_LIMITATIONS.md`](docs/ASSUMPTIONS_AND_LIMITATIONS.md) before
citing any number from the dashboard.**

---

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Simulation model](docs/SIMULATION_MODEL.md)
- [Data sources](docs/DATA_SOURCES.md)
- [Assumptions and limitations](docs/ASSUMPTIONS_AND_LIMITATIONS.md)
- [Hybrid data modes and expanded network](docs/HYBRID_DATA_MODES.md)
- [Validation](docs/VALIDATION.md)
- [Map architecture](docs/MAP_ARCHITECTURE.md)
- [Animation architecture](docs/ANIMATION_ARCHITECTURE.md)
- [API integration](docs/API_INTEGRATION.md)
- [Presentation guide](docs/PRESENTATION_GUIDE.md)
- [Viva preparation](docs/VIVA_PREPARATION.md)
- [Technical audit](docs/PROJECT_TECHNICAL_AUDIT.md)