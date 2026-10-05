"""Tests for scripts/ingest_and_vet_data.py — vetting rigour and generator shape.

The vetting suite must accept the committed snapshots and reject targeted
mutations of every bound it claims to enforce. The generators must emit
well-formed, internally consistent series whose JSON and CSV renderings agree.
"""

from __future__ import annotations

import copy
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ingest_and_vet_data import (
    DataVettingError,
    VettingReport,
    generate_diurnal_solar_profiles,
    generate_transit_dispatch_curves,
    vet_lake_environmental_data,
    vet_nasa_power_data,
    vet_transit_economics_data,
)

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def _load(name: str) -> dict:
    with open(RAW_DIR / name, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture()
def nasa() -> dict:
    return _load("nasa_power_2023_coimbatore.json")


@pytest.fixture()
def lake() -> dict:
    return _load("ukkadam_lake_environmental_data.json")


@pytest.fixture()
def transit() -> dict:
    return _load("coimbatore_transit_economics_data.json")


def _rejects(vet_fn, data: dict) -> None:
    with pytest.raises(DataVettingError):
        vet_fn(copy.deepcopy(data), VettingReport())


def test_vetting_accepts_committed_snapshots(nasa, lake, transit):
    report = VettingReport()
    vet_nasa_power_data(nasa, report)
    vet_lake_environmental_data(lake, report)
    vet_transit_economics_data(transit, report)
    assert len(report.checks) == 92
    assert all(ok for _, ok, _ in report.checks)


def test_nasa_snapshot_has_exactly_twelve_months(nasa):
    assert len(nasa["monthly_ghi"]) == 12
    assert len(nasa["clear_sky_ghi"]) == 12
    assert "202313" not in nasa["monthly_ghi"]
    assert "202313" not in nasa["clear_sky_ghi"]


def test_nasa_vetting_rejects_bad_inputs(nasa):
    bad = copy.deepcopy(nasa)
    bad["latitude"] = 45.0
    _rejects(vet_nasa_power_data, bad)
    bad = copy.deepcopy(nasa)
    bad["monthly_ghi"]["202304"] = 0.5
    _rejects(vet_nasa_power_data, bad)
    bad = copy.deepcopy(nasa)
    bad["clear_sky_ghi"]["202304"] = 1.0
    _rejects(vet_nasa_power_data, bad)
    bad = copy.deepcopy(nasa)
    bad["monthly_temp_c"]["202304"] = 60.0
    _rejects(vet_nasa_power_data, bad)
    bad = copy.deepcopy(nasa)
    bad["monthly_humidity_pct"]["202304"] = 5.0
    _rejects(vet_nasa_power_data, bad)
    bad = copy.deepcopy(nasa)
    bad["monthly_wind_speed_ms"]["202304"] = 40.0
    _rejects(vet_nasa_power_data, bad)


def test_lake_vetting_rejects_bad_inputs(lake):
    bad = copy.deepcopy(lake)
    bad["site_metadata"]["hydrology"]["surface_area_sq_km"] = 9.9
    _rejects(vet_lake_environmental_data, bad)
    bad = copy.deepcopy(lake)
    bad["water_quality_benchmarks"]["dissolved_oxygen_mg_l"][
        "cpcb_class_c_minimum"] = 2.0
    _rejects(vet_lake_environmental_data, bad)
    bad = copy.deepcopy(lake)
    bad["water_quality_benchmarks"][
        "biochemical_oxygen_demand_bod_mg_l"]["mean"] = 1.0
    _rejects(vet_lake_environmental_data, bad)


def test_transit_vetting_rejects_bad_inputs(transit):
    bad = copy.deepcopy(transit)
    bad["transit_operations"]["corridor_archetypes"] = bad["transit_operations"]["corridor_archetypes"][:2]
    _rejects(vet_transit_economics_data, bad)
    bad = copy.deepcopy(transit)
    bad["transit_operations"]["diesel_counterfactual"][
        "diesel_fuel_economy_km_per_litre"] = 12.0
    _rejects(vet_transit_economics_data, bad)
    bad = copy.deepcopy(transit)
    bad["electricity_tariffs"]["public_ev_charging_tod_tariffs_inr_per_kwh"][
        "solar_hours_09_00_to_16_00"] = 3.00
    _rejects(vet_transit_economics_data, bad)
    bad = copy.deepcopy(transit)
    bad["carbon_emissions"]["cea_grid_emission_factors_kg_co2_per_kwh"][
        "official_2023_24"] = 0.10
    _rejects(vet_transit_economics_data, bad)


def test_solar_profile_shape(nasa):
    profiles = generate_diurnal_solar_profiles(nasa)
    assert len(profiles) == 3 * 48
    by_season = {}
    for row in profiles:
        by_season.setdefault(row["season"], []).append(row)
    assert set(by_season) == {"summer", "monsoon", "winter"}
    for rows in by_season.values():
        hours = [r["hour"] for r in rows]
        assert hours == [i * 0.5 for i in range(48)]
        night = [r for r in rows if r["hour"] < 5.5 or r["hour"] > 18.5]
        assert all(r["global_horizontal_irradiance_w_m2"] == 0.0 for r in night)
        day = [r for r in rows if 8.0 <= r["hour"] <= 16.0]
        assert all(r["global_horizontal_irradiance_w_m2"] > 0.0 for r in day)
        assert all(0.0 <= r["diffuse_fraction"] <= 1.0 for r in rows)
        assert all(r["clear_sky_irradiance_w_m2"] >= r["global_horizontal_irradiance_w_m2"]
                   for r in rows)


def test_dispatch_curve_shape(transit):
    curves = generate_transit_dispatch_curves(transit)
    corridors = transit["transit_operations"]["corridor_archetypes"]
    assert len(curves) == 48 * len(corridors)
    night = [c for c in curves if c["hour"] < 6.0 or c["hour"] > 22.0]
    assert all(c["active_fleet_ratio"] == 0.0 for c in night)
    assert all(c["target_headway_minutes"] == 0.0 for c in night)
    day = [c for c in curves if 6.0 <= c["hour"] <= 22.0]
    assert all(0.0 < c["active_fleet_ratio"] <= 1.0 for c in day)
    assert all(c["congestion_multiplier"] >= 1.0 for c in day)
    morning = [c for c in day if 8.0 <= c["hour"] <= 10.0]
    midday = [c for c in day if 11.0 <= c["hour"] <= 14.0]
    avg = lambda rows: sum(c["active_fleet_ratio"] for c in rows) / len(rows)
    assert avg(morning) > avg(midday)


def test_processed_csv_json_agreement(processed_datasets):
    for stem in ("diurnal_solar_profiles", "transit_dispatch_curves"):
        with open(processed_datasets / f"{stem}.json", encoding="utf-8") as handle:
            records = json.load(handle)
        with open(processed_datasets / f"{stem}.csv", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == len(records)
        assert rows[0]["hour"] == str(records[0]["hour"])


def test_benchmark_summary_is_computed(processed_datasets):
    with open(processed_datasets / "clean_simulation_benchmarks.json",
              encoding="utf-8") as handle:
        summary = json.load(handle)
    assert summary["pipeline_version"] == "2.1.0"
    stamped = datetime.fromisoformat(summary["processed_timestamp_utc"])
    assert stamped.tzinfo is not None
    assert (datetime.now(UTC) - stamped).days < 3650
    nasa = _load("nasa_power_2023_coimbatore.json")
    expected_ghi = round(sum(nasa["monthly_ghi"][f"2023{m:02d}"]
                             for m in range(1, 13)) / 12, 2)
    assert summary["metrics_summary"]["annual_average_ghi_kwh_m2_day"] == expected_ghi
