#!/usr/bin/env python3
"""Final verification sweep across the whole project."""

import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(sys.argv[1]).resolve()

#: Scripts that must spell out the very tokens they forbid, and so cannot be scanned
#: for those tokens. Mirrors ``tests/test_regressions.py::_ENFORCEMENT_SCRIPTS``.
_ENFORCEMENT_SCRIPTS = frozenset({"sweep_invariants.py", "probe_invariants.py"})


def _scannable_sources():
    """Project ``.py`` files excluding caches and the enforcement scripts."""
    for path in sorted(ROOT.rglob("*.py")):
        if ".venv" in str(path) or "__pycache__" in str(path):
            continue
        if path.name in _ENFORCEMENT_SCRIPTS:
            continue
        yield path


sys.path.insert(0, str(ROOT))
os_ok = True


def section(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def check(label, ok, detail=""):
    global os_ok
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        os_ok = False
    return ok


# ── 1. static hygiene ────────────────────────────────────────────────────
section("1. STATIC HYGIENE")
for path in _scannable_sources():
    if ".venv" in str(path) or "__pycache__" in str(path):
        continue
    try:
        ast.parse(path.read_text())
    except SyntaxError as exc:
        check(f"parses: {path.relative_to(ROOT)}", False, str(exc))
check("all Python files parse", True)

stale = {
    "simulation_engine.py": "old flat filename",
    "map_visualizer.py": "old flat filename",
    "utils/solar_calculator.py": "old flat filename",
    "utils/bus_scheduler.py": "old flat filename",
    "utils/ecological_monitor.py": "old flat filename",
    "data_loader.py": "old flat filename",
    "DO_SHADING_PENALTY_PER_PCT": "removed coefficient",
    "calc_demand(": "removed independent demand curve",
    "build_map(": "removed dead folium fallback",
    "use_container_width": "deprecated Streamlit argument",
    "def generate_bus_routes": "removed contradictory geometry helper",
}
# tests/test_regressions.py deliberately lists these tokens in its own forbidden set.
SELF_REFERENTIAL = "tests/test_regressions.py"
for needle, why in stale.items():
    source_hits = []
    for path in _scannable_sources():
        if ".venv" in str(path):
            continue
        if str(path.relative_to(ROOT)) == SELF_REFERENTIAL:
            continue
        if needle in path.read_text():
            source_hits.append(str(path.relative_to(ROOT)))
    check(f"no leftover '{needle}' in source ({why})", not source_hits, ", ".join(source_hits))

# ── 2. tests ─────────────────────────────────────────────────────────────
section("2. TEST SUITE")
proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT,
                      capture_output=True, text=True)
tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
check("pytest passes", proc.returncode == 0, tail)

# ── 3. validation gate ───────────────────────────────────────────────────
section("3. VALIDATION GATE")
proc = subprocess.run([sys.executable, "scripts/validate_results.py"], cwd=ROOT,
                      capture_output=True, text=True)
passed = proc.stdout.count("[PASS]")
failed = proc.stdout.count("[FAIL]")
check("validate_results.py exits 0", proc.returncode == 0, f"{passed} passed, {failed} failed")
check("every invariant check passes", failed == 0 and passed >= 24, f"{passed} checks")

# ── 4. engine self-check ─────────────────────────────────────────────────
section("4. ENGINE SELF-CHECK")
proc = subprocess.run([sys.executable, "-m", "src.simulation.engine"], cwd=ROOT,
                      capture_output=True, text=True)
check("standalone engine runs clean", proc.returncode == 0 and "Traceback" not in proc.stderr)

# ── 5. behavioural invariants across the full matrix ─────────────────────
section("5. INVARIANT SWEEP ACROSS THE SCENARIO MATRIX")
from src.config.settings import Config
from src.models.ecology import coverage_status_summary
from src.simulation.engine import STEPS_PER_DAY, SimulationEngine

