"""Tests for the photovoltaic generation model."""

from __future__ import annotations

import pytest

from src.algorithms.solar import (
    CLOUD_MAX_ATTENUATION,
    PMAX_TEMP_COEFF_PER_C,
    STC_REFERENCE_TEMP_C,
    calc_generation,
    cell_temperature_c,
    cloud_attenuation,
    evaporation_reduction_pct,
    floating_cooling_premium,
    get_cloud_cover,
    interpolated_profile_kw,
    scale_base_profile,
    temperature_derate,
)

# ── temperature physics ──────────────────────────────────────────────────

def test_cell_temperature_is_ambient_plus_rise():
    assert cell_temperature_c(30.0, 8.0) == pytest.approx(38.0)


@pytest.mark.parametrize("ambient", [15.0, 25.0, 32.5, 40.0])
def test_derate_follows_the_manufacturer_coefficient(ambient):
    """Output must equal nameplate times the -0.4 %/degC coefficient above 25 degC."""
    for rise in (0.0, 8.0, 22.0):
        t_cell = ambient + rise
        expected = 1.0 + PMAX_TEMP_COEFF_PER_C * max(0.0, t_cell - STC_REFERENCE_TEMP_C)
        assert temperature_derate(ambient, rise) == pytest.approx(expected)


def test_derate_is_unity_at_the_reference_temperature():
    assert temperature_derate(STC_REFERENCE_TEMP_C - 8.0) == pytest.approx(1.0)


def test_derate_stays_within_unity_and_zero():
    for ambient in (-20.0, 0.0, 17.0, 25.0, 40.0, 60.0, 200.0):
        assert 0.0 <= temperature_derate(ambient) <= 1.0
    assert temperature_derate(0.0) == pytest.approx(1.0), "cold cells are not derated"
    # At a 60 degC ambient the cell sits at 68 degC, so about 17% is lost.
    assert temperature_derate(60.0) < 0.85, "extreme heat must derate heavily"


def test_derate_decreases_as_ambient_rises():
    values = [temperature_derate(t) for t in (20.0, 25.0, 30.0, 35.0, 40.0)]
    assert all(a > b for a, b in zip(values, values[1:], strict=False))


def test_floating_array_outperforms_ground_mount():
    """This is the only legitimate sense in which floating PV 'generates more'."""
    for ambient in (25.0, 30.0, 35.0, 40.0):
        assert floating_cooling_premium(ambient) > 1.0
    # The premium is a few per cent, not tens of per cent.
    assert 1.0 < floating_cooling_premium(32.5) < 1.15


# ── cloud attenuation ────────────────────────────────────────────────────

def test_cloud_attenuation_endpoints():
    assert cloud_attenuation(0.0) == pytest.approx(1.0)
    assert cloud_attenuation(100.0) == pytest.approx(1.0 - CLOUD_MAX_ATTENUATION)
    assert cloud_attenuation(50.0) == pytest.approx(1.0 - 0.5 * CLOUD_MAX_ATTENUATION)


def test_cloud_attenuation_clamps_out_of_range_input():
    assert cloud_attenuation(-20.0) == pytest.approx(1.0)
    assert cloud_attenuation(150.0) == pytest.approx(1.0 - CLOUD_MAX_ATTENUATION)


def test_overcast_still_produces_some_power():
    """A uniform overcast sky delivers diffuse light, not zero."""
    assert cloud_attenuation(100.0) > 0.15


# ── profile handling ─────────────────────────────────────────────────────

def test_profile_scales_linearly_with_coverage():
    base = {12: 12_000.0}
    assert scale_base_profile(base, 15.0)[12] == pytest.approx(12_000.0)
    assert scale_base_profile(base, 30.0)[12] == pytest.approx(24_000.0)
    assert scale_base_profile(base, 7.5)[12] == pytest.approx(6_000.0)


def test_fractional_hours_interpolate_between_profile_points():
    base = {11: 10_000.0, 12: 12_000.0}
    assert interpolated_profile_kw(base, 11.5, 15.0) == pytest.approx(11_000.0)
    assert interpolated_profile_kw(base, 11.0, 15.0) == pytest.approx(10_000.0)


def test_interpolation_wraps_around_midnight():
    base = {23: 1_000.0, 0: 0.0}
    assert interpolated_profile_kw(base, 23.5, 15.0) == pytest.approx(500.0)


def test_generation_is_zero_outside_the_profile():
    base = {12: 12_000.0}
    assert calc_generation(3.0, base, 13_000.0, temperature=30.0, coverage_pct=15.0) == 0.0


def test_generation_is_capped_at_installed_capacity():
    base = {12: 99_000.0}
    assert calc_generation(12.0, base, 13_000.0, temperature=25.0, coverage_pct=15.0) \
        <= 13_000.0


def test_hot_weather_reduces_output():
    """Regression test: the model once *increased* output as ambient temperature rose."""
    base = {12: 12_000.0}
    cool = calc_generation(12.0, base, 13_000.0, temperature=25.0, coverage_pct=15.0)
    hot = calc_generation(12.0, base, 13_000.0, temperature=40.0, coverage_pct=15.0)
    assert hot < cool


def test_cloud_reduces_output_monotonically():
    base = {12: 12_000.0}
    outputs = [calc_generation(12.0, base, 13_000.0, cloud_cover=c, temperature=30.0)
               for c in (0, 25, 50, 75, 100)]
    assert all(a > b for a, b in zip(outputs, outputs[1:], strict=False))


def test_output_is_never_negative():
    base = {12: 12_000.0}
    assert calc_generation(12.0, base, 0.0, cloud_cover=0.0, temperature=30.0) == 0.0


# ── cloud-cover sampling ─────────────────────────────────────────────────

def test_cloud_cover_is_deterministic_for_a_given_seed():
    assert get_cloud_cover(12, "summer", seed=5) == get_cloud_cover(12, "summer", seed=5)
    assert get_cloud_cover(12, "summer", seed=5) != get_cloud_cover(12, "summer", seed=6)


def test_monsoon_is_cloudier_than_summer():
    summer = [get_cloud_cover(h, "summer", seed=h) for h in range(24)]
    monsoon = [get_cloud_cover(h, "monsoon", seed=h) for h in range(24)]
    assert sum(monsoon) / 24 > sum(summer) / 24


def test_cloud_cover_stays_in_range():
    for season in ("summer", "monsoon"):
        for hour in range(24):
            value = get_cloud_cover(hour, season, seed=hour)
            assert 0.0 <= value <= 100.0


# ── auxiliary estimates ──────────────────────────────────────────────────

def test_evaporation_reduction_is_bounded_and_monotonic():
    assert evaporation_reduction_pct(0.0) == 0.0
    assert evaporation_reduction_pct(100.0) <= 70.0
    values = [evaporation_reduction_pct(c) for c in (0, 10, 20, 30, 40)]
    assert all(a <= b for a, b in zip(values, values[1:], strict=False))


# ── integration with the configuration ───────────────────────────────────

def test_config_cooling_bonus_is_consistent_with_the_derate_model():
    from src.config.settings import Config

    cfg = Config()
    modelled = floating_cooling_premium(cfg.MONTHLY_TEMP_C[4])
    assert pytest.approx(modelled, rel=0.15) == cfg.COOLING_BONUS_FACTOR, \
        "the analytic cooling premium must agree with the hourly temperature model"
