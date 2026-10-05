# Assumptions and Limitations

Read this before citing any number from the dashboard, the reports or the exports.
This is an academic operations model. It is not an engineering design, a dispatch
tool, or a financial forecast.

---

## 1. Lake ecology — a heuristic budget, not a calibrated model

The dissolved-oxygen model is a standard budget,

```
dDO/dt = k2 * (DO_sat(T) - DO)  +  P_net(t)  +  R_resp(T)  +  A(t)
```

with temperature-dependent saturation from the APHA standard-methods polynomial, a
re-aeration relaxation, light-driven photosynthesis, Q10-scaled respiration, and a
mechanical-aeration term with hysteresis (engage below 3.0 mg/L, release above
4.2 mg/L).

**What this structure buys.** The re-aeration term makes the budget an attractor, so an
uncovered lake converges to a stationary diurnal cycle instead of drifting. Verified
over 20 simulated days: the daily minimum moves by less than 0.03 mg/L between day 10
and day 20.

**What it is not.** It has never been calibrated against Ukkadam Lake measurements. It
has no vertical structure, no stratification, no sediment oxygen demand, no
hydrodynamics, and no species-specific oxygen requirements.

### The two calibrated coefficients

`EcologyParams.shade_effectiveness = 2.1` converts shaded area fraction into the
fraction of lake-wide primary production lost, and
`EcologyParams.gross_photosynthesis_mg_l_per_hr` sets the unshaded production rate.
Both are **calibrated, not measured**. `scripts/fit_shading.py` sweeps every ecology
coefficient over its plausible range and ranks them by how far each one moves the
headline minimum, which corrected an earlier draft of this document: it had named
`shade_effectiveness` as the single weak point, but the ranking shows gross
photosynthesis has the larger effect on the absolute minimum.

| Coefficient | Effect on minimum DO at 15% | Shipped value |
|---|---|---|
| `gross_photosynthesis_mg_l_per_hr` | **3.08 mg/L** across 0.5–2.5 | 0.7 |
| `shade_effectiveness` | **1.83 mg/L** across 1.0–4.0 | 2.1 |

Shading dominates the *direction* of the coverage response, which is why the coverage
ladder is robust, but a calibration against a real observation should constrain both. The
value above 1 for `shade_effectiveness` encodes two real effects — the array sits over
the shallow littoral margin where macrophyte density is highest, and an opaque surface
panel blocks the full photic depth rather than a proportionate slice of lake volume — but
the specific number remains a modelling choice. Full ranking, inverted values against a
4.0 and 5.0 mg/L target, and the sensitivity grid are in
[`ECOLOGY_CALIBRATION.md`](ECOLOGY_CALIBRATION.md).

The dashboard runs a sensitivity analysis over it, and the coverage-versus-oxygen
conclusion is proportional to it:

| `shade_effectiveness` | 10% | 15% | 20% | 25% | 15% compliant? |
|---:|---:|---:|---:|---:|:--|
| 1.26 (0.6x) | 5.06 | 4.84 | 4.62 | 4.40 | yes |
| 1.68 (0.8x) | 4.89 | 4.58 | 4.28 | 3.98 | yes |
| **2.10 (1.0x)** | **4.72** | **4.33** | **3.94** | **3.56** | **yes** |
| 2.52 (1.2x) | 4.55 | 4.08 | 3.60 | 3.13 | yes |
| 2.94 (1.4x) | 4.38 | 3.82 | 3.26 | 3.23 | **no** |

So the 15% conclusion survives a plus or minus 20% perturbation of the calibrated
coefficient and fails at +40%. **State this when presenting.** The *ordering* of
scenarios is robust regardless: a linear shading term fixes the sensitivity ratio
between adjacent coverage steps by geometry, not by tuning.

### Structural simplifications

- **Response to coverage is linear** in the shaded area, so the dissolved-oxygen
  difference between 0 and 15% is always about three times that between 15 and 20%. A
  real lake would saturate as the littoral zone is progressively shaded.
- **No compensating benefit beyond what is modelled.** Panel shading does suppress the
  algal bloom (credited as reduced respiration and cooler water), but the model does not
  credit the clearer water's improved light penetration outside the array.
- **Temperature and pH are indicators, not state variables.** Water temperature is a
  closed-form function of ambient temperature, time of day and coverage; it does not
  integrate heat or wind. That is sufficient because it drives the oxygen saturation
  target, but it would not survive a transient weather event.
- **Turbidity and pH are display-only.** They are reported, never fed back.

### Spin-up

Every reported figure is taken at the **diurnal steady state**, after discarding a
10-day transient. This matters more than it sounds: panels only shade the lake between
10:00 and 15:00, and the daily oxygen minimum occurs near 07:00. A window that starts
at midnight therefore reaches its minimum *before* that day's shading has had any
effect, so measuring a single midnight-to-midnight window reports a minimum driven by
the initial condition rather than by lake coverage. Both the live engine and the
scenario comparison apply the spin-up and use the same 30-minute step, which is why they
agree to within 0.05 mg/L.

---

## 2. Solar generation

- The hourly clear-sky profile is **embedded, not measured**, and represents a
  representative clear day rather than a specific date.
- Cell temperature uses a fixed +8 degC rise above ambient for a floating array against
  +22 degC for a ground mount, with the standard -0.4 %/degC coefficient. The floating
  advantage therefore appears as *less derating*, never as a gain above nameplate.
  Output is **below** nameplate in hot conditions, as it must be. An earlier revision
  multiplied output by a factor that *increased* generation as ambient temperature rose;
  that inverted sign has been removed.