balance = inventory = soc_ok = 0
traction_gap = 0.0
determinism = True
cases = 0
for coverage in (0.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 45.0, 50.0):
    for season in ("summer", "monsoon"):
        for fleet, battery, traffic, cloud, bays in (
            (260, 300.0, 1.0, 1.0, 4), (120, 300.0, 1.0, 1.0, 4),
            (400, 300.0, 1.0, 1.0, 4), (260, 150.0, 1.0, 1.0, 4),
            (260, 450.0, 1.0, 1.0, 4), (260, 300.0, 1.5, 1.0, 4),
            (260, 300.0, 1.0, 1.5, 12), (50, 300.0, 0.8, 0.5, 1),
        ):
            cases += 1
            cfg = Config()
            cfg.NUM_BUSES, cfg.BATTERY_CAPACITY_KWH = fleet, battery
            cfg.TRAFFIC_INTENSITY_FACTOR, cfg.CLOUD_COVER_MULTIPLIER = traffic, cloud
            cfg.CHARGER_BAYS_PER_STATION = bays
            e = SimulationEngine(cfg, coverage_pct=coverage, season=season, seed=42)
            states = e.run(STEPS_PER_DAY)
            if all(abs(s.solar_direct_use_kw + s.solar_curtailed_kw - s.generation_kw) < 0.15
                   and abs(s.solar_direct_use_kw + s.grid_import_kw - s.total_demand_kw) < 0.15
                   for s in states):
                balance += 1
            if all(v["ready_packs"] + len(v["charging_packs"]) + len(v["charging_queue"])
                   == s["capacity_buses"]
                   for s, v in zip(cfg.SWAP_STATIONS, e._stations_state.values(), strict=False)):
                inventory += 1
            if min(b.soc for b in e.get_fleet()) >= 5.0:
                soc_ok += 1
            reported = sum(s.demand_kw for s in states) * 0.5
            drawn = sum(b.energy_consumed_kwh for b in e.get_fleet())
            traction_gap = max(traction_gap, abs(reported - drawn) / max(drawn, 1.0))
            repeat = SimulationEngine(cfg, coverage_pct=coverage, season=season,
                                      seed=42).run(STEPS_PER_DAY)
            if any(a.do_level != b.do_level or a.total_swaps != b.total_swaps
                   for a, b in zip(states, repeat, strict=False)):
                determinism = False

check("energy balance closes in every case", balance == cases, f"{balance}/{cases}")
check("pack inventory conserved in every case", inventory == cases, f"{inventory}/{cases}")
check("no bus stranded in any case", soc_ok == cases, f"{soc_ok}/{cases}")
check("traction demand equals energy drawn", traction_gap < 1e-3, f"max gap {traction_gap:.2e}")
check("deterministic in every case", determinism, f"{cases} scenarios")

# ── 6. ecology behaviour ─────────────────────────────────────────────────
section("6. ECOLOGY BEHAVIOUR")
params = Config().ecology_params()
ladder = [coverage_status_summary(c, params, initial_do=5.5, seed=42,
                                  ambient_temp_c=32.5)["min_do"]
          for c in (0.0, 10.0, 15.0, 20.0, 25.0, 30.0)]
check("oxygen decreases monotonically with coverage",
      all(a > b for a, b in zip(ladder, ladder[1:], strict=False)),
      " > ".join(f"{v:.2f}" for v in ladder))
check("15% stays above the 4.0 threshold", ladder[2] >= 4.0, f"{ladder[2]:.2f}")
check("20% reaches the 4.0 threshold", ladder[3] < 4.0, f"{ladder[3]:.2f}")
check("aeration reachable in the stress range",
      any(coverage_status_summary(c, params, initial_do=5.5, seed=42,
                                  ambient_temp_c=32.5)["aeration_hours"] > 0
          for c in (35.0, 40.0, 45.0, 50.0)))
check("uncovered lake never reaches the ceiling",
      max(s.do_level for s in SimulationEngine(Config(), coverage_pct=0.0).run(STEPS_PER_DAY))
      < Config().DO_MAX)

