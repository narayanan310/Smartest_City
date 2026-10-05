"""Cross-model accounting tests for hybrid mode and mode switching.

Pins the properties the audit demanded:
- the annual model applies the same hybrid calibration as the daily step,
- revenue uses one rate everywhere within a mode,
- mode switches are idempotent and validated,
- the expanded map document carries the new controls and geometry.
"""

from __future__ import annotations

import pytest

from src.config.settings import Config
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine
from src.visualization.map import build_frame_trajectory, build_interactive_map_html


def _run(data_mode: str, network_mode: str = "standard", **kwargs):
    cfg = Config(data_mode=data_mode, network_mode=network_mode)
    engine = SimulationEngine(cfg, coverage_pct=15.0, season="summer", seed=42,
                              **kwargs)
    engine.run(STEPS_PER_DAY)
    return engine


def test_tod_tariff_window_boundaries():
    cfg = Config(data_mode="hybrid")
    assert cfg.tod_tariff_per_kwh(6.0) == pytest.approx(9.75)
    assert cfg.tod_tariff_per_kwh(9.0) == pytest.approx(6.50)
    assert cfg.tod_tariff_per_kwh(15.999) == pytest.approx(6.50)
    assert cfg.tod_tariff_per_kwh(16.0) == pytest.approx(8.10)
    assert cfg.tod_tariff_per_kwh(18.0) == pytest.approx(9.75)
    assert cfg.tod_tariff_per_kwh(21.999) == pytest.approx(9.75)
    assert cfg.tod_tariff_per_kwh(22.0) == pytest.approx(8.10)
    assert cfg.tod_tariff_per_kwh(0.0) == pytest.approx(8.10)
    assert cfg.tod_tariff_per_kwh(23.5) == pytest.approx(8.10)
    assert cfg.tod_tariff_per_kwh(25.0) == pytest.approx(8.10)  # wraps to 01:00


def test_hybrid_annual_generation_reflects_calibration():
    sim = SimulationEngine(Config(), coverage_pct=15.0,
                           season="summer", seed=42).run_annual_simulation()
    hyb_cfg = Config(data_mode="hybrid")
    hyb = SimulationEngine(hyb_cfg, coverage_pct=15.0,
                           season="summer", seed=42).run_annual_simulation()
    assert hyb["annual_generation_gwh"] > sim["annual_generation_gwh"]
    # Same uplift the daily step sees (cloud draws differ, so compare ratios
    # loosely rather than exactly).
    sim_day = _run("simulated").daily_summary()["generation_kwh"]
    hyb_day = _run("hybrid").daily_summary()["generation_kwh"]
    daily_ratio = hyb_day / sim_day
    annual_ratio = (hyb["annual_generation_gwh"] / sim["annual_generation_gwh"]
                    if sim["annual_generation_gwh"] > 0 else 1.0)
    assert abs(daily_ratio - annual_ratio) < 0.05


def test_annual_savings_equals_revenue_accounting():
    for mode in ("simulated", "hybrid"):
        cfg = Config(data_mode=mode)
        annual = SimulationEngine(cfg, coverage_pct=15.0, season="summer",
                                  seed=42).run_annual_simulation()
        direct_kwh = annual["annual_direct_use_gwh"] * 1e6
        blended = annual["annual_blended_retail_inr_per_kwh"]
        # The engine reports savings rounded to 0.01 Cr, so recompute through
        # the same rounding rather than demanding unrounded equality.
        assert annual["annual_savings_inr_cr"] == pytest.approx(
            round(direct_kwh * blended / 1e7, 2), abs=0.02)
        if mode == "simulated":
            assert blended == pytest.approx(8.0 + 0.54)
        else:
            assert 6.50 + 0.54 <= blended <= 9.75 + 0.54


def test_daily_cost_matches_step_revenue():
    cfg = Config(data_mode="hybrid")
    engine = SimulationEngine(cfg, coverage_pct=15.0, season="summer", seed=42)
    engine.run(STEPS_PER_DAY)
    frame = engine.to_dataframe()
    expected = float((frame["Solar Direct Use (kW)"] * 0.5
                      * (frame["Hour"].map(cfg.tod_tariff_per_kwh)
                         + cfg.OA_SURCHARGE_PER_KWH)).sum())
    # Both sides accumulate per-step rounded values (cost to paise, frame kW
    # to 1 dp), so allow a few rupees of rounding noise on a ~Rs 4 lakh total.
    assert engine.daily_summary()["cost_inr"] == pytest.approx(expected, abs=5.0)


def test_mode_switchers_are_idempotent():
    cfg = Config()
    for _ in range(3):
        cfg.set_network_mode("expanded")
    assert len(cfg.ROUTES) == 6
    assert len(cfg.SWAP_STATIONS) == 6
    assert len({s["id"] for s in cfg.SWAP_STATIONS}) == 6
    for _ in range(3):
        cfg.set_network_mode("standard")
    assert len(cfg.ROUTES) == 4
    assert len(cfg.SWAP_STATIONS) == 4
    cfg.set_data_mode("hybrid")
    cfg.set_data_mode("hybrid")
    assert cfg.DATA_MODE == "hybrid"
    with pytest.raises(ValueError, match="network_mode must be"):
        cfg.set_network_mode("mega")
    with pytest.raises(ValueError, match="data_mode must be"):
        cfg.set_data_mode("mega")


def test_lowercase_alias_wins_documented():
    cfg = Config(DATA_MODE="simulated", data_mode="hybrid")
    assert cfg.DATA_MODE == "hybrid"


def test_expanded_map_document_controls():
    cfg = Config(network_mode="expanded")
    engine = SimulationEngine(cfg, seed=42)
    frames = build_frame_trajectory(engine, cfg)
    html = build_interactive_map_html(cfg, frames, initial_step=0)
    assert "fitNetwork" in html
    assert "legend-route-row" in html
    assert "Show all routes" in html
    for token in ("Route 45", "Route 70", "Route 69", "Route 1",
                  "Saravanampatti IT Hub Station", "Vadavalli - RS Puram West Hub"):
        assert token in html


def test_expanded_stations_have_unique_ids_and_capacity():
    cfg = Config(network_mode="expanded")
    ids = [s["id"] for s in cfg.SWAP_STATIONS]
    assert sorted(ids) == [1, 2, 3, 4, 5, 6]
    assert sum(s["capacity_buses"] for s in cfg.SWAP_STATIONS) == 205
