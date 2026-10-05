"""
solar.py — Photovoltaic generation model for the Ukkadam floating array.

Physical basis
--------------
Generation at a given hour is the nameplate DC profile attenuated by two effects:

1. **Cloud attenuation.** Beam irradiance is scattered by cloud, but a substantial
   diffuse component still reaches the panel, so output is not extinguished
   linearly. Attenuation is capped at :data:`CLOUD_MAX_ATTENUATION` (80%), i.e.
   100% overcast still yields 20% of the clear-sky profile.

2. **Cell-temperature derate.** Silicon output falls as cell temperature rises
   above the 25 °C standard-test-condition reference. The manufacturer power
   temperature coefficient is typically about **-0.4 %/°C**.

   Floating arrays do not generate *more* power because the air is warm. They
   generate more power than an equivalent **ground-mounted** array because the
   water body removes heat, so the module runs cooler. This module therefore models
   the cooling explicitly as a reduced cell-temperature rise above ambient
   (:data:`CELL_TEMP_RISE_FLOATING_C` versus :data:`CELL_TEMP_RISE_GROUND_C`) and
   then applies the same physical derate to both. See
   :func:`floating_cooling_premium` for the resulting relative benefit.

   The previous implementation multiplied output by ``1 + 0.005 * (T - 25) * 0.5``,
   which made hot summer afternoons *increase* generation. That inverted sign is
   physically wrong and has been removed.
"""

from __future__ import annotations

import random
from typing import Dict, Optional

#: Standard-test-condition reference temperature for module rating (°C).
STC_REFERENCE_TEMP_C = 25.0

#: Fraction of clear-sky output lost at 100% cloud cover. The remainder is diffuse.
CLOUD_MAX_ATTENUATION = 0.80

#: Module maximum-power temperature coefficient, fraction per °C (typical silicon).
PMAX_TEMP_COEFF_PER_C = -0.004

#: Modelled cell-temperature rise above ambient for a floating array. The water
#: body acts as a heat sink, so this is much lower than for a ground mount.
CELL_TEMP_RISE_FLOATING_C = 8.0

#: Modelled cell-temperature rise above ambient for a ground-mounted array.
CELL_TEMP_RISE_GROUND_C = 22.0


def cell_temperature_c(
    ambient_temp_c: float, cell_temp_rise_c: float = CELL_TEMP_RISE_FLOATING_C
) -> float:
    """Return module cell temperature for an ambient temperature."""
    return ambient_temp_c + cell_temp_rise_c


def temperature_derate(
    ambient_temp_c: float,
    cell_temp_rise_c: float = CELL_TEMP_RISE_FLOATING_C,
    temp_coeff_per_c: float = PMAX_TEMP_COEFF_PER_C,
    reference_temp_c: float = STC_REFERENCE_TEMP_C,
) -> float:
    """Return the fractional output multiplier from module temperature.

    Applies the power temperature coefficient above the reference temperature and
    clamps at zero so extreme conditions cannot produce negative output.
    """
    t_cell = cell_temperature_c(ambient_temp_c, cell_temp_rise_c)
    return max(0.0, 1.0 + temp_coeff_per_c * max(0.0, t_cell - reference_temp_c))


def floating_cooling_premium(ambient_temp_c: float) -> float:
    """Relative yield of the floating array versus an identical ground mount (ratio).

    This is the honest form of the "floating PV runs cooler" effect: an absolute
    derate below 1.0 for both mount types, with floating retaining more of nameplate.
    """
    floating = temperature_derate(ambient_temp_c, CELL_TEMP_RISE_FLOATING_C)
    ground = temperature_derate(ambient_temp_c, CELL_TEMP_RISE_GROUND_C)
    if ground <= 0.0:
        return 1.0
    return floating / ground


def cloud_attenuation(cloud_cover_pct: float) -> float:
    """Return the clear-sky transmission factor for a cloud-cover percentage."""
    return 1.0 - (max(0.0, min(100.0, cloud_cover_pct)) / 100.0) * CLOUD_MAX_ATTENUATION


def scale_base_profile(
    base_profile: Dict[int, float], coverage_pct: float, reference_pct: float = 15.0
) -> Dict[int, float]:
    """Scale the reference (13 MWp at 15% coverage) hourly profile to another scenario."""
    if reference_pct <= 0.0:
        return dict(base_profile)
    ratio = coverage_pct / reference_pct
    return {hour: kw * ratio for hour, kw in base_profile.items()}


def interpolated_profile_kw(base_profile: Dict[int, float], hour: float, coverage_pct: float) -> float:
    """Linearly interpolate the (coverage-scaled) hourly profile at a fractional hour."""
    scaled = scale_base_profile(base_profile, coverage_pct)
    h_floor = int(hour) % 24
    h_ceil = (h_floor + 1) % 24
    fraction = float(hour) - int(hour)

    kw_floor = scaled.get(h_floor, 0.0)
    kw_ceil = scaled.get(h_ceil, 0.0)
    return kw_floor + (kw_ceil - kw_floor) * fraction