seeds = [min(s.do_level for s in SimulationEngine(Config(), coverage_pct=15.0,
                                                  season="summer", seed=n).run(STEPS_PER_DAY))
         for n in range(1, 61)]
check("15% never breaches across 60 seeds", min(seeds) >= 4.0, f"min {min(seeds):.2f}")

# ── 7. solar physics ─────────────────────────────────────────────────────
section("7. SOLAR PHYSICS")
from src.algorithms.solar import calc_generation, floating_cooling_premium

base = Config().BASE_GENERATION_KW
cool = calc_generation(12.0, base, 13_000.0, cloud_cover=0.0, temperature=25.0)
hot = calc_generation(12.0, base, 13_000.0, cloud_cover=0.0, temperature=40.0)
check("output derates as ambient temperature rises", hot < cool,
      f"{hot:,.0f} kW at 40C vs {cool:,.0f} kW at 25C")
check("output never exceeds nameplate", cool <= 13_000.0)
check("floating beats ground mount", floating_cooling_premium(32.5) > 1.0,
      f"+{(floating_cooling_premium(32.5) - 1) * 100:.1f}%")

# ── 8. documentation consistency ─────────────────────────────────────────
section("8. DOCUMENTATION CONSISTENCY")
engine = SimulationEngine(Config(), coverage_pct=15.0, season="summer")
engine.run(STEPS_PER_DAY)
summary = engine.daily_summary()
annual = engine.run_annual_simulation()
facts = {
    "68,227": summary["generation_kwh"],
    "48,685": summary["direct_use_kwh"],
    "19,542": summary["curtailed_kwh"],
    "21.36": annual["annual_generation_gwh"],
    "10,881": annual["annual_co2_tonnes"],
    "12.95": Config().capacity_mwp(15.0),
}
docs = [p for p in ROOT.rglob("*.md") if ".venv" not in str(p)]
corpus = "\n".join(p.read_text() for p in docs)
for token, value in facts.items():
    check(f"documented figure {token} is present", token in corpus,
          f"model value {value:,.2f}")

stale_claims = {
    "22.61 GWh": "superseded annual generation",
    "18,540": "superseded carbon figure",
    "74,100": "superseded daily generation",
    "4.38 mg/L": "superseded oxygen minimum",
    "~21 GWh": "superseded target",
}
for token, why in stale_claims.items():
    check(f"no stale claim '{token}' ({why})", token not in corpus)

# ── 9. dependency declaration ────────────────────────────────────────────
section("9. DEPENDENCIES")
stdlib = set(sys.stdlib_module_names)
external = set()
for path in _scannable_sources():
    if ".venv" in str(path):
        continue
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            external.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            external.add(node.module.split(".")[0])
external = {m for m in external if m not in stdlib and m not in {"src", "tests", "conftest"}}
runtime = {"streamlit", "pandas", "plotly", "requests"}
def declared_in(name):
    text = (ROOT / name).read_text()
    if name == "requirements-dev.txt":
        text = text.replace("-r requirements.txt", "")
    return {line.split(">=")[0].split("<")[0].strip()
            for line in text.splitlines()
            if line.strip() and not line.startswith("#")}

declared = declared_in("requirements.txt")
dev_declared = declared_in("requirements-dev.txt")
declared |= dev_declared
check("every external import is declared", external <= declared,
      f"imported {sorted(external)}, declared {sorted(declared)}")
check("no declared runtime dependency is unused", declared_in("requirements.txt") <= external,
      f"unused {sorted(declared_in('requirements.txt') - external)}")
check("pytest is in the dev requirements",
      "pytest" in (ROOT / "requirements-dev.txt").read_text())

# ── summary ──────────────────────────────────────────────────────────────
print(f"\n{'=' * 72}")
print("ALL VERIFICATION CHECKS PASSED" if os_ok else "SOME VERIFICATION CHECKS FAILED")
print(f"{'=' * 72}")
sys.exit(0 if os_ok else 1)
