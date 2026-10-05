"""Performance guard.

These are not micro-benchmarks. Each budget is roughly an order of magnitude above the
measured cost, so they pass on a slow or heavily loaded machine and fail only on a
*catastrophic* regression.

That distinction matters because this project has already had one such regression: route
geometry was re-walked on every ``lat``/``lon`` access, which cost about two million
redundant haversine evaluations per simulated day and dominated the runtime. A tight
budget would flake; a loose one still catches that class of bug.
"""

from __future__ import annotations

import time

import pytest

from src.config.settings import Config
from src.models.ecology import coverage_status_summary
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine
from src.visualization.map import build_frame_trajectory, build_interactive_map_html


def _fastest(fn, repeats: int = 3) -> float:
    """Best of ``repeats`` wall-clock seconds, to suppress scheduler noise."""
    return min(_timed(fn) for _ in range(repeats))


def _timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start


@pytest.mark.performance
def test_one_simulated_day_is_not_pathologically_slow():
    """A full 48-step day for 260 buses. Measured ~0.04 s; budget 2 s."""
    cfg = Config()
    engine = SimulationEngine(cfg)
    elapsed = _fastest(lambda: engine.run(STEPS_PER_DAY))
    assert len(engine.get_fleet()) == cfg.NUM_BUSES
    assert elapsed < 2.0, f"simulated day took {elapsed:.2f} s, budget 2.0 s"


@pytest.mark.performance
def test_annual_simulation_is_not_pathologically_slow():
    """The 365-day climatological roll-up. Measured ~0.07 s; budget 4 s."""
    cfg = Config()
    elapsed = _fastest(lambda: SimulationEngine(cfg).run_annual_simulation())
    assert elapsed < 4.0, f"annual simulation took {elapsed:.2f} s, budget 4.0 s"


@pytest.mark.performance
def test_map_trajectory_and_html_stay_within_budget():
    """Trajectory plus payload render. Measured ~0.12 s; budget 3 s.

    The trajectory build is the expensive half and the one that regressed when route
    geometry was recomputed per access, so it carries most of this budget.
    """
    cfg = Config()
    engine = SimulationEngine(cfg)
    frames = build_frame_trajectory(engine, cfg)
    assert len(frames) == STEPS_PER_DAY

    elapsed = _fastest(
        lambda: build_interactive_map_html(
            cfg, frames, 0, cfg.NUM_BUSES,
            cfg.max_generation_kw(cfg.DEFAULT_COVERAGE_PCT),
        ),
        repeats=2,
    )
    assert elapsed < 3.0, f"map payload render took {elapsed:.2f} s, budget 3.0 s"


@pytest.mark.performance
def test_route_geometry_is_cached_not_recomputed():
    """Regression guard for the two-million-redundant-haversines bug.

    ``Bus.lat``/``Bus.lon`` are called twice per bus per step by the trajectory builder.
    If the geometry cache were removed, this loop would take seconds instead of
    milliseconds. Asserted as a ratio against a single uncached evaluation so the test
    is about caching, not about absolute machine speed.
    """
    from src.algorithms.scheduler import create_fleet

    cfg = Config()
    bus = create_fleet(1, cfg.ROUTES, seed=1)[0]
    bus._ensure_geometry()

    def one_pass() -> None:
        for _ in range(200):
            _ = bus.lat, bus.lon

    cached = _fastest(one_pass, repeats=2)

    # Drop the cache and force a rebuild on the first access.
    bus._segment_lengths = []
    bus._cumulative_lengths = []
    bus._route_length_cache = 0.0
    bus._geometry_waypoint_count = -1

    uncached = _timed(one_pass)

    assert cached < uncached, (
        "repeated position reads must reuse the cached geometry; "
        f"cached {cached * 1000:.2f} ms vs uncached {uncached * 1000:.2f} ms"
    )


@pytest.mark.performance
def test_ecology_summary_is_cheap_enough_to_sweep():
    """Eight coverage scenarios, as the sensitivity tables require. Budget 2 s."""
    cfg = Config()
    params = cfg.ecology_params()

    def sweep() -> None:
        for coverage in range(8):
            coverage_status_summary(
                float(coverage), params, initial_do=5.5, seed=42, ambient_temp_c=32.5,
            )

    elapsed = _fastest(sweep, repeats=2)
    assert elapsed < 2.0, f"ecology sweep took {elapsed:.2f} s, budget 2.0 s"
