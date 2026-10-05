# Simulation model

## Time

48 fixed 30-minute steps represent one day. A scenario is deterministic for a given
seed: the same configuration and seed reproduce every reported quantity exactly.

## Solar

An embedded SRRA/MNRE-style hourly clear-sky profile, linearly interpolated to
half-hourly resolution, scaled by coverage, attenuated by cloud cover (capped at 80%,
since overcast still delivers diffuse light) and derated by module temperature using the
standard -0.4 %/degC coefficient above 25 degC. Floating modules are modelled with a
smaller cell-temperature rise than a ground mount, so the floating advantage appears as
reduced derating rather than output above nameplate.

## Fleet

Buses are distributed across the four corridors in proportion to their design share,
using the largest-remainder method so the allocation sums exactly to the fleet size.
Each bus has deterministic progress, a 16-21 km/h cohort speed, a fixed efficiency factor
and battery-aware SOC depletion.

Traction energy comes from a single normalised hourly profile whose weights sum to
exactly `DAILY_CONSUMPTION_KWH` at unity traffic intensity. Reported traction power and
SOC drain are both derived from it, so they cannot disagree.

## Fleet energy accounting

`DAILY_CONSUMPTION_KWH` is the **nominal** per-bus figure at traffic factor 1.0. The
realised value is that figure multiplied by each vehicle's efficiency cohort factor
(0.94 to 1.06), because `bus_step_energy_kwh` applies both. A bus spending any step
mid-swap draws no traction energy, so realised fleet consumption sits a little below the
nominal total; that shortfall is reported rather than reconciled away, and the reported
traction power always equals the energy actually drawn.

## Swaps

A bus is eligible when it is in service, below the swap threshold and within the hub's
service radius. Eligible buses are served lowest-SOC first and funded in two tiers:

- **solar** — the step's generation surplus pays for the pack refill, so surplus genuinely
  limits how many swaps the array can fund;
- **grid** — a documented fallback reserved for buses below the critical threshold, so
  energy scarcity cannot strand a vehicle.

Buses outside a hub's radius are flagged for diversion and steered along their own
corridor toward the nearest point that passes that hub.

## Ecology

A dissolved-oxygen budget with temperature-dependent saturation, re-aeration relaxation,
light-driven photosynthesis, Q10 respiration, an algae-suppression credit and a
mechanical-aeration term with hysteresis. Every reported figure is taken at the diurnal
steady state after a 10-day spin-up, at the same 30-minute resolution as the engine.

## Energy, carbon and economics

Generation and demand are matched at every step, so direct use, curtailment and grid
import are separately reported. Carbon and tariff savings are credited only against
self-consumed energy. The annual model integrates all 8,760 hours with an independent
weather draw per hour and matches generation against demand hour by hour.

## What this model is not

Not a dispatch tool, not an hourly year-long dispatch optimisation, and not a calibrated
water-quality simulation. See `ASSUMPTIONS_AND_LIMITATIONS.md`.
