"""Unit tests for src/services/hybrid_data.py — the runtime data bridge.

Covers: derivation of calibration numbers from the curated files, graceful
fallback when files are missing/corrupt/absurd, the shared generation-input
helper, and the model-vs-benchmark consistency report.
"""

from __future__ import annotations

import json

import pytest

from src.config.settings import Config
from src.services import hybrid_data
from src.services.hybrid_data import (
    effective_generation_inputs,
    hybrid_emission_factor,
    hybrid_solar_calibration,
    hybrid_tariff_table,
    model_data_consistency,
    raw_snapshots_available,
)


@pytest.fixture(autouse=True)
def clear_loader_cache():
    hybrid_data._load_snapshot.cache_clear()
    yield
    hybrid_data._load_snapshot.cache_clear()


def test_solar_calibration_matches_hand_computation():
    scale_summer, temp_summer = hybrid_solar_calibration("summer")
    assert scale_summer == pytest.approx(6.33 / 6.20, rel=1e-9)
    assert temp_summer == pytest.approx(28.43)
    scale_monsoon, temp_monsoon = hybrid_solar_calibration("monsoon")
    assert scale_monsoon == pytest.approx(4.01 / 3.80, rel=1e-9)
    assert temp_monsoon == pytest.approx(24.47)


def test_solar_calibration_unknown_season_uses_april():
    scale, temp = hybrid_solar_calibration("bogus")
    assert scale == pytest.approx(6.33 / 6.20, rel=1e-9)
    assert temp == pytest.approx(28.43)


def test_missing_files_fall_back_to_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(hybrid_data, "RAW_DIR", tmp_path)
    scale, temp = hybrid_solar_calibration("summer")
    assert (scale, temp) == (1.0, 32.5)
    assert hybrid_tariff_table() == {"solar_hours": 6.00, "peak_hours": 9.00,
                                     "normal_hours": 7.50}
    assert hybrid_emission_factor() == pytest.approx(0.727)
    assert raw_snapshots_available() == {
        hybrid_data.NASA_SNAPSHOT: False,
        hybrid_data.LAKE_SNAPSHOT: False,
        hybrid_data.TRANSIT_SNAPSHOT: False,
    }


def test_corrupt_files_fall_back_to_baseline(tmp_path, monkeypatch):
    for name in (hybrid_data.NASA_SNAPSHOT, hybrid_data.LAKE_SNAPSHOT,
                 hybrid_data.TRANSIT_SNAPSHOT):
        (tmp_path / name).write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(hybrid_data, "RAW_DIR", tmp_path)
    scale, temp = hybrid_solar_calibration("monsoon")
    assert (scale, temp) == (1.0, 29.0)
    assert hybrid_tariff_table()["peak_hours"] == pytest.approx(9.00)


def test_absurd_values_fall_back_to_baseline(tmp_path, monkeypatch):
    nasa = {"monthly_ghi": {"202304": 25.0},
            "monthly_temp_c": {"202304": 28.43}}
    (tmp_path / hybrid_data.NASA_SNAPSHOT).write_text(json.dumps(nasa))
    transit = {"electricity_tariffs": {
        "public_ev_charging_tod_tariffs_inr_per_kwh": {
            "solar_hours_09_00_to_16_00": 600.0,
            "peak_hours_06_00_to_09_00_and_18_00_to_22_00": 9.00,
            "normal_offpeak_hours_16_00_to_18_00_and_22_00_to_06_00": 7.50}},
        "carbon_emissions": {"cea_grid_emission_factors_kg_co2_per_kwh": {
            "official_2023_24": 99.0}}}
    (tmp_path / hybrid_data.TRANSIT_SNAPSHOT).write_text(json.dumps(transit))
    monkeypatch.setattr(hybrid_data, "RAW_DIR", tmp_path)
    assert hybrid_solar_calibration("summer") == (1.0, 32.5)
    assert hybrid_tariff_table()["solar_hours"] == pytest.approx(6.00)
    assert hybrid_emission_factor() == pytest.approx(0.727)


def test_effective_generation_inputs_simulated_passthrough():
    cfg = Config()
    base, temp = effective_generation_inputs(cfg, "summer", 32.5)
    assert base is cfg.BASE_GENERATION_KW
    assert temp == pytest.approx(32.5)


def test_effective_generation_inputs_hybrid_scales_profile():
    cfg = Config(data_mode="hybrid")
    base, temp = effective_generation_inputs(cfg, "summer", 32.5)
    assert base is not cfg.BASE_GENERATION_KW
    assert base[12] == pytest.approx(cfg.BASE_GENERATION_KW[12] * (6.33 / 6.20),
                                     rel=1e-9)
    assert temp == pytest.approx(28.43)


def test_model_data_consistency_all_ok_with_committed_files():
    rows = model_data_consistency()
    assert rows, "consistency report must not be empty"
    failures = [r for r in rows if not r["ok"]]
    assert not failures, f"model/benchmark mismatches: {failures}"


def test_model_data_consistency_detects_drift():
    rows = model_data_consistency(Config())
    by_name = {r["check"]: r for r in rows}
    assert by_name["regulatory DO threshold (model vs CPCB benchmark)"]["ok"]
    assert by_name["lake mean depth (model vs benchmark)"]["model"] == pytest.approx(5.82)
