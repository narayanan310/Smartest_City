"""Tests for the lake ecological model."""

from __future__ import annotations

import dataclasses
import random

import pytest

from src.config.settings import Config
from src.models.ecology import (
    DEFAULT_SPIN_UP_DAYS,
    EcologyParams,
    blocked_production_fraction,
    check_ecology,
    coverage_status_summary,
    do_saturation_mg_l,
    photosynthetically_active_fraction,
    simulate_do_24h,
    update_ecology,
    water_temperature_c,
)

# ── dissolved-oxygen saturation ──────────────────────────────────────────

@pytest.mark.parametrize(
    "temp_c, expected",
    [(15.0, 10.07), (20.0, 9.08), (25.0, 8.26), (30.0, 7.44), (35.0, 6.63)],
)
def test_saturation_falls_with_temperature(temp_c, expected):
    """Published freshwater saturation values, APHA standard-methods form."""
    assert do_saturation_mg_l(temp_c) == pytest.approx(expected, abs=0.12)


def test_saturation_is_monotonic_decreasing():
    values = [do_saturation_mg_l(t / 2) for t in range(0, 80)]
    assert all(a > b for a, b in zip(values, values[1:], strict=False))


# ── model structure ──────────────────────────────────────────────────────

def test_light_is_zero_at_night_and_peaks_at_noon():
    assert photosynthetically_active_fraction(0.0) == 0.0
    assert photosynthetically_active_fraction(23.0) == 0.0
    assert photosynthetically_active_fraction(12.0) == pytest.approx(1.0)
    assert photosynthetically_active_fraction(6.0) == pytest.approx(0.0, abs=1e-9)


def test_shading_only_applies_inside_the_insolation_window():
    params = EcologyParams()
    assert blocked_production_fraction(9.0, 15.0, params) == 0.0
    assert blocked_production_fraction(16.0, 15.0, params) == 0.0
    assert blocked_production_fraction(12.0, 15.0, params) > 0.0


def test_blocked_production_is_capped_at_one():
    params = EcologyParams(shade_effectiveness=50.0)
    assert blocked_production_fraction(12.0, 100.0, params) == 1.0


def test_water_cools_beneath_the_array():
    params = EcologyParams()
    clear = water_temperature_c(13.0, 0.0, 32.5, params)
    shaded = water_temperature_c(13.0, 15.0, 32.5, params)
    assert shaded < clear


def test_water_is_warmest_late_afternoon_and_coolest_before_dawn():
    params = EcologyParams()
    temps = {h: water_temperature_c(float(h), 0.0, 32.5, params) for h in range(24)}
    assert max(temps, key=temps.get) == 15, "peak at the configured afternoon hour"
    assert min(temps, key=temps.get) == 3, "trough half a day later, before sunrise"


# ── mass balance ─────────────────────────────────────────────────────────

def test_uncovered_lake_converges_to_a_stationary_cycle():
    """The re-aeration term must make the budget self-correcting, not drift."""
    params = EcologyParams()
    rng = random.Random(11)
    do = 5.5

    minima = []
    for _day in range(16):
        low = high = do
        for step in range(48):
            do, _, _, _, _, _ = update_ecology(do, (step % 48) * 0.5, 0.0, params,
                                                False, 0.5, rng, 30.0)
            low, high = min(low, do), max(high, do)
        minima.append((low, high))

    early = minima[DEFAULT_SPIN_UP_DAYS - 1][0]
    late = minima[-1][0]
    assert abs(late - early) < 0.15, "drift between the spin-up day and the last day"
    assert late > 4.0, "an uncovered eutrophic lake should not become anoxic"


def test_oxygen_never_reaches_the_ceiling_when_uncovered():
    params = EcologyParams()
    rng = random.Random(5)
    do = 5.5
    for _day in range(14):
        for step in range(48):
            do, _, _, _, _, _ = update_ecology(do, (step % 48) * 0.5, 0.0, params,
                                                False, 0.5, rng, 30.0)
    assert do < params.do_max_mg_l


def test_zero_coverage_consumes_no_oxygen_at_the_floor():
    params = EcologyParams()
    do, _, _, _, _, _ = update_ecology(2.0, 12.0, 0.0, params, False, 0.5,
                                       random.Random(1), 25.0)
    assert do >= params.do_min_floor_mg_l


# ── coverage response ────────────────────────────────────────────────────

def test_minimum_oxygen_decreases_monotonically_with_coverage():
    params = EcologyParams()
    minima = [min(r["do_level"] for r in simulate_do_24h(5.5, cov, params, seed=42))
              for cov in (0.0, 10.0, 15.0, 20.0, 25.0, 30.0)]
    assert all(a > b for a, b in zip(minima, minima[1:], strict=False)), minima


def test_fifteen_percent_scenario_stays_above_the_threshold():
    params = EcologyParams()
    summary = coverage_status_summary(15.0, params, seed=42)
    assert summary["min_do"] >= 4.0
    assert summary["ecological_status"] == "OK"
    assert summary["aeration_hours"] == 0


