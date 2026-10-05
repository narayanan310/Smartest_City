"""Guards against fabricated data re-entering the curated snapshots.

These tests encode the *published* values as literals. If anyone reintroduces a
plausible-looking but unsourced number (the failure mode this suite exists to
prevent), these fail immediately.

Sources encoded here:
  - NASA POWER API v2.10.0, 10.99N 76.96E, 2023 monthly (verified against the
    live API by hand)
  - TNERC Suo-motu Order No. 6 of 2023, clause 3.1.7, HT-V public EV charging
  - CEA CO2 Baseline Database v20/v21 weighted-average series
  - Gelera-Adefuin (2019) IJITEE v8(6), 14-point Ukkadam Lake survey
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from src.config.settings import Config
from src.services import hybrid_data
from src.services.hybrid_data import (
    data_provenance_summary,
    hybrid_emission_factor,
    hybrid_solar_calibration,
    hybrid_tariff_table,
)

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

# ── published constants ───────────────────────────────────────────────────
TNERC_TOD = {"solar_hours": 6.00, "peak_hours": 9.00, "normal_hours": 7.50}
CEA_2023_24 = 0.727
CEA_2022_23 = 0.716
NASA_GHI_2023 = [5.4454, 6.2455, 6.3317, 6.3286, 5.4871, 4.6471,
                 4.0133, 5.7439, 4.3901, 4.8178, 4.6121, 4.5178]
NASA_ANNUAL_MEAN = 5.209
LAKE_AREA_KM2 = 1.295
LAKE_DEPTH_M = 5.82

# Values that appeared in an earlier fabricated draft and must never return.
FORBIDDEN = [
    (6.50, "solar tariff (order publishes 6.00)"),
    (9.75, "peak tariff (order publishes 9.00)"),
    (8.10, "off-peak tariff (order publishes 7.50)"),
    (94.25, "COD mean (published survey gives 390.4)"),
    (0.823, "CEA 2022-23 (published series gives 0.716)"),
    (46.42, "BOD mean presented as a single authoritative figure"),
    (24.8, "turbidity mean (published survey gives 11.7)"),
    (8.50, "pH presented as a single authoritative figure"),
]


@pytest.fixture()
def lake() -> dict:
    return json.loads((RAW / "ukkadam_lake_environmental_data.json").read_text())


@pytest.fixture()
def transit() -> dict:
    return json.loads((RAW / "coimbatore_transit_economics_data.json").read_text())


@pytest.fixture()
def nasa() -> dict:
    return json.loads((RAW / "nasa_power_2023_coimbatore.json").read_text())


# ── TNERC Order No. 6 of 2023 ─────────────────────────────────────────────

def test_tod_tariffs_match_published_order():
    assert hybrid_tariff_table() == TNERC_TOD


def test_tod_windows_are_the_published_windows():
    cfg = Config(data_mode="hybrid")
    assert cfg.tod_tariff_per_kwh(9.0) == pytest.approx(6.00)
    assert cfg.tod_tariff_per_kwh(15.99) == pytest.approx(6.00)
    assert cfg.tod_tariff_per_kwh(6.0) == pytest.approx(9.00)
    assert cfg.tod_tariff_per_kwh(21.99) == pytest.approx(9.00)
    assert cfg.tod_tariff_per_kwh(16.0) == pytest.approx(7.50)
    assert cfg.tod_tariff_per_kwh(5.99) == pytest.approx(7.50)


def test_tariff_file_records_the_order_and_verified_status(transit):
    tariffs = transit["electricity_tariffs"]
    assert "Order No. 6 of 2023" in tariffs["order_reference"]
    assert tariffs["provenance"]["verification_status"] == "verified"
    assert tariffs["provenance"]["source"].startswith("TNERC")


def test_peak_window_is_18_to_22_not_18_to_21(transit):
    key = "peak_hours_06_00_to_09_00_and_18_00_to_22_00"
    assert key in transit["electricity_tariffs"]["public_ev_charging_tod_tariffs_inr_per_kwh"]
    assert hybrid_tariff_table()["peak_hours"] == pytest.approx(9.00)


# ── CEA emission factor ───────────────────────────────────────────────────

def test_cea_factors_match_published_series(transit):
    cea = transit["carbon_emissions"]["cea_grid_emission_factors_kg_co2_per_kwh"]
    assert cea["official_2023_24"] == pytest.approx(CEA_2023_24)
    assert cea["baseline_2022_23"] == pytest.approx(CEA_2022_23)
    assert hybrid_emission_factor() == pytest.approx(CEA_2023_24)


def test_cea_provenance_is_verified(transit):
    prov = transit["carbon_emissions"]["provenance"]
    assert prov["verification_status"] == "verified"
    assert "CEA" in prov["source"]


# ── NASA POWER snapshot ───────────────────────────────────────────────────

def test_nasa_snapshot_matches_live_api_values(nasa):
    stored = [nasa["monthly_ghi"][f"2023{m:02d}"] for m in range(1, 13)]
    assert stored == [pytest.approx(v, abs=0.005) for v in NASA_GHI_2023]


def test_nasa_annual_mean_key_is_absent_but_value_known(nasa):
    # NASA publishes an annual mean under the YYYY13 key. It was removed from the
    # snapshot (consumers compute their own mean), but the value is the genuine
    # days-weighted annual mean, not a thirteenth month.
    assert "202313" not in nasa["monthly_ghi"]
    simple_mean = sum(NASA_GHI_2023) / 12
    assert simple_mean == pytest.approx(5.215, abs=0.001)
    assert pytest.approx(5.209, abs=0.001) == NASA_ANNUAL_MEAN


def test_august_value_is_genuine_not_an_anomaly(nasa):
    # August 5.74 really is the NASA POWER figure; it sits between the wettest
    # monsoon months and is not an error to be "corrected".
    assert nasa["monthly_ghi"]["202308"] == pytest.approx(5.74, abs=0.01)
    assert nasa["monthly_ghi"]["202307"] < nasa["monthly_ghi"]["202308"]


def test_hybrid_scale_derived_from_snapshot_not_literal():
    scale, temp = hybrid_solar_calibration("summer")
    # Derived as NASA_GHI / SRRA fallback for the representative month.
    assert scale == pytest.approx(6.3317 / 6.20, rel=1e-3)
    assert temp == pytest.approx(28.43)
    monsoon_scale, monsoon_temp = hybrid_solar_calibration("monsoon")
    assert monsoon_scale == pytest.approx(4.0133 / 3.80, rel=1e-3)
    assert monsoon_temp == pytest.approx(24.47)


# ── Lake survey values ────────────────────────────────────────────────────

def test_lake_hydrology_matches_published_survey(lake):
    hydro = lake["site_metadata"]["hydrology"]
    assert hydro["surface_area_sq_km"] == pytest.approx(LAKE_AREA_KM2)
    assert hydro["mean_depth_m"] == pytest.approx(LAKE_DEPTH_M)


def test_lake_water_quality_matches_published_survey(lake):
    params = lake["water_quality_benchmarks"]
    do = params["dissolved_oxygen_mg_l"]
    assert do["mean"] == pytest.approx(5.71, abs=0.02)
    assert (do["min"], do["max"]) == (5.0, 6.7)
    assert do["cpcb_class_c_minimum"] == pytest.approx(4.0)
    cod = params["chemical_oxygen_demand_cod_mg_l"]
    assert cod["mean"] == pytest.approx(390.43, abs=0.02)
    assert (cod["min"], cod["max"]) == (242, 487)
    assert params["ph"]["mean"] == pytest.approx(9.17, abs=0.02)
    assert params["turbidity_ntu"]["mean"] == pytest.approx(11.69, abs=0.02)


def test_every_lake_parameter_carries_provenance(lake):
    params = lake["water_quality_benchmarks"]
    for key in ("dissolved_oxygen_mg_l", "ph", "biochemical_oxygen_demand_bod_mg_l",
                "chemical_oxygen_demand_cod_mg_l", "turbidity_ntu",
                "total_dissolved_solids_mg_l", "total_alkalinity_mg_l",
                "chloride_mg_l"):
        assert params[key].get("provenance"), f"{key} missing provenance"


def test_unverified_inputs_are_labelled_assumptions(transit):
    ops = transit["transit_operations"]
    assert ops["provenance"]["verification_status"] == "verified_with_assumptions"
    assert ops["provenance"]["assumption"], "assumption group must be populated"
    assert ops["corridor_archetypes_provenance"]["verification_status"] == "assumption"
    sec = transit["electricity_tariffs"]["secondary_assumptions_provenance"]
    assert sec["verification_status"] == "assumption"


def test_diesel_economy_matches_published_tnstc(transit):
    """TNSTC Coimbatore city operation is ~5.10 KMPL, not the earlier 3.5."""
    ref = transit["transit_operations"]["verified_operations_reference"]
    assert ref["city_fuel_economy_kmpl"] == pytest.approx(5.10)
    assert ref["rural_fuel_economy_kmpl"] == pytest.approx(5.57)
    assert ref["fleet_strength_total"] == 2559
    assert ref["provenance"]["verification_status"] == "verified"
    assert transit["transit_operations"]["diesel_counterfactual"][
        "diesel_fuel_economy_km_per_litre"] == pytest.approx(5.10)


# ── no fabricated values anywhere in the snapshots ────────────────────────

def _numeric_leaves(node, path=""):
    """Yield every numeric leaf value with its path, skipping prose."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _numeric_leaves(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _numeric_leaves(value, f"{path}[{index}]")
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield path, float(node)