def calc_generation(
    hour: float,
    base_profile: Dict[int, float],
    max_kw: float,
    cloud_cover: float = 0.0,
    temperature: float = 30.0,
    coverage_pct: float = 15.0,
) -> float:
    """Return AC-side generation in kW for the given hour.

    Args:
        hour:           Hour of day, fractional values such as ``11.5`` supported.
        base_profile:   ``{hour: kW}`` clear-sky profile for the reference scenario.
        max_kw:         Installed capacity ceiling for this scenario, in kW.
        cloud_cover:    Cloud cover percentage, 0–100.
        temperature:    Ambient air temperature, °C.
        coverage_pct:   Lake surface coverage percentage (scales the profile).
    """
    base_kw = interpolated_profile_kw(base_profile, hour, coverage_pct)
    if base_kw <= 0.0:
        return 0.0

    derate = temperature_derate(temperature, CELL_TEMP_RISE_FLOATING_C)
    output_kw = base_kw * cloud_attenuation(cloud_cover) * derate
    return max(0.0, min(output_kw, max_kw))


def get_cloud_cover(hour: int, season: str = "summer", seed: Optional[int] = None) -> float:
    """Return a cloud-cover percentage for Coimbatore.

    Args:
        hour:   Hour of day; monsoon cloud thickens in the afternoon.
        season: ``"summer"`` (Nov–May) or ``"monsoon"`` (Jun–Sep).
        seed:   RNG seed, giving a deterministic but reproducible realisation.
    """
    rng = random.Random(seed)

    if season == "monsoon":
        base_min, base_max = 40.0, 85.0
        afternoon_boost = 10.0 if 13 <= hour <= 17 else 0.0
        morning_clear = -10.0 if 6 <= hour <= 8 else 0.0
        lo = min(100.0, base_min + afternoon_boost + morning_clear)
        hi = min(100.0, base_max + afternoon_boost + morning_clear)
    else:
        base_min, base_max = 5.0, 20.0
        afternoon_boost = 5.0 if 14 <= hour <= 16 else 0.0
        lo = base_min
        hi = min(100.0, base_max + afternoon_boost)

    return round(rng.uniform(lo, hi), 1)


def daily_generation_kwh(
    base_profile: Dict[int, float],
    max_kw: float,
    cloud_cover_by_hour: Optional[Dict[int, float]] = None,
    temperature: float = 30.0,
    coverage_pct: float = 15.0,
) -> float:
    """Total daily generation in kWh, assuming the hourly profile holds all day."""
    total = 0.0
    for hour in range(24):
        cloud = cloud_cover_by_hour.get(hour, 0.0) if cloud_cover_by_hour else 0.0
        total += calc_generation(hour, base_profile, max_kw, cloud, temperature, coverage_pct)
    return round(total, 2)


def annual_generation_mwh(
    base_profile: Dict[int, float],
    max_kw: float,
    season_months: Optional[Dict[int, str]] = None,
    coverage_pct: float = 15.0,
) -> float:
    """Estimate annual generation in MWh using 12 representative monthly days.

    This is a fast screening estimate that multiplies one representative day per
    month by the number of days in that month. The authoritative annual figure
    comes from :meth:`SimulationEngine.run_annual_simulation`, which integrates
    every hour of all 365 days with an independent daily weather realisation.
    """
    if season_months is None:
        season_months = {
            1: "summer", 2: "summer", 3: "summer", 4: "summer", 5: "summer",
            6: "monsoon", 7: "monsoon", 8: "monsoon", 9: "monsoon",
            10: "summer", 11: "summer", 12: "summer",
        }

    monthly_temps = {
        1: 25.5, 2: 27.0, 3: 30.0, 4: 32.5, 5: 33.0, 6: 30.0,
        7: 29.0, 8: 29.0, 9: 29.5, 10: 28.0, 11: 26.5, 12: 25.0,
    }
    days_in_month = {
        1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31,
    }

    annual_kwh = 0.0
    for month, season in season_months.items():
        cloud_by_hour = {h: get_cloud_cover(h, season, seed=month * 100 + h) for h in range(24)}
        daily_kwh = daily_generation_kwh(
            base_profile, max_kw, cloud_by_hour, monthly_temps[month], coverage_pct
        )
        annual_kwh += daily_kwh * days_in_month[month]

    return round(annual_kwh / 1_000.0, 1)


def evaporation_reduction_pct(coverage_pct: float) -> float:
    """Estimated percentage reduction in lake evaporation from panel shading.

    Literature values for full or near-full covers fall in the 30–70% band; this
    helper linearly interpolates partial coverage and is reported, not simulated.
    """
    return round(min(70.0, max(0.0, coverage_pct) / 100.0 * 150.0), 1)
