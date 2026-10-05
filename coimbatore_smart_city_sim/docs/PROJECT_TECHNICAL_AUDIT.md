# Project Technical Audit

Forensic read-only audit of the current codebase, describing what is *actually*
implemented, independent of original intentions.

## Legend

- **[MEASURED]** taken from a cited published, regulatory or satellite source
- **[HARDCODED]** working, but resting on static embedded data
- **[MODELLED]** computed by a documented physical or operational model
- **[CALIBRATED]** two coefficients tuned to reproduce a stated result; not measured
- **[SYNTHETIC]** generated, not observed
- **[MISSING]** not present

## 1. Architecture and stack

- **[IMPLEMENTED]** Streamlit dashboard (`app.py`), four tabs, session-state driven.
- **[IMPLEMENTED]** pandas for history and tabular output; Plotly for all charts.
- **[IMPLEMENTED]** Map is hand-written Leaflet markup in an isolated iframe.
- **[IMPLEMENTED]** Numerics use the standard library only.
- **[REMOVED]** `folium`, `streamlit-folium`, `numpy` were declared but never imported;
  they have been dropped from `requirements.txt`.

## 2. Simulation engine

- **[IMPLEMENTED]** Discrete-event engine, 30-minute steps, 48 per day.
- **[HARDCODED]** Diurnal cloud cover is a seeded uniform draw per hour. There is no
  hour-to-hour or day-to-day weather persistence.
- **[HARDCODED]** The clear-sky solar profile is an embedded hourly dictionary.
- **[MODELLED]** Annual model integrates all 8,760 hours with an independent weather draw
  per hour, matching generation against demand hour by hour.

## 3. Energy accounting

- **[IMPLEMENTED]** `direct_use + curtailed = generation` and
  `direct_use + grid_import = demand` hold at every step.
- **[IMPLEMENTED]** Traction demand is derived from the fleet model, not from a separate
  demand curve. The two can no longer disagree.
- **[IMPLEMENTED]** Curtailed energy is excluded from both carbon and tariff savings.
- **[FIXED]** An earlier revision credited carbon against total generation,
  overstating the annual figure by 61% (17,517 t instead of 10,881 t).

## 4. Fleet and routing

- **[HARDCODED]** Corridor geometry is embedded OSRM waypoints. No routing call at runtime.
- **[MODELLED]** Fleet allocation is proportional by the largest-remainder method, so it
  sums exactly to the fleet size at any slider setting.
- **[MODELLED]** Reported traction power equals the energy the fleet actually drew,
  verified to within 0.001% over a full day.
- **[IMPLEMENTED]** Pack capacity affects the state-of-charge drain rate. The slider is
  no longer cosmetic.
- **[IMPLEMENTED]** Low-state-of-charge buses divert along their corridor toward the
  nearest point passing a hub.

## 5. Swap scheduling

- **[MODELLED]** Eligibility requires service, sub-threshold SOC and hub proximity;
  candidates are served lowest-SOC first.
- **[MODELLED]** Funding is solar-first from the step's surplus, with a documented grid
  fallback for buses below 15% SOC. Both tiers are reported separately.
- **[IMPLEMENTED]** Pack inventory is conserved exactly at every hub and step.
- **[DOCUMENTED]** The funding tier records the scheduling decision at the instant of
  approval; the pack's charging energy is drawn on the following step, when the balance
  may differ. The energy balance remains authoritative for sourcing.
- **[IMPLEMENTED]** Charger-bay contention is modelled and respected.
- **[MISSING]** Station-level electrical constraints: feeder capacity, transformer limits,
  demand charges.

## 6. Ecological model

- **[MODELLED]** Standard dissolved-oxygen budget with APHA-form saturation, re-aeration,
  light-driven photosynthesis, Q10 respiration, algae-suppression credit and aeration
  hysteresis.
- **[MODELLED]** Mass conservation is structural: the re-aeration term makes the budget an
  attractor. Verified stationary within 10 days.
- **[CALIBRATED]** `shade_effectiveness = 2.1` and `gross_photosynthesis_mg_l_per_hr` are
  dashboard runs a sensitivity analysis over it; 15% compliance survives plus or minus 20%
  and fails at +40%.
- **[MODELLED]** Minimum dissolved oxygen decreases monotonically with coverage.
- **[SYNTHETIC]** Turbidity and pH are display indicators and are never fed back.
- **[IMPLEMENTED]** Every figure is reported at the diurnal steady state after a 10-day
  spin-up, because a midnight-to-midnight window would otherwise measure the initial
  condition rather than the coverage effect.
- **[MISSING]** Stratification, sediment oxygen demand, hydrodynamic transport, species
  requirements, any calibration against Ukkadam Lake measurements.

## 7. Economics

- **[MODELLED]** Savings use a non-overlapping basis: tariff on self-consumed energy,
  diesel pro rata to the traction share actually served.
- **[FIXED]** An earlier revision counted both in full and double-counted the same
  electron-mile.
- **[ASSUMED]** Array CAPEX, pack pricing, hub construction cost, repack interval,
  discount rate and diesel price are editable assumptions.
- **[MODELLED]** NPV, payback and cumulative cash flow include periodic repacks.
- **[MISSING]** Degradation curve, financing structure, tax treatment, decommissioning.

## 8. External integrations

- **[IMPLEMENTED]** OpenStreetMap raster tiles, browser side, no key.
- **[IMPLEMENTED]** Leaflet from a CDN, browser side.
- **[IMPLEMENTED]** NASA POWER irradiance cross-check, cached to disk, with an offline
  SRRA/MNRE fallback. Used only for validation, never in the live path.
- **[MISSING]** Live weather, traffic, GTFS or AVL integration.
- **[MISSING]** Any API key requirement; the project runs fully offline.

## 9. Software hygiene

- **[IMPLEMENTED]** 342 automated tests covering determinism, conservation, SOC bounds,
  route allocation, inventory accounting, swap tiers, solar physics, oxygen monotonicity
  and stationarity.
- **[IMPLEMENTED]** A validation script with 24 physical invariants that exits non-zero on
  failure.
- **[IMPLEMENTED]** All derived computations cached by scenario tuple.
- **[IMPLEMENTED]** Random seed exposed in the UI and recorded in an exported run manifest,
  so every published figure is reproducible.
- **[IMPLEMENTED]** Every headline figure carries a measured / calibrated / assumed /
  modelled provenance tag.
- **[KNOWN]** `st.components.v1.html` is deprecated in favour of `st.iframe`, which
  accepts a URL rather than markup. Streamlit is pinned below 2.0 because that shim is
  expected to be removed there.
- **[KNOWN]** All state is ephemeral in Streamlit session memory; there is no database.

## 10. Verification performed

| Check | Result |
|---|---|
| `python -m pytest -q` | 342 passed |
| `python scripts/validate_results.py` | all checks passed, exit 0 |
| `python -m src.simulation.engine` | clean |
| Headless `streamlit run` | serves 200, no runtime errors or deprecation warnings |
| App execution with stubbed widgets across all sidebar paths | no exceptions |
| Leaflet JavaScript syntax and runtime (Node, stubbed Leaflet) | valid; render correct at all steps |
| Energy-balance closure across coverage, season and slider matrix | 0 violations |
| Pack inventory conservation across the same matrix | 0 violations |
| No bus stranded across the same matrix | minimum SOC 5.0%, never at the floor |