- Cloud cover is drawn independently for each hour from a seeded uniform distribution.
  There is **no hour-to-hour or day-to-day persistence**, so the weather is more variable
  than reality and inter-day curtailment is understated.
- Cloud attenuation is capped at 80%, so full overcast still yields about 20% of the
  clear-sky profile from diffuse light.
- The analytic planning estimate (`Config.annual_generation_mwh`, specific-yield method)
  and the hourly annual model disagree by about 3% (21.96 vs 21.36 GWh). Both are
  reported and the hourly model is the headline. The residual comes from the analytic
  method's measured specific yield versus the hourly model's synthetic cloud.

---

## 3. Fleet and operations

- **No GTFS, GPS, traffic or passenger-demand feed.** Corridor geometry, stops, station
  positions and arrival patterns are static and synthetic.
- Bus speeds are deterministic 16-21 km/h cohorts, not traffic-responsive. The traffic
  slider scales traction energy arithmetically and divides speed; it is not a congestion
  model.
- `DAILY_CONSUMPTION_KWH = 219` is valid **at traffic factor 1.0**. The hourly shape is
  normalised so the profile sums to exactly that figure rather than inflating it by the
  peak and off-peak weighting.
- State-of-charge depletion **is** battery-capacity aware, so the pack-capacity slider
  genuinely changes fleet behaviour. A 150 kWh pack drains faster in percentage terms
  than a 450 kWh pack for the same energy.
- Diverting buses steer toward the nearest waypoint on their own corridor that passes
  the target hub. That is a real movement, but it is not a road-network route plan.
- SOC is floored at 5%, and hubs begin each day fully charged, so the swap network
  cannot strand a bus by construction. Verified across the whole coverage, season and
  slider matrix.
- **Swaps are gated on solar surplus but have a documented grid fallback.** A swap is
  funded from the step's surplus where surplus exists. Buses below 15% SOC are topped up
  from the grid regardless, so energy scarcity cannot strand a vehicle. Both tiers are
  counted and displayed separately; neither is hidden. At the default scenario the array
  funds 77 of 168 daily swaps.
- **The funding tier is a scheduling decision, not a meter reading.** A swap is labelled
  solar or grid using the surplus visible at the instant of approval, but the pack's
  charging energy is drawn on the *following* step, when the balance may differ. On the
  default day this affects a single step. The energy balance remains authoritative for
  sourcing; the tier records the operator's intent.
- Pack inventory and charger-bay contention are modelled. Station-level electrical
  constraints (feeder capacity, transformer limits, demand charges) are not.

---

## 4. Energy, carbon and economics

- **Carbon and tariff savings are credited only against self-consumed energy.** Curtailed
  output displaces nothing. Crediting total generation would report 17,517 t/yr instead
  of 10,881 t/yr, a 61% overstatement. This was a defect in an earlier revision.
- The annual model integrates all 8,760 hours with an independent weather draw per hour,
  matching generation against demand hour by hour.
- The CO2 factor (0.82 kg/kWh, CEA India) and the diesel factor (2.68 kg/L) are **grid
  displacement only**. On-site combustion emissions are excluded.
- **Fleet demand is flat across the year** at 260 x 219 kWh/day, with no seasonality.
- Municipal load is a two-level step (1,600 kW daytime, 250 kW overnight). It is not
  derived from the three named buildings' individual loads.
- **Tab 3 credits each self-consumed kilowatt-hour exactly once.** Self-consumed solar
  is split by the load it serves: the traction share displaces the diesel an all-diesel
  baseline would have burned and is valued at the implied fuel cost per kWh
  (Rs 28.69/kWh at defaults), while the municipal share displaces grid purchase and is
  valued at retail plus surcharge (Rs 8.54/kWh). Because the fuel value is 3.4x the
  retail tariff, getting this wrong is expensive: two earlier revisions overstated the
  annual benefit by roughly Rs 19.5 Cr, or 65%.
- **The simulated fleet is 100% electric.** No diesel vehicle exists anywhere in the
  operational model -- `models/bus.py`, `simulation/engine.py` and
  `algorithms/scheduler.py` contain no diesel reference at all, and this is asserted by
  a test. Diesel appears solely as the counterfactual baseline the project replaces, and
  is therefore the dominant benefit term. A cost-benefit analysis of an electric-fleet
  conversion cannot be stated without that baseline; what it must not do is count the same
  kilowatt-hour twice.
- Grid-displacement CO2 (0.82 kg/kWh) and diesel combustion CO2 (2.68 kg/L) are
  reported as **separate scopes** and are never summed. At defaults the grid basis gives
  10,881 t/yr and the fuel basis 7,100 t/yr avoided against a 15,987 t/yr baseline.
- Swap-hub civil and electrical CAPEX, pack CAPEX and periodic whole-fleet repacks are
  included and editable, but pack and hub pricing are assumptions.
- Economics assume all 260 buses were previously diesel.

---

## 5. Software limitations

- `st.components.v1.html` is deprecated in favour of `st.iframe`, which accepts a URL
  rather than markup, so it remains the only way to host an isolated Leaflet document.
  Streamlit is pinned below 2.0 because that shim is expected to be removed there.
- All simulation state is ephemeral and lives in Streamlit session memory. There is no
  database and no persistence between sessions.
- The random seed is exposed in the sidebar and recorded in the exported run manifest, so
  any figure in this repository can be reproduced exactly.