@pytest.mark.parametrize("value,reason", FORBIDDEN)
def test_no_forbidden_fabricated_values(lake, transit, nasa, value, reason):
    """A fabricated figure must never appear as a DATA value.

    Prose in a ``*_note`` field legitimately mentions the corrected figure, so
    only numeric leaves are inspected.
    """
    for name, blob in (("lake", lake), ("transit", transit), ("nasa", nasa)):
        for path, leaf in _numeric_leaves(blob):
            assert leaf != value, (
                f"fabricated value {value} reappeared at {name}{path} ({reason})")


# ── provenance transparency layer ─────────────────────────────────────────

def test_provenance_summary_classifies_inputs():
    rows = data_provenance_summary()
    kinds = {r["kind"] for r in rows}
    assert kinds <= {"authentic", "generated", "assumption", "not available"}
    assert "authentic" in kinds and "generated" in kinds
    by_input = {r["input"]: r for r in rows}
    assert by_input["Cloud cover realisation"]["kind"] == "generated"
    assert by_input["EV charging time-of-day tariff"]["kind"] == "authentic"
    assert by_input["Grid emission factor"]["kind"] == "authentic"
    assert by_input["Diesel counterfactual (220 km/day, 3.5 km/L, price)"]["kind"] == "assumption"


def test_simulated_mode_uses_no_file_backed_input():
    """The FABRICATED mode must be fully self-contained."""
    cfg = Config(data_mode="simulated")
    assert cfg.grid_emission_factor() == pytest.approx(0.82)
    assert cfg.tod_tariff_per_kwh(12.0) == pytest.approx(cfg.GRID_TARIFF_PER_KWH)
    hybrid_data._load_snapshot.cache_clear()
    base_profile, temp = hybrid_data.effective_generation_inputs(cfg, "summer", 32.5)
    assert base_profile is cfg.BASE_GENERATION_KW
    assert temp == pytest.approx(32.5)


