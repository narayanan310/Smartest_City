#!/usr/bin/env bash
#
# One command that verifies the whole project.
#
#   ./scripts/verify_all.sh            run everything
#   ./scripts/verify_all.sh --quick    skip the slower adversarial stages
#
# Exits non-zero on the first failing stage, so it is usable as a pre-commit hook or a
# CI entry point. This directory is not a git repository, so there is no GitHub Actions
# workflow; this script is the enforcement path.
#
# Stages, in order of increasing cost:
#   1  lint            ruff, clean
#   2  import          every module imports without side effects
#   3  tests           full pytest suite
#   4  validation      the 40-check results gate
#   5  self-check      the engine runs standalone
#   6  probe           edge-case probes and fuzz trials
#   7  sweep           cross-module invariant sweep
#   8  script          scripts/fit_shading.py runs end to end
#
# Every harness this gate uses lives in scripts/ inside the repository, so no stage
# depends on a temporary directory that the operating system may purge.

set -uo pipefail

QUICK=0
[[ "${1:-}" == "--quick" ]] && QUICK=1

cd "$(dirname "$0")/.." || exit 1
ROOT="$PWD"
PY="${PYTHON:-python3}"

FAILED=()
declare -a RESULTS

hr() { printf '%s\n' "────────────────────────────────────────────────────────────────"; }

stage() {
  local name="$1"; shift
  hr
  printf ' %-14s %s\n' "$name" "$*"
  hr
}

record() {
  RESULTS+=("$1")
  [[ "$1" == FAIL* ]] && FAILED+=("$2")
}

# ── 0. environment ────────────────────────────────────────────────────────
if [[ ! -d .venv ]]; then
  echo "error: no .venv here. Create one and install requirements-dev.txt:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 2
fi
# shellcheck disable=SC1091
source .venv/bin/activate

hr
printf ' %-14s %s\n' "environment" "$("$PY" -c 'import sys,streamlit,pandas,plotly;print(f"python {sys.version.split()[0]} | streamlit {streamlit.__version__} | pandas {pandas.__version__} | plotly {plotly.__version__}")')"
printf ' %-14s %s\n' "root" "$ROOT"
hr
echo

# ── 1. lint ───────────────────────────────────────────────────────────────
stage "1 lint" "ruff"
if ruff check . --output-format concise; then
  record "PASS  lint"
else
  record "FAIL  lint" "ruff check"
fi
echo

# ── 2. import ─────────────────────────────────────────────────────────────
stage "2 import" "every module imports cleanly"
IMPORT_OUT=$("$PY" - <<'PY' 2>&1
import importlib, pkgutil, sys
mods = ["app", "src.config.settings", "src.models.bus", "src.models.ecology",
        "src.algorithms.scheduler", "src.algorithms.solar",
        "src.simulation.engine", "src.visualization.map", "src.services.nasa_power"]
bad = 0
for name in mods:
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        bad += 1
print(f"  {len(mods) - bad}/{len(mods)} modules imported")
sys.exit(1 if bad else 0)
PY
)
if [[ $? -eq 0 ]]; then
  record "PASS  import"
else
  echo "$IMPORT_OUT"
  record "FAIL  import" "module import"
fi
echo "$IMPORT_OUT" | grep -E 'FAIL' || true
echo

# ── 3. tests ──────────────────────────────────────────────────────────────
stage "3 tests" "pytest"
if "$PY" -m pytest -q 2>&1 | tail -3; then
  record "PASS  tests"
else
  record "FAIL  tests" "pytest"
fi
echo

# ── 4. validation gate ────────────────────────────────────────────────────
stage "4 validation" "scripts/validate_results.py"
VAL_LOG=$(mktemp)
if "$PY" scripts/validate_results.py >"$VAL_LOG" 2>&1; then
  printf '  %s passed, %s failed\n' \
    "$(grep -c '\[PASS\]' "$VAL_LOG")" "$(grep -c '\[FAIL\]' "$VAL_LOG")"
  record "PASS  validation"
else
  grep -E '\[FAIL\]' "$VAL_LOG" || tail -20 "$VAL_LOG"
  record "FAIL  validation" "validate_results.py"
fi
rm -f "$VAL_LOG"
echo

# ── 5. engine self-check ──────────────────────────────────────────────────
stage "5 self-check" "python -m src.simulation.engine"
if "$PY" -m src.simulation.engine >/dev/null 2>&1; then
  record "PASS  self-check"
else
  record "FAIL  self-check" "engine __main__"
fi
echo

# ── 6. edge probes and fuzz ───────────────────────────────────────────────
if [[ $QUICK -eq 1 ]]; then
  stage "6 probe" "SKIPPED (--quick)"
  record "SKIP  probe"
else
  stage "6 probe" "edge probes + fuzz trials"
  FUZZ_LOG=$(mktemp)
    if "$PY" "scripts/probe_invariants.py" "$ROOT" >"$FUZZ_LOG" 2>&1; then
      printf '  %s probes passed | %s\n' \
        "$(grep -c '\[ok\]' "$FUZZ_LOG")" "$(grep -o 'violations: [0-9]*' "$FUZZ_LOG")"
      record "PASS  probe"
    else
      grep -E '\[FAIL\]' "$FUZZ_LOG" || tail -20 "$FUZZ_LOG"
      record "FAIL  probe" "probe_invariants.py"
    fi
  rm -f "$FUZZ_LOG"
  echo
fi

# ── 7. invariant sweep ────────────────────────────────────────────────────
if [[ $QUICK -eq 1 ]]; then
  stage "7 sweep" "SKIPPED (--quick)"
  record "SKIP  sweep"
else
  stage "7 sweep" "cross-module invariant sweep"
  SWEEP_LOG=$(mktemp)
    if "$PY" "scripts/sweep_invariants.py" "$ROOT" >"$SWEEP_LOG" 2>&1; then
      printf '  %s passed, %s failed\n' \
        "$(grep -c '\[PASS\]' "$SWEEP_LOG")" "$(grep -c '\[FAIL\]' "$SWEEP_LOG")"
      record "PASS  sweep"
    else
      grep -E '\[FAIL\]' "$SWEEP_LOG" || tail -20 "$SWEEP_LOG"
      record "FAIL  sweep" "sweep_invariants.py"
    fi
  rm -f "$SWEEP_LOG"
  echo
fi

# ── 8. analysis scripts ───────────────────────────────────────────────────
stage "8 scripts" "scripts/fit_shading.py"
if "$PY" scripts/fit_shading.py --seeds 2 >/dev/null 2>&1; then
  record "PASS  scripts"
else
  record "FAIL  scripts" "fit_shading.py"
fi
echo

# ── summary ───────────────────────────────────────────────────────────────
hr
printf ' %-14s %s\n' "summary" "${#RESULTS[@]} stages"
hr
for line in "${RESULTS[@]}"; do
  printf '  %s\n' "$line"
done
hr

if [[ ${#FAILED[@]} -gt 0 ]]; then
  printf ' RESULT: FAIL (%s failing stage(s): %s)\n' "${#FAILED[@]}" "${FAILED[*]}"
  exit 1
fi
printf ' RESULT: PASS\n'
exit 0
