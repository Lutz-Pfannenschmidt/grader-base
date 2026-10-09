#!/usr/bin/env bash
# Smoke tests for the grader-base framework image.
#
# Builds the base image, then runs it with a minimal mounted config, task,
# and workspace to verify the framework correctly executes ordered steps,
# runs tests, and emits a valid GraderResult. Exits non-zero on any failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="grader-base:latest"

TMPDIR="$(mktemp -d)"
OUT="$TMPDIR/base_stdout"
ERR="$TMPDIR/base_stderr"
trap 'rm -rf "$TMPDIR"' EXIT

green() { printf '\033[32m%s\033[0m\n' "$*"; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

PASS=0
FAIL=0

# validate_json <json-string> <label>
validate_json() {
  local json="$1" label="$2"
  python3 - "$json" "$label" <<'PY' || return 1
import json, sys
json_str, label = sys.argv[1], sys.argv[2]
try:
    obj = json.loads(json_str)
except Exception as e:
    print(f"  {label}: NOT valid JSON: {e}", file=sys.stderr)
    sys.exit(1)
errors = []
if not isinstance(obj, dict):
    errors.append("top-level value is not an object")
else:
    if "steps" not in obj:
        errors.append("missing 'steps'")
    elif not isinstance(obj["steps"], list):
        errors.append("'steps' is not a list")
    else:
        for i, s in enumerate(obj["steps"]):
            if not isinstance(s, dict): errors.append(f"step[{i}] not object")
            elif "name" not in s or "logs" not in s or "failure" not in s:
                errors.append(f"step[{i}] missing name/logs/failure")
            elif not isinstance(s["name"], str):
                errors.append(f"step[{i}].name not string")
            elif not isinstance(s["logs"], list):
                errors.append(f"step[{i}].logs not list")
            elif not all(isinstance(x, str) for x in s["logs"]):
                errors.append(f"step[{i}].logs has non-string element")
            elif not isinstance(s["failure"], bool):
                errors.append(f"step[{i}].failure not bool")
    if "failure" not in obj:
        errors.append("missing top-level 'failure'")
    elif not isinstance(obj["failure"], bool):
        errors.append("top-level 'failure' not bool")
    if "tests" in obj and obj["tests"] is not None:
        t = obj["tests"]
        if not isinstance(t, dict): errors.append("'tests' not object")
        else:
            for k in ("run","passed","failures","errors","failure"):
                if k not in t: errors.append(f"tests missing '{k}'")
            if "run" in t and not isinstance(t["run"], int):
                errors.append("tests.run not int")
            elif "run" in t and t["run"] < 0:
                errors.append("tests.run is negative")
            if "failure" in t and not isinstance(t["failure"], bool):
                errors.append("tests.failure not bool")
            if "passed" in t:
                if not isinstance(t["passed"], list):
                    errors.append("tests.passed not list")
                elif not all(isinstance(x, str) for x in t["passed"]):
                    errors.append("tests.passed has non-string element")
            for k in ("failures","errors"):
                if k in t and isinstance(t[k], list):
                    for j, f in enumerate(t[k]):
                        if not isinstance(f, dict) or "method" not in f or "traceback" not in f:
                            errors.append(f"tests.{k}[{j}] not {{method,traceback}} object")
                        elif not isinstance(f["method"], str) or not isinstance(f["traceback"], str):
                            errors.append(f"tests.{k}[{j}] method/traceback not strings")
            if all(k in t and isinstance(t[k], list) for k in ("passed","failures","errors")) \
               and "run" in t and isinstance(t["run"], int):
                lo = len(t["passed"]) + len(t["failures"])
                hi = lo + len(t["errors"])
                if not (lo <= t["run"] <= hi):
                    errors.append(
                        f"tests.run={t['run']} not in [{lo}, {hi}]"
                    )
            if "failure" in t and isinstance(t["failure"], bool) \
               and all(k in t and isinstance(t[k], list) for k in ("failures","errors")):
                if t["failure"] != (bool(t["failures"]) or bool(t["errors"])):
                    errors.append("tests.failure disagrees with failures/errors emptiness")
    for forbidden in ("points", "score", "grade"):
        if forbidden in obj:
            errors.append(f"forbidden field '{forbidden}' present")
if errors:
    print(f"  {label}: INVALID: " + "; ".join(errors), file=sys.stderr)
    sys.exit(1)
sys.exit(0)
PY
}

# run_case <label> <expect_exit> <docker args...>
run_case() {
  local label="$1"; shift
  local expect_exit="$1"; shift
  set +e
  docker run --rm "$@" "$IMAGE" >"$OUT" 2>"$ERR"
  RC=$?
  set -e
  if [ "$RC" != "$expect_exit" ]; then
    red "FAIL: $label — exit code $RC (expected $expect_exit)"
    cat "$ERR" >&2 || true
    FAIL=$((FAIL+1))
    return
  fi
  echo "  stderr tail:" >&2
  tail -n 3 "$ERR" >&2 || true
  if [ "$expect_exit" = "0" ]; then
    if validate_json "$(cat "$OUT")" "$label"; then
      green "PASS: $label (exit $RC)"
      PASS=$((PASS+1))
    else
      red "FAIL: $label — JSON validation failed"
      FAIL=$((FAIL+1))
    fi
  else
    green "PASS: $label (exit $RC as expected)"
    PASS=$((PASS+1))
  fi
}

bold "Building image $IMAGE ..."
docker build -t "$IMAGE" "$SCRIPT_DIR"

# --- Create a minimal exercise for testing the base framework ------------
EXERCISE="add_pair"
mkdir -p "$TMPDIR/tasks/$EXERCISE/tests"
cat > "$TMPDIR/tasks/$EXERCISE/tests/test_add_pair.py" <<'PY'
from solution import add_pair

def test_basic():
    assert add_pair(2, 3) == 5

def test_zero():
    assert add_pair(0, 0) == 0
PY

mkdir -p "$TMPDIR/workspace/$EXERCISE"
cat > "$TMPDIR/workspace/$EXERCISE/solution.py" <<'PY'
def add_pair(a, b):
    return a + b
PY

# Minimal config: one compile step + tests.
cat > "$TMPDIR/config.yaml" <<'YAML'
steps:
  - name: compile
    command:
      - python3
      - "-c"
      - "import sys, io; src = io.open(sys.argv[1], encoding='utf-8').read(); compile(src, sys.argv[1], 'exec'); print('syntax ok')"
      - "${SUBMISSION}/solution.py"
    on_failure: stop

tests:
  command:
    - python3
    - /opt/grader-base/framework/testrunner.py
    - "${SUBMISSION}"
    - "${TESTS_DIR}"
YAML

bold "Case 1: correct submission — expect exit 0, success"
run_case "correct submission" 0 \
  -e COURSE=test -e STUDENT=alice -e EXERCISE=add_pair -e JOB_KIND=build \
  -v "$TMPDIR/config.yaml:/opt/grader-base/config.yaml" \
  -v "$TMPDIR/tasks:/opt/grader-base/tasks" \
  -v "$TMPDIR/workspace:/workspace"
python3 -c '
import json,sys
o=json.loads(sys.argv[1])
assert o["failure"] is False, o
assert o["steps"][0]["name"]=="compile" and o["steps"][0]["failure"] is False
assert o["tests"]["run"]==2, o["tests"]["run"]
assert len(o["tests"]["passed"])==2
assert o["tests"]["failure"] is False
print("  contract checks: ok")
' "$(cat "$OUT")" || { red "FAIL: correct contract assertion"; FAIL=$((FAIL+1)); }

bold "Case 2: broken solution (mounted) — expect exit 0, failure"
mkdir -p "$TMPDIR/broken/$EXERCISE"
cat > "$TMPDIR/broken/$EXERCISE/solution.py" <<'PY'
def add_pair(a, b):
    return a - b  # wrong
PY
run_case "broken solution" 0 \
  -e EXERCISE=add_pair -e JOB_KIND=grade \
  -v "$TMPDIR/config.yaml:/opt/grader-base/config.yaml" \
  -v "$TMPDIR/tasks:/opt/grader-base/tasks" \
  -v "$TMPDIR/broken:/workspace"
python3 -c '
import json,sys
o=json.loads(sys.argv[1])
assert o["failure"] is True, o
assert o["steps"][0]["failure"] is False, "compile should pass"
assert o["tests"]["failure"] is True
assert len(o["tests"]["failures"])>=1, o["tests"]["failures"]
print("  contract checks: ok")
' "$(cat "$OUT")" || { red "FAIL: broken contract assertion"; FAIL=$((FAIL+1)); }

bold "Case 3: syntax error (mounted) — expect exit 0, compile failure, no tests"
mkdir -p "$TMPDIR/syntaxerr/$EXERCISE"
cat > "$TMPDIR/syntaxerr/$EXERCISE/solution.py" <<'PY'
def add_pair(a, b
    return a + b
PY
run_case "syntax error" 0 \
  -e EXERCISE=add_pair -e JOB_KIND=build \
  -v "$TMPDIR/config.yaml:/opt/grader-base/config.yaml" \
  -v "$TMPDIR/tasks:/opt/grader-base/tasks" \
  -v "$TMPDIR/syntaxerr:/workspace"
python3 -c '
import json,sys
o=json.loads(sys.argv[1])
assert o["failure"] is True
assert o["steps"][0]["name"]=="compile" and o["steps"][0]["failure"] is True
assert "tests" not in o, "tests must be omitted when compile fails"
print("  contract checks: ok")
' "$(cat "$OUT")" || { red "FAIL: syntax contract assertion"; FAIL=$((FAIL+1)); }

bold "Case 4: missing EXERCISE — expect nonzero exit"
run_case "missing EXERCISE" 2 \
  -e COURSE=x -e STUDENT=y -e JOB_KIND=build \
  -v "$TMPDIR/config.yaml:/opt/grader-base/config.yaml" \
  -v "$TMPDIR/tasks:/opt/grader-base/tasks" \
  -v "$TMPDIR/workspace:/workspace"

bold "Case 5: COMMAND_TIMEOUT enforced (infinite loop) — expect exit 0, failure"
mkdir -p "$TMPDIR/loop/$EXERCISE"
cat > "$TMPDIR/loop/$EXERCISE/solution.py" <<'PY'
def add_pair(a, b):
    while True:
        pass
PY
run_case "timeout_loop" 0 \
  -e EXERCISE=add_pair -e JOB_KIND=build -e COMMAND_TIMEOUT=2 \
  -v "$TMPDIR/config.yaml:/opt/grader-base/config.yaml" \
  -v "$TMPDIR/tasks:/opt/grader-base/tasks" \
  -v "$TMPDIR/loop:/workspace"
python3 -c '
import json,sys
o=json.loads(sys.argv[1])
assert o["failure"] is True, o
assert "tests" in o, "tests must be present (compile passed, test timed out)"
assert o["tests"]["failure"] is True
assert len(o["tests"]["errors"])>=1, o["tests"]["errors"]
assert any("timed out" in e["traceback"] for e in o["tests"]["errors"]), o["tests"]["errors"]
print("  contract checks: ok")
' "$(cat "$OUT")" || { red "FAIL: timeout contract assertion"; FAIL=$((FAIL+1)); }

bold "Case 6: boolean YAML timeout rejected — expect exit 2 (config error)"
cat > "$TMPDIR/config_bool_timeout.yaml" <<'YAML'
steps:
  - name: compile
    command: [python3, "-c", "print('ok')"]
    timeout: true
tests:
  command: [python3, "-c", "print('ok')"]
YAML
run_case "boolean step timeout" 2 \
  -e EXERCISE=add_pair -e JOB_KIND=build \
  -v "$TMPDIR/config_bool_timeout.yaml:/opt/grader-base/config.yaml" \
  -v "$TMPDIR/tasks:/opt/grader-base/tasks" \
  -v "$TMPDIR/workspace:/workspace"

cat > "$TMPDIR/config_bool_test_timeout.yaml" <<'YAML'
steps:
  - name: compile
    command: [python3, "-c", "print('ok')"]
tests:
  command: [python3, "-c", "print('ok')"]
  timeout: false
YAML
run_case "boolean tests timeout" 2 \
  -e EXERCISE=add_pair -e JOB_KIND=build \
  -v "$TMPDIR/config_bool_test_timeout.yaml:/opt/grader-base/config.yaml" \
  -v "$TMPDIR/tasks:/opt/grader-base/tasks" \
  -v "$TMPDIR/workspace:/workspace"

bold "Case 7: empty tests dir — expect exit 2 (config error, not silent pass)"
mkdir -p "$TMPDIR/emptytasks/no_tests/tests"
mkdir -p "$TMPDIR/emptyws/no_tests"
cat > "$TMPDIR/emptyws/no_tests/solution.py" <<'PY'
def add_pair(a, b):
    return a + b
PY
run_case "empty tests dir" 2 \
  -e EXERCISE=no_tests -e JOB_KIND=build \
  -v "$TMPDIR/config.yaml:/opt/grader-base/config.yaml" \
  -v "$TMPDIR/emptytasks:/opt/grader-base/tasks" \
  -v "$TMPDIR/emptyws:/workspace"

bold "Summary"
echo "  passed: $PASS"
echo "  failed: $FAIL"
if [ "$FAIL" -ne 0 ]; then
  red "SMOKE TESTS FAILED"
  exit 1
fi
green "ALL SMOKE TESTS PASSED"