def test_hybrid_mode_reads_files():
    cfg = Config(data_mode="hybrid")
    assert cfg.grid_emission_factor() == pytest.approx(0.727)
    assert cfg.tod_tariff_per_kwh(12.0) == pytest.approx(6.00)


# ── expanded-network geometry (OSRM-derived) ─────────────────────────────

def test_expanded_routes_trace_real_roads_not_straight_lines():
    """The two added corridors must bend like roads, not chords.

    Route 45 previously ran within 2.1 % of its own start-to-end chord, i.e. it
    was effectively a straight line. Both routes now follow OSRM road geometry.
    Both deviation and chord are computed in kilometres so the ratio is scale-free.
    """
    from src.config.settings import Config

    cfg = Config(network_mode="expanded")
    for route_id in ("Route 45", "Route 70"):
        wp = cfg.ROUTES[route_id]["waypoints"]
        assert len(wp) >= 30, f"{route_id} has too few waypoints to follow roads"
        # project to a local km frame (1 deg lat ~ 111 km, 1 deg lon ~ 111*cos(lat))
        pts = [(lon * 111.0 * 0.98, lat * 111.0) for lat, lon in wp]
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        dx, dy = x1 - x0, y1 - y0
        chord = math.hypot(dx, dy)
        assert chord > 0
        deviation_km = max(abs(dy * (p[0] - x0) - dx * (p[1] - y0)) / chord for p in pts)
        assert deviation_km > 0.5, (
            f"{route_id} deviates only {deviation_km:.2f} km from its chord — "
            "it is not following roads")


def test_expanded_route_stop_anchors_are_in_range():
    from src.config.settings import Config

    cfg = Config(network_mode="expanded")
    for route_id, route in cfg.ROUTES.items():
        n = len(route["waypoints"])
        assert len(route["stop_waypoint_indices"]) == len(route["stop_names"])
        assert all(0 <= i < n for i in route["stop_waypoint_indices"]), route_id


def test_core_routes_retain_osrm_density():
    """The original four corridors must keep their high-fidelity waypoint counts."""
    from src.config.settings import Config

    cfg = Config(network_mode="standard")
    for route_id in ("Route 69", "Route 1", "Route 2", "Route 3"):
        assert len(cfg.ROUTES[route_id]["waypoints"]) >= 50, route_id
