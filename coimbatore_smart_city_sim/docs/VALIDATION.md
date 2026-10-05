# Validation

```bash
python scripts/validate_results.py   # exits 1 if any check fails
python -m pytest -q                  # 383 tests
python -m src.simulation.engine      # standalone self-check
```

The validation script prints every check with its measured value. It currently runs
**40 checks** across seven groups: energy balance, fleet consistency, impact
accounting, determinism, the coverage ladder, robustness to degenerate configurations,
and the photovoltaic model. Current results at 15% coverage, summer, seed 42
(simulated/standard — the class defaults; the dashboard opens on
simulated/expanded, see `docs/HYBRID_DATA_MODES.md` for that column):

| Measure | Value |
|---|---:|
| Installed capacity | 12.95 MWp |
| Daily generation | 68,227 kWh |
| Daily self-consumption | 48,685 kWh (71.4%) |
| Daily curtailment | 19,542 kWh (28.6%) |
| Daily grid import | 53,154 kWh |
| Peak generation | 10,680 kW |
| Daily fleet traction energy | 54,716 kWh over 80,218 km |
| Daily swaps | 168 (77 solar-funded, 91 grid-assisted) |
| Daily CO2 avoided | 39.9 t |
| Daily tariff saving | Rs 415.8k |
| Annual generation (hourly) | 21.36 GWh |
| Annual self-consumed | 13.27 GWh |
| Annual curtailment | 8.09 GWh |
| Annual grid import | 16.61 GWh |
| Annual fleet demand | 20.79 GWh |
| Annual municipal demand | 9.09 GWh |
| Annual CO2 avoided (self-consumed basis) | 10,881 t |
| Annual CO2 if all generation were credited | 17,517 t |
| Annual tariff saving | Rs 11.33 Cr |
| Annual benefit (single-credit) | Rs 29.94 Cr |

## Minimum dissolved oxygen by coverage

Summer, steady state, seed 42:

| Coverage | Min DO (mg/L) | Max DO (mg/L) | Aeration | Status |
|---:|---:|---:|---:|---|
| 0% | 5.49 | 8.38 | 0 h | OK |
| 10% | 4.72 | 7.21 | 0 h | OK |
| 15% | 4.33 | 6.62 | 0 h | OK |
| 20% | 3.94 | 6.04 | 0 h | CRITICAL / over cap |
| 25% | 3.56 | 5.46 | 0 h | CRITICAL / over cap |
| 30% | 3.17 | 4.88 | 0 h | CRITICAL / over cap |
| 40% | 2.98 | 4.85 | 10 h | CRITICAL / over cap |
| 45% | 2.97 | 4.52 | 8 h | CRITICAL / over cap |

## Seasonal contrast at 15%

| Season | Daily generation | Minimum DO | Swaps |
|---|---:|---:|---:|
| Summer | 68,227 kWh | 4.29 mg/L | 168 |
| Monsoon | 34,978 kWh | 4.71 mg/L | 154 |

Monsoon halves generation but *raises* the oxygen minimum, because cloud cover suppresses
photosynthesis less than the reduced surface heating lowers water temperature. The
array funds only 9 swaps on the monsoon day against 77 in summer.

## Robustness to degenerate configurations

The gate also runs the model against configurations the dashboard cannot produce, and
requires each to complete without raising while still closing the energy balance, keeping
every bus above its state-of-charge floor, and reporting only finite values:

| Degenerate case | Expected outcome |
|---|---|
| Fleet of 1 bus | Runs; route allocation sums to 1 |
| 0% coverage | Runs; zero generation, all load on the grid |
| 0 charger bays | Runs; packs queue, no bus stranded |
| 1 kWh packs | Runs; state of charge floors at 5% |
| Zero daily consumption | Runs; zero traction demand |

It also asserts that a non-positive step length is **rejected** rather than silently
dividing by zero, in `process_station_charging`, `update_fleet` and `update_ecology`.

## Reproducibility

The seed is exposed in the sidebar and written into the exported run manifest. Every
figure above can be reproduced exactly. Swap counts depend on the scheduler and scenario
and should be read from a run rather than treated as fixed targets.

These are model outputs. They validate code execution and internal consistency, not
real-world performance.
