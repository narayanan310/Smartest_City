"""
ecology.py — Lake ecological model for Ukkadam Lake (Periyakulam).

Model structure
---------------
The previous implementation applied a fixed per-hour random dissolved-oxygen
delta plus a linear shading penalty. That structure has two defects:

* It was **not mass-conserving**. The net integral of the diurnal delta table was
  about +0.2 mg/L per day, so an uncovered lake gained oxygen every simulated day
  and would eventually saturate at the 9 mg/L cap.
* The entire difference between the 10% / 15% / 20% scenarios came from a single
  hand-tuned coefficient, with no competing physical effect and no coupling to
  anything else in the model.

This module replaces that with a standard dissolved-oxygen budget:

.. math::

    \\frac{dDO}{dt} = k_2 (DO_{sat}(T) - DO) + P_{net}(t) + R_{resp}(t) + A(t)

where the re-aeration term :math:`k_2 (DO_{sat} - DO)` relaxes dissolved oxygen
toward the temperature-dependent saturation concentration. That relaxation is an
**attractor**: it makes the budget self-correcting, so a lake with no shading
returns to the same daily state instead of drifting. Mass conservation is a
structural property of the model rather than a tuned accident.

Two competing coverage effects are now modelled explicitly, which is what makes
the ecological-economic trade-off meaningful rather than asserted:

* **Deleterious** — panels are optically opaque at the water surface, so they
  suppress submerged macrophyte and algal photosynthesis over the shaded area.
  The magnitude is set by :attr:`EcologyParams.shade_effectiveness`, which is a
  **calibrated site parameter** (see the module docstring in
  ``docs/ASSUMPTIONS_AND_LIMITATIONS.md``), not a measured constant.
* **Beneficial** — shading suppresses the algal bloom, lowering water temperature
  and decomposition respiration, and cooler water holds more dissolved oxygen.
  Both channels are modelled.

The residual risk that dissolved oxygen crosses the 4.0 mg/L regulatory threshold
therefore emerges from the balance of these effects, and the dashboard exposes a
sensitivity analysis over the calibrated coefficient.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────
# DISSOLVED OXYGEN SATURATION
# ─────────────────────────────────────────────────────────
# Standard empirical polynomial for oxygen saturation in clean freshwater
# (APHA Standard Methods / Benson & Krause form), valid roughly 0-40 degC.
def do_saturation_mg_l(water_temp_c: float) -> float:
    """Return the dissolved-oxygen saturation concentration, mg/L, at temperature."""
    t = max(0.0, water_temp_c)
    return (
        14.652
        - 0.41022 * t
        + 0.007991 * t**2
        - 0.000077774 * t**3
    )


def photosynthetically_active_fraction(hour: float) -> float:
    """Normalised daylight intensity (0 at night, 1 at solar noon)."""
    if hour < 6.0 or hour > 18.0:
        return 0.0
    return max(0.0, math.sin(math.pi * (hour - 6.0) / 12.0))


# Backwards-compatible alias retained for earlier revisions and the viva notes.
DO_HOURLY_SHADING_HOURS: Tuple[int, ...] = (10, 11, 12, 13, 14, 15)

#: Initial dissolved oxygen for the eutrophic baseline, mg/L.
DEFAULT_INITIAL_DO_MG_L = 5.5

#: Days of ecological integration discarded before the reported 24-hour window, so
#: that scenario statistics describe the diurnal steady state rather than the
#: transient from the assumed initial condition. See :func:`simulate_do_24h`.
DEFAULT_SPIN_UP_DAYS = 10

#: Integration step shared with :class:`~src.simulation.engine.SimulationEngine`, so
#: the scenario comparison and the live trace resolve diurnal dynamics identically.
DEFAULT_STEP_HOURS = 0.5

#: Default ambient air temperature, degC. Matches the month the engine uses to
#: represent its selected season (April for summer, July for monsoon), so that
#: calling these helpers without an explicit temperature reproduces the live result
#: rather than silently assuming a milder day.
DEFAULT_AMBIENT_SUMMER_C = 32.5


@dataclass(frozen=True)
class EcologyParams:
    """Coefficients of the lake ecological model.

    Defaults reproduce the shipped calibration. Every field is overridable so the
    dashboard can run a sensitivity analysis without editing source.
    """

    # ── Dissolved oxygen budget ────────────────────────────────────────────
    reaeration_k_per_hr: float = 0.028
    gross_photosynthesis_mg_l_per_hr: float = 0.70
    respiration_mg_l_per_hr: float = 0.24
    respiration_q10: float = 1.08
    respiration_ref_temp_c: float = 25.0

    # ── Floating-array coverage coupling ───────────────────────────────────
    #: Multiplier converting shaded area fraction into the fraction of lake-wide
    #: primary production that is lost. Greater than one because the array is sited
    #: over the shallow littoral margin where macrophyte and algal density is
    #: highest, and because an opaque panel at the surface blocks the full photic
    #: depth rather than a proportionate slice of the lake volume.
    #:
    #: This is the single **calibrated** coefficient in the ecological model. It is
    #: not measured, and the coverage-versus-oxygen conclusion is proportional to it.
    #: The dashboard runs a sensitivity analysis over this parameter precisely so
    #: that the dependence is visible rather than hidden.
    shade_effectiveness: float = 2.1
    #: Oxygen credit from suppressed algae (less microbial decomposition),
    #: expressed per unit of production actually lost.
    algae_suppression_credit: float = 0.11

    # ── Mechanical aeration ────────────────────────────────────────────────
    aeration_rate_mg_l_per_hr: float = 0.22
    aeration_on_below_mg_l: float = 3.0
    aeration_off_above_mg_l: float = 4.2

    # ── Bounds ─────────────────────────────────────────────────────────────
    do_max_mg_l: float = 9.0
    do_min_floor_mg_l: float = 2.0

    # ── Window over which panels shade the water surface ───────────────────
    shading_hours: Tuple[int, ...] = (10, 11, 12, 13, 14, 15)

    # ── Water temperature ──────────────────────────────────────────────────
    water_temp_base_c: float = 24.2
    water_temp_ambient_gain: float = 0.55
    water_temp_ambient_ref_c: float = 25.5
    water_temp_diurnal_amplitude_c: float = 1.6
    water_temp_diurnal_peak_hour: float = 15.0
    #: Surface cooling beneath the array during the insolation window, degC at
    #: the 15% reference coverage.
    pv_cooling_delta_c: float = 1.40
    pv_cooling_night_fraction: float = 0.11

    # ── Derived display indicators ─────────────────────────────────────────
    ph_base: float = 7.6
    ph_photosynthetic_rise: float = 0.65
    ph_coverage_penalty: float = 0.15
    turbidity_base_ntu: float = 25.0
    turbidity_coverage_reduction_ntu: float = 4.5
    turbidity_noise_ntu: float = 0.8
    turbidity_min_ntu: float = 12.0


def water_temperature_c(hour: float, coverage_pct: float, ambient_temp_c: float, params: EcologyParams) -> float:
    """Return lake surface temperature in degC.

    Ambient temperature sets the seasonal level, a cosine sets the diurnal swing,
    and the floating array removes heat during the insolation window. This
    temperature then drives oxygen saturation, so coverage has a second, opposite
    pathway to dissolved oxygen.
    """
    hour_rad = (hour - params.water_temp_diurnal_peak_hour) * (2.0 * math.pi / 24.0)
    natural = (
        params.water_temp_base_c
        + (ambient_temp_c - params.water_temp_ambient_ref_c) * params.water_temp_ambient_gain
        + params.water_temp_diurnal_amplitude_c * math.cos(hour_rad)
    )
    coverage_scale = coverage_pct / 15.0
    if 9.0 <= hour <= 17.0:
        cooling = coverage_scale * params.pv_cooling_delta_c
    else:
        cooling = coverage_scale * params.pv_cooling_delta_c * params.pv_cooling_night_fraction
    return round(natural - cooling, 2)


def blocked_production_fraction(hour: float, coverage_pct: float, params: EcologyParams) -> float:
    """Return the fraction (0-1) of lake-wide primary production suppressed by panels."""
    if int(hour) % 24 not in params.shading_hours:
        return 0.0
    shade = max(0.0, coverage_pct) / 100.0
    return min(1.0, shade * params.shade_effectiveness)


def respiration_rate_mg_l_per_hr(water_temp_c: float, params: EcologyParams) -> float:
    """Community respiration with a Q10 temperature response."""
    return params.respiration_mg_l_per_hr * (
        params.respiration_q10 ** ((water_temp_c - params.respiration_ref_temp_c) / 10.0)
    )


@dataclass
class EcologicalSnapshot:
    """Snapshot of ecological parameters at one simulation step."""

    hour: int
    do_level: float
    do_delta: float
    water_temp_c: float
    ph_level: float
    turbidity_ntu: float
    coverage_pct: float
    aeration_on: bool
    status: str
    message: str


def update_ecology(
    current_do: float,
    hour: float,
    coverage_pct: float,
    params: EcologyParams,
    aeration_on: bool = False,
    step_hours: float = 0.5,
    rng: Optional[random.Random] = None,
    ambient_temp_c: float = 30.0,
) -> Tuple[float, float, bool, float, float, float]:
    """Advance all lake ecological parameters by one simulation step.

    Returns:
        ``(new_do, do_delta, new_aeration, water_temp_c, ph_level, turbidity_ntu)``
    """
    if step_hours <= 0.0:
        raise ValueError(f"step_hours must be positive, got {step_hours!r}")

    # NOTE: the hour index is derived inside each sub-model from `hour` itself
    # (see blocked_production_fraction); it used to be computed here as `h_int` and
    # never read, which invited the belief that a single cached hour was being used.
    rng = rng or random.Random()

    # 1. Water temperature first: it sets the oxygen saturation target.
    temp_c = water_temperature_c(hour, coverage_pct, ambient_temp_c, params)
    saturation = do_saturation_mg_l(temp_c)

    # 2. Dissolved oxygen budget, integrated over the step.
    light = photosynthetically_active_fraction(hour)
    gross_photo = params.gross_photosynthesis_mg_l_per_hr * light
    blocked = blocked_production_fraction(hour, coverage_pct, params)
    production_loss = gross_photo * blocked
    algae_credit = production_loss * params.algae_suppression_credit

    reaeration = params.reaeration_k_per_hr * (saturation - current_do)
    respiration = respiration_rate_mg_l_per_hr(temp_c, params)
    aeration_bonus = params.aeration_rate_mg_l_per_hr if aeration_on else 0.0

    # Small stochastic term represents unresolved turbulent mixing and
    # patchiness. It is symmetric and unbiased, so it cannot create drift.
    mixing_noise = rng.uniform(-0.012, 0.012)

    rate_mg_l_per_hr = (
        reaeration + gross_photo - production_loss + algae_credit - respiration
        + aeration_bonus + mixing_noise
    )
    total_delta = rate_mg_l_per_hr * step_hours
    new_do = max(params.do_min_floor_mg_l, min(params.do_max_mg_l, current_do + total_delta))

    # 3. Aeration hysteresis: engage below the emergency threshold, release only
    #    once dissolved oxygen has recovered past the safe threshold.
    if new_do < params.aeration_on_below_mg_l:
        new_aeration = True
    elif new_do >= params.aeration_off_above_mg_l:
        new_aeration = False
    else:
        new_aeration = aeration_on

    # 4. pH. Photosynthesis consumes dissolved CO2, raising pH; respiration
    #    releases it. Net panel effect is a slight downward bias because the
    #    shaded zone respires rather than photosynthesises.
    ph_level = round(
        params.ph_base
        + params.ph_photosynthetic_rise * light
        - (max(0.0, coverage_pct) / 100.0) * params.ph_coverage_penalty,
        2,
    )

    # 5. Turbidity. Eutrophic baseline, reduced by algae suppression under panels.
    turbidity_ntu = round(
        max(
            params.turbidity_min_ntu,
            params.turbidity_base_ntu
            - (max(0.0, coverage_pct) / 15.0) * params.turbidity_coverage_reduction_ntu
            + rng.uniform(-params.turbidity_noise_ntu, params.turbidity_noise_ntu),
        ),
        1,
    )

    return (
        round(new_do, 3),
        round(total_delta, 3),
        new_aeration,
        temp_c,
        ph_level,
        turbidity_ntu,
    )


def check_ecology(do_level: float, coverage_pct: float) -> Tuple[str, str]:
    """Evaluate ecological health status.

    ``CRITICAL`` is reserved for a breach of the 15% policy cap, because that is a
    regulatory finding independent of the instantaneous oxygen reading. The message
    always reports the measured dissolved oxygen so the two are never confused.

    Statuses: ``CRITICAL`` (>cap) | ``EMERGENCY`` (<3.0) | ``WARNING`` (<4.0) | ``OK``
    """
    if coverage_pct > 15.0:
        return (
            "CRITICAL",
            f"⛔ Coverage {coverage_pct:.1f}% exceeds the 15% ecological cap "
            f"(current DO = {do_level:.2f} mg/L). Non-compliant scenario.",
        )
    if do_level < 3.0:
        return (
            "EMERGENCY",
            f"🚨 DO = {do_level:.2f} mg/L (<3 mg/L)! Mechanical aeration activated to prevent fish kill.",
        )
    if do_level < 4.0:
        return (
            "WARNING",
            f"⚠️ DO = {do_level:.2f} mg/L (<4 mg/L threshold). Dissolved oxygen depressed; monitoring closely.",
        )
    return (
        "OK",
        f"✅ DO = {do_level:.2f} mg/L (healthy, ≥4 mg/L). 15% coverage cap strictly maintained.",
    )


def simulate_do_24h(
    initial_do: float,
    coverage_pct: float,
    params: EcologyParams,
    seed: int = 99,
    step_hours: float = DEFAULT_STEP_HOURS,
    ambient_temp_c: float = DEFAULT_AMBIENT_SUMMER_C,
    spin_up_days: int = DEFAULT_SPIN_UP_DAYS,
) -> List[Dict]:
    """Run a full 24-hour ecological simulation for scenario comparison.

    Two properties matter for this function to be comparable with the live engine:

    * It integrates ``DEFAULT_STEP_HOURS`` (30 minutes), the **same** resolution as
      :class:`~src.simulation.engine.SimulationEngine`, so the scenario table in the
      dashboard and the live dissolved-oxygen trace cannot disagree by more than the
      reporting precision. Earlier revisions integrated whole hours here and the two
      tabs consequently reported visibly different minima for identical inputs.
    * It discards ``spin_up_days`` days before the reported window, because panels
      only shade the water between 10:00 and 15:00. A window that starts at midnight
      reaches its dissolved-oxygen minimum near 07:00, **before** that day's shading
      has had any effect, so measuring a single midnight-to-midnight window reports
      a minimum driven by the initial condition rather than by lake coverage.

    Args:
        initial_do: Dissolved oxygen at 00:00 of the first day, mg/L.
        coverage_pct: Lake surface coverage percentage.
        params: Ecological model coefficients.
        seed: RNG seed for the turbulent-mixing term.
        step_hours: Integration step in hours; ``24 / step_hours`` steps per day.
        ambient_temp_c: Ambient air temperature for the day, °C.
        spin_up_days: Days to integrate and discard before the reported day.
    """
    if spin_up_days < 0:
        raise ValueError("spin_up_days must be non-negative")
    if step_hours <= 0.0:
        raise ValueError("step_hours must be positive")

    steps_per_day = int(round(24.0 / step_hours))
    rng = random.Random(seed)
    do = initial_do
    aeration = False

    def advance(hour_index: int) -> Tuple[float, bool]:
        nonlocal do, aeration
        hour = hour_index * step_hours
        do, _, aeration, _, _, _ = update_ecology(
            do, hour, coverage_pct, params, aeration,
            step_hours=step_hours, rng=rng, ambient_temp_c=ambient_temp_c,
        )
        return do, aeration

    for _ in range(spin_up_days):
        for step_index in range(steps_per_day):
            advance(step_index)

    results: List[Dict] = []
    for step_index in range(steps_per_day):
        hour = step_index * step_hours
        step_start_do = do
        new_do, delta, new_aeration, w_temp, ph, turb = update_ecology(
            step_start_do,
            hour,
            coverage_pct,
            params,
            aeration,
            step_hours=step_hours,
            rng=rng,
            ambient_temp_c=ambient_temp_c,
        )
        status, message = check_ecology(new_do, coverage_pct)
        results.append({
            "hour": hour,
            "time_str": _format_clock(hour),
            "do_level": new_do,
            "delta": delta,
            "water_temp_c": w_temp,
            "ph_level": ph,
            "turbidity_ntu": turb,
            "aeration_on": new_aeration,
            "status": status,
            "message": message,
        })
        do = new_do
        aeration = new_aeration

    return results


def _format_clock(hour: float) -> str:
    """Format a fractional hour as a 24-hour ``HH:MM`` string."""
    total_minutes = int(round(hour * 60.0)) % (24 * 60)
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def coverage_status_summary(
    coverage_pct: float, params: EcologyParams, initial_do: Optional[float] = None,
    seed: int = 42, ambient_temp_c: float = DEFAULT_AMBIENT_SUMMER_C,
) -> Dict:
    """Return an ecological summary for one coverage scenario."""
    if initial_do is None:
        initial_do = DEFAULT_INITIAL_DO_MG_L
    do_results = simulate_do_24h(
        initial_do, coverage_pct, params, seed=seed, ambient_temp_c=ambient_temp_c
    )
    levels = [r["do_level"] for r in do_results]
    min_do = min(levels)
    max_do = max(levels)
    aeration_hours = sum(1 for r in do_results if r["aeration_on"])

    if coverage_pct > 15.0:
        status = "CRITICAL / OVER CAP"
    elif min_do < 3.0:
        status = "EMERGENCY"
    elif min_do < 4.0:
        status = "WARNING"
    else:
        status = "OK"

    return {
        "coverage_pct": coverage_pct,
        "min_do": round(min_do, 2),
        "max_do": round(max_do, 2),
        "final_do": round(levels[-1], 2),
        "net_do_change": round(levels[-1] - initial_do, 3),
        "aeration_hours": aeration_hours,
        "ecological_status": status,
        "within_threshold": min_do >= 4.0,
    }