def test_twenty_percent_scenario_breaches_the_threshold():
    params = EcologyParams()
    assert coverage_status_summary(20.0, params, seed=42)["min_do"] < 4.0


def test_aeration_becomes_reachable_in_the_stress_range():
    params = EcologyParams()
    summaries = [coverage_status_summary(cov, params, seed=42)
                 for cov in (30.0, 40.0, 45.0, 50.0)]
    assert any(s["aeration_hours"] > 0 for s in summaries), \
        "the aeration mitigation path must be demonstrable"


def test_aeration_raises_oxygen_and_has_hysteresis():
    params = EcologyParams()
    rng = random.Random(2)
    # With aeration forced on, oxygen must rise faster than with it off.
    off = update_ecology(2.6, 3.0, 45.0, params, False, 0.5, rng, 32.5)
    on = update_ecology(2.6, 3.0, 45.0, params, True, 0.5, rng, 32.5)
    assert on[0] > off[0]
    # Release threshold is above the engage threshold, so the device does not chatter.
    assert params.aeration_off_above_mg_l > params.aeration_on_below_mg_l


def test_response_is_proportional_to_the_calibrated_coefficient():
    base = EcologyParams(shade_effectiveness=2.0)
    double = dataclasses.replace(base, shade_effectiveness=4.0)
    a = coverage_status_summary(15.0, base, seed=42)["min_do"]
    b = coverage_status_summary(15.0, double, seed=42)["min_do"]
    assert b < a, "doubling the coefficient must lower the oxygen minimum"
    assert base.shade_effectiveness * 2 == double.shade_effectiveness


def test_oxygen_depends_on_season_through_temperature():
    """A physically coupled model must show a seasonal signal."""
    params = EcologyParams()
    cool = coverage_status_summary(15.0, params, seed=42, ambient_temp_c=25.0)["min_do"]
    hot = coverage_status_summary(15.0, params, seed=42, ambient_temp_c=33.0)["min_do"]
    assert hot < cool, "warmer water holds less oxygen"


# ── spin-up ──────────────────────────────────────────────────────────────

def test_spin_up_changes_the_answer_and_is_required_for_coverage_sensitivity():
    """Without spin-up the minimum precedes any shading, so coverage cannot matter."""
    params = EcologyParams()
    naive = [min(r["do_level"] for r in simulate_do_24h(5.5, cov, params, seed=42,
                                                       spin_up_days=0))
             for cov in (10.0, 20.0)]
    spun = [min(r["do_level"] for r in simulate_do_24h(5.5, cov, params, seed=42,
                                                      spin_up_days=DEFAULT_SPIN_UP_DAYS))
            for cov in (10.0, 20.0)]
    assert spun[0] - spun[1] > 0.4, "steady state must separate the scenarios"
    assert naive[0] == pytest.approx(naive[1], abs=0.05), \
        "this documents the defect spin-up exists to correct"


def test_spin_up_rejects_invalid_arguments():
    params = EcologyParams()
    with pytest.raises(ValueError):
        simulate_do_24h(5.5, 15.0, params, spin_up_days=-1)
    with pytest.raises(ValueError):
        simulate_do_24h(5.5, 15.0, params, step_hours=0.0)


def test_scenario_and_live_resolutions_agree():
    """Both views must use the same time step or the dashboard contradicts itself."""
    params = EcologyParams()
    series = simulate_do_24h(5.5, 15.0, params, seed=42)
    assert len(series) == 48, "scenario comparison must integrate at 30-minute resolution"
    assert series[0]["time_str"] == "00:00"
    assert series[-1]["time_str"] == "23:30"


# ── status reporting ─────────────────────────────────────────────────────

def test_status_bands():
    assert check_ecology(5.0, 15.0)[0] == "OK"
    assert check_ecology(4.0, 15.0)[0] == "OK"
    assert check_ecology(3.9, 15.0)[0] == "WARNING"
    assert check_ecology(2.9, 15.0)[0] == "EMERGENCY"
    assert check_ecology(5.0, 20.0)[0] == "CRITICAL"


def test_over_cap_message_still_reports_the_oxygen_reading():
    status, message = check_ecology(4.25, 20.0)
    assert status == "CRITICAL"
    assert "4.25" in message, "the measured value must never be hidden by the policy flag"


def test_summary_reports_net_change_and_threshold_flag():
    params = EcologyParams()
    summary = coverage_status_summary(15.0, params, seed=42)
    for key in ("min_do", "max_do", "final_do", "net_do_change", "aeration_hours",
                "ecological_status", "within_threshold"):
        assert key in summary
    assert summary["within_threshold"] is True


def test_config_builds_a_matching_parameter_set():
    cfg = Config()
    params = cfg.ecology_params()
    assert params.shade_effectiveness == cfg.DO_SHADE_EFFECTIVENESS
    assert params.aeration_on_below_mg_l == cfg.DO_AERATION_TRIGGER
    assert params.aeration_off_above_mg_l == cfg.AERATION_RELEASE_MG_L
    assert params.shading_hours == tuple(cfg.DO_SHADING_HOURS)
