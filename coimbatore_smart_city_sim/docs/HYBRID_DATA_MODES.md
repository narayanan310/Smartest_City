# Hybrid data modes and the expanded network

This document describes the two scenario axes added after the 1.0 baseline:
**data mode** (`simulated` vs `hybrid`) and **network mode** (`standard` vs
`expanded`). It states exactly what each mode changes, where every number
comes from, and what is still synthetic.

## 1. Data mode

| | `simulated` (default for the model) | `hybrid` |
|---|---|---|
| Solar profile | Embedded SRRA/MNRE-style clear-sky curve | Same curve **scaled** by the NASA monthly deviation for the representative month (April 6.33/6.20 = 1.021, July 4.01/3.80 = 1.055), read live from `data/raw/nasa_power_2023_coimbatore.json` |
| Cell temperature | Ambient from `MONTHLY_TEMP_C` | NASA monthly mean for the representative month (April 28.43 °C, July 24.47 °C), read live from the same file |
| Revenue rate | Flat `GRID_TARIFF_PER_KWH` + surcharge | Hourly TNERC Order 6 ToD tariff + surcharge, read live from `data/raw/coimbatore_transit_economics_data.json` |
| Emission factor | 0.82 kg/kWh (CEA baseline) | 0.727 kg/kWh (CEA 2023-24 official), read live from the same file |

The loading lives in `src/services/hybrid_data.py`. Every accessor falls
back to the simulated baseline when its file is missing, corrupt, or absurd,
so a bare checkout behaves exactly like the 1.0 model. The daily step and
the annual loop share one helper (`effective_generation_inputs`), so the two
models calibrate identically by construction.

### What hybrid does NOT do

- It does **not** fit the ecological model to measurements. Lake dissolved
  oxygen, temperature, pH and turbidity are still produced by the
  mass-conserving budget in `src/models/ecology.py`. The lake benchmarks in
  `data/raw/ukkadam_lake_environmental_data.json` are compiled secondary
  data used for vetting and display — the model is benchmarked against them,
  not fitted to them. `src/services/hybrid_data.py::model_data_consistency`
  cross-checks every live constant against its benchmark counterpart and is
  asserted green by the test suite.
- It does **not** consume the synthesised diurnal profiles or dispatch
  curves. Those are planning artefacts of the ingest pipeline, verified for
  shape and CSV/JSON agreement by tests, but no live code path reads them.

## 2. ToD tariff windows (TNERC Order No. 6, public EV charging)

| Window | Rate |
|---|---|
| 09:00–16:00 solar hours | Rs 6.50/kWh |
| 06:00–09:00 and 18:00–22:00 peak | Rs 9.75/kWh |
| 16:00–18:00 and 22:00–06:00 normal/off-peak | Rs 8.10/kWh |

Revenue accrues on self-consumed energy hour by hour in the daily step, the
annual loop, and the Tab 3 benefit table (via the realised blend the annual
model reports: Rs 7.61/kWh at defaults). One rate everywhere within a mode.

## 3. Network mode

| | `standard` | `expanded` (dashboard default) |
|---|---|---|
| Corridors | 4 (Route 69/1/2/3) | 6 (+ Route 45 Sathy Rd IT SEZ, Route 70 Western Vadavalli) |
| Swap hubs | 4 (150 spare packs) | 6 (+ Saravanampatti 30, Vadavalli 25 = 205 spares) |
| Geometry standard | Street-exact OSRM waypoints | Core network as before; the two added corridors are hand-digitized and flagged pending OSRM verification |

Switching is idempotent (`set_network_mode` filters by route key and station
id before merging), validated in `Config.__post_init__`, and covered by tests
that assert the map bounds contain every new waypoint and station.

## 4. Reference figures (15% coverage, summer, seed 42)

| Mode | Day gen | Swaps (solar/grid) | Day cost | Day CO₂ | Annual gen | Annual CO₂ | Annual saving |
|---|---|---|---|---|---|---|---|
| simulated/standard | 68,227 | 168 (77/91) | Rs 415.8k | 39.92 t | 21.36 | 10,881 t | Rs 11.33 Cr |
| hybrid/standard | 70,866 | 168 (82/86) | Rs 375.5k | 35.91 t | 22.06 | 9,789 t | Rs 10.25 Cr |
| simulated/expanded | 68,227 | 175 (71/104) | Rs 423.1k | 40.62 t | 21.36 | 10,881 t | Rs 11.33 Cr |
| hybrid/expanded | 70,866 | 175 (77/98) | Rs 383.9k | 36.77 t | 22.06 | 9,789 t | Rs 10.25 Cr |

The dashboard opens on simulated/expanded. The 1.0 headline figures
(68,227 kWh, 168 swaps, 39.92 t) are the simulated/standard column.

## 5. Data pipeline

```bash
python scripts/ingest_and_vet_data.py
```

Vets the three `data/raw/` snapshots (78 checks: physical bounds,
monotonicity, mathematical coherence of the implied diesel value) and writes
five artefacts to `data/processed/` (gitignored by design). The test suite
regenerates them through a session fixture when absent, so a bare clone stays
green. Raw snapshots are committed; the runtime NASA cache keeps its own
path (`data/raw/nasa_cache.json`, gitignored) and never shares a file with
the curated input.
