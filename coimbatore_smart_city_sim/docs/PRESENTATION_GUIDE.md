# Presentation guide

## Structure (10-12 minutes)

1. **Problem (1 min).** Coimbatore's bus fleet and Ukkadam Lake both need power. Floating
   solar on the lake is attractive, but covering the water changes the lake's ecology.
   The question is: how much surface can be covered before the lake is harmed, and does
   the energy actually help the buses?
2. **System (2 min).** 12.95 MWp array at 15% coverage, four swap hubs, 260 buses on
   four corridors, 30-minute simulation resolution. Show the sidebar scenario definition.
3. **Live demo (3 min).** Press Play from 00:00. Point out three things in order:
   - generation ramps while the fleet draws power, and the two curves cross around 09:30;
   - **solar-funded swaps cluster in the surplus window** while grid-assisted swaps appear
     in the evening deficit — the core of the argument;
   - the lake turns from blue to red when dissolved oxygen crosses 4.0 mg/L.
4. **Coverage scenarios (2 min).** The dissolved-oxygen ladder: 0% to 5.49, 10% to 4.72,
   15% to 4.33, 20% to 3.94 mg/L. State plainly that 20% also breaches the 15% policy cap.
5. **Sensitivity (2 min).** Show the tornado chart. Say that the oxygen response is
   proportional to two uncalibrated coefficients, that 15% survives plus or minus 20% of
   and that at +40% the conclusion fails. Volunteering this is far stronger than being
   asked.
6. **Annual model (1 min).** 21.36 GWh generated, 13.27 self-consumed, 8.09 curtailed.
   The array covers about 64% of fleet traction energy; the fleet plus municipal load is
   29.88 GWh against 21.36 GWh generated, so the rest is grid-supplied.
7. **Limitations and close (1 min).** Heuristic ecology, synthetic operations data,
   academic economics.

## Numbers worth memorising

| Quantity | Value |
|---|---|
| Capacity at 15% | 12.95 MWp |
| Daily generation | 68,227 kWh (71.4% self-consumed, 28.6% curtailed) |
| Daily swaps | 168 total: 77 solar-funded, 91 grid-assisted |
| Annual generation | 21.36 GWh |
| Annual CO2 avoided | 10,881 t on self-consumed energy (17,517 t if all output were credited) |
| Min DO at 15% | 4.33 mg/L, above the 4.0 threshold |
| Min DO at 20% | 3.94 mg/L, at the threshold |
| Annual fleet + municipal demand | 29.88 GWh versus 21.36 GWh generated |

## Anticipated questions

- *"Is this real-time?"* No. 48 discrete 30-minute steps, deterministic per seed.
- *"Do you use a mapping API?"* Only for browser tiles. Corridors are embedded geometry.
- *"Why 15%?"* It is the ecological policy cap, and it is also where the oxygen model
  retains margin. 20% sits at the threshold and breaches the cap.
- *"How good is the ecology model?"* A mass-conserving budget, not a calibrated
  simulation. One coefficient is calibrated; the sensitivity analysis shows by how much
  the conclusion depends on it.
- *"Does the array power the buses?"* It funds 77 of 168 daily swaps and about 64% of
  fleet traction energy. The remainder is grid-supplied, and we report that rather than
  hiding it.

## Backup

Export the telemetry CSV, the annual model CSV and the run manifest from Tab 4 before
the presentation. Every number is reproducible from the seed recorded in the manifest.
