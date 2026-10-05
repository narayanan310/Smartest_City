# Viva preparation

Concise answers to the questions most likely to be asked.

**Q: Is this real-time?**
No. It is a 48-step discrete simulation at 30-minute resolution. Given the same
configuration and seed it reproduces every number exactly.

**Q: How do you validate it?**
`scripts/validate_results.py` asserts 40 physical invariants and exits non-zero on
failure: energy balance closure at every step, traction demand equal to the energy the
fleet actually drew, pack inventory conservation, charger-bay limits, no stranded bus,
solar-funded swaps confined to the surplus window, and carbon credited only on
self-consumed energy. There are also 342 unit and integration tests.

**Q: Do you use a mapping API?**
Only OpenStreetMap raster tiles, fetched by the browser. Corridor geometry is embedded
OSRM waypoints. There is no routing call at runtime and no API key anywhere.

**Q: Why 15% coverage?**
Two independent reasons. It is the ecological policy cap, enforced in code. And it is
where the oxygen model retains margin: minimum dissolved oxygen is 4.33 mg/L at 15%
against a 4.0 threshold, falling to 3.94 mg/L at 20%.

**Q: Is the ecology model calibrated?**
The structure is standard and mass-conserving, but it has never been calibrated against
Ukkadam Lake measurements. Two coefficients are calibrated rather than measured, and
`scripts/fit_shading.py` ranks them and inverts them against a target observation.
Ranking showed that `gross_photosynthesis_mg_l_per_hr`, not `shade_effectiveness`, has
the larger effect on the absolute minimum: 3.08 mg/L of span against 1.83. At plus or
minus 20% of either value the 15% conclusion holds; at plus 40% it fails. We report that
rather than presenting the coefficients as data. See `docs/ECOLOGY_CALIBRATION.md`.

**Q: Why does the oxygen minimum occur before the shading happens?**
It does not, once the model is spun up. Panels shade only between 10:00 and 15:00, so a
window starting at midnight reaches its minimum near 07:00, before any shading has
acted. Both the engine and the scenario comparison therefore discard a 10-day transient
before reporting, and they agree to within 0.05 mg/L as a result.

**Q: Where does the electricity actually go?**
On the default summer day, 68.2 MWh is generated, 48.7 MWh is self-consumed and 19.5 MWh
is curtailed. Grid import is 53.2 MWh. The array funds 77 of 168 battery swaps; the rest
are grid-assisted top-ups for buses below the critical threshold.

**Q: How is carbon calculated?**
0.82 kg CO2 per kWh of grid electricity displaced, applied only to self-consumed solar
energy. Applying it to total generation would inflate the annual figure from 10,881 t to
17,517 t, because curtailed output displaces nothing.

**Q: What are the main limitations?**
The lake model is heuristic, not calibrated. Traffic, demand and weather are synthetic:
there is no GTFS, GPS or traffic feed. Battery swapping models pack inventory and
charger contention but not station-level electrical limits. Economics are academic
estimates.

**Q: What would you do next?**
Calibrate the ecological coefficients against real Ukkadam Lake monitoring data, couple
the solar profile to a live irradiance feed, and replace the static corridors with GTFS
and AVL data so that the diversion and arrival logic could be tested against real
operations.
