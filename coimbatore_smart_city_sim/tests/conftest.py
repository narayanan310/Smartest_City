"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.settings import Config
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine


@pytest.fixture()
def config() -> Config:
    """A fresh configuration instance."""
    return Config()


@pytest.fixture()
def engine(config: Config) -> SimulationEngine:
    """An engine reset to 00:00, with no steps taken."""
    return SimulationEngine(config, coverage_pct=15.0, season="summer", seed=42)


@pytest.fixture(scope="module")
def day_run():
    """One complete reference day: the states, and the engine that produced them.

    The same instance must be returned for both, because several tests assert that
    reported aggregates match the fleet state left behind by the run.
    """
    engine = SimulationEngine(Config(), coverage_pct=15.0, season="summer", seed=42)
    states = engine.run(STEPS_PER_DAY)
    return states, engine


def make_bus(config: Config, bus_id: int = 1, route: str = "Route 69", soc: float = 20.0,
             progress: float = 0.0, state: str = "running"):
    """Construct a Bus bound to a real corridor from the configuration."""
    from src.models.bus import Bus

    return Bus(
        bus_id=bus_id,
        route_id=route,
        waypoints=config.ROUTES[route]["waypoints"],
        stop_names=config.ROUTES[route]["stop_names"],
        stop_waypoint_indices=config.ROUTES[route]["stop_waypoint_indices"],
        soc=soc,
        progress=progress,
        state=state,
    )


def make_station_state(config: Config, ready: int) -> dict:
    """A single-hub station state dictionary."""
    return {
        config.SWAP_STATIONS[0]["name"]: {
            "ready_packs": ready,
            "charging_packs": [],
            "charging_queue": [],
        }
    }


@pytest.fixture(scope="session", autouse=True)
def processed_datasets():
    """Guarantee the pipeline outputs exist before any test reads them.

    ``data/processed/`` is gitignored by design (generated artefacts), so a
    bare clone has no CSV/JSON feeds. This fixture regenerates them from the
    committed ``data/raw/`` snapshots exactly once per session, which keeps
    the suite green without vendoring generated files. Yields the processed
    directory path.
    """
    processed_dir = Path(__file__).resolve().parent.parent / "data" / "processed"
    expected = (
        "clean_simulation_benchmarks.json",
        "diurnal_solar_profiles.json",
        "diurnal_solar_profiles.csv",
        "transit_dispatch_curves.json",
        "transit_dispatch_curves.csv",
    )
    if not all((processed_dir / name).is_file() for name in expected):
        sys.path.insert(0, str(processed_dir.parent.parent / "scripts"))
        from ingest_and_vet_data import main as run_pipeline

        assert run_pipeline() == 0
        assert all((processed_dir / name).is_file() for name in expected)
    return processed_dir
