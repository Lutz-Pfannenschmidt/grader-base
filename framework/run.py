#!/usr/bin/env python3
"""Shared grader-base framework entrypoint.

Reads a stage config (YAML), executes the ordered steps, optionally runs
tests, and emits exactly one ``GraderResult`` JSON object on stdout (the
last line). All diagnostics go to stderr.

This is the shared runtime for **both** image stages. A derived image
provides only a ``config.yaml``, its task tests, and an example workspace;
the framework, test runner, and schema live here and are never duplicated.

Environment variables (supplied by the grading runner / build service):

  ``EXERCISE``        (required) selects ``$WORKSPACE/$EXERCISE`` and
                       ``$TASKS_BASE/$EXERCISE/tests``.
  ``COURSE``          (optional) identification / diagnostics only.
  ``STUDENT``         (optional) identification / diagnostics only.
  ``WORKSPACE``       (default ``/workspace``) submission root.
  ``COMMAND_TIMEOUT`` (default ``300``) per-step/per-test timeout in seconds.
  ``JOB_KIND``        (optional) ``build`` or ``grade``; passed through.
  ``CONFIG_PATH``     (default ``/opt/grader-base/config.yaml``) stage config.
  ``TASKS_BASE``      (default ``/opt/grader-base/tasks``) bundled tests root.

The framework also exports ``SUBMISSION`` (``$WORKSPACE/$EXERCISE``) and
``TESTS_DIR`` (``$TASKS_BASE/$EXERCISE/tests``) into the environment so that
``${SUBMISSION}`` / ``${TESTS_DIR}`` placeholders in the config's command
argv arrays resolve correctly.

Exit codes:
  0  run completed (student/compile/test failures are reported in the
     JSON, not via the exit code).
  2  invocation/internal error (missing env var, missing submission dir,
     no bundled tests, unparseable config, ...).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Framework and base directories (resolved from this script's location).
FRAMEWORK_DIR = Path(__file__).resolve().parent
BASE_DIR = FRAMEWORK_DIR.parent

# Import the config loader from the same directory.
sys.path.insert(0, str(FRAMEWORK_DIR))
from config import Config, Step, TestsConfig, load_config  # noqa: E402

DEFAULT_WORKSPACE = "/workspace"
DEFAULT_TASKS_BASE = "/opt/grader-base/tasks"
DEFAULT_CONFIG_PATH = "/opt/grader-base/config.yaml"
DEFAULT_TIMEOUT = 300


def log(msg: str) -> None:
    """Diagnostic output goes to stderr only, never stdout."""
    print(msg, file=sys.stderr, flush=True)


def emit(result: dict) -> None:
    """Print the single GraderResult JSON object to stdout."""
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()


def interpolate(arg: str) -> str:
    """Expand ``${VAR}`` placeholders from the process environment."""
    return os.path.expandvars(arg)


def run_step(step: Step, timeout_default: int) -> dict:
    """Execute one config step and return a ``{name, logs, failure}`` dict."""
    logs: list[str] = []
    failure = False
    timeout = step.timeout if step.timeout is not None else timeout_default
    cmd = [interpolate(a) for a in step.command]
    log(f"[{step.name}] running {cmd}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        logs.append(f"exit code {proc.returncode}")
        if proc.stdout:
            logs.extend(proc.stdout.splitlines())
        if proc.stderr:
            logs.extend(proc.stderr.splitlines())
        failure = proc.returncode != 0
    except subprocess.TimeoutExpired:
        logs.append(f"timed out after {timeout}s")
        failure = True
    except FileNotFoundError as exc:
        logs.append(f"command not found: {exc}")
        failure = True
    return {"name": step.name, "logs": logs, "failure": failure}


def _test_error(traceback: str) -> dict:
    return {
        "run": 0,
        "passed": [],
        "failures": [],
        "errors": [{"method": "testrunner", "traceback": traceback}],
        "failure": True,
    }


def run_tests(tests_cfg: TestsConfig, timeout_default: int) -> dict:
    """Run the test command and parse its JSON output into a tests dict."""
    timeout = tests_cfg.timeout if tests_cfg.timeout is not None else timeout_default
    cmd = [interpolate(a) for a in tests_cfg.command]
    log(f"[test] running {cmd}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log(f"[test] timed out after {timeout}s")
        return _test_error(f"test run timed out after {timeout}s")
    except FileNotFoundError as exc:
        log(f"[test] command not found: {exc}")
        return _test_error(f"command not found: {exc}")

    if proc.stderr:
        for line in proc.stderr.splitlines():
            log(f"[testrunner] {line}")
    if proc.returncode != 0:
        log(f"[test] testrunner exited {proc.returncode}")
        return _test_error(
            proc.stdout.strip()
            or f"testrunner exited with code {proc.returncode}"
        )

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        log(f"[test] could not parse testrunner output: {proc.stdout!r}")
        return _test_error(f"unparseable testrunner output: {proc.stdout!r}")


def main() -> None:
    course = os.environ.get("COURSE", "")
    student = os.environ.get("STUDENT", "")
    exercise = os.environ.get("EXERCISE", "")
    workspace = os.environ.get("WORKSPACE", DEFAULT_WORKSPACE)
    job_kind = os.environ.get("JOB_KIND", "")
    config_path = os.environ.get("CONFIG_PATH", DEFAULT_CONFIG_PATH)
    tasks_base = os.environ.get("TASKS_BASE", DEFAULT_TASKS_BASE)
    try:
        timeout = int(os.environ.get("COMMAND_TIMEOUT", str(DEFAULT_TIMEOUT)))
    except ValueError:
        log(f"invalid COMMAND_TIMEOUT, using {DEFAULT_TIMEOUT}")
        timeout = DEFAULT_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_TIMEOUT

    log(
        f"job_kind={job_kind!r} course={course!r} student={student!r} "
        f"exercise={exercise!r} workspace={workspace!r} timeout={timeout}"
    )

    # --- Invocation / internal errors -> nonzero exit ---------------------
    if not exercise:
        log("ERROR: EXERCISE env var is required")
        sys.exit(2)

    submission = Path(workspace) / exercise
    if not submission.is_dir():
        log(f"ERROR: submission directory not found: {submission}")
        sys.exit(2)

    # Export the computed submission path so ${SUBMISSION} resolves in the
    # config's command argv arrays.
    os.environ["SUBMISSION"] = str(submission)

    config_file = Path(config_path)
    if not config_file.is_file():
        log(f"ERROR: config not found: {config_path}")
        sys.exit(2)
    try:
        config = load_config(config_file)
    except (ValueError, FileNotFoundError) as exc:
        log(f"ERROR: invalid config: {exc}")
        sys.exit(2)

    # The tests directory is only required when the config actually runs
    # tests. A no-tests image (e.g. a builder that only lints/compiles) is
    # not forced to bundle tests it would never run.
    tests_dir = Path(tasks_base) / exercise / "tests"
    if config.tests is not None:
        if not tests_dir.is_dir():
            log(f"ERROR: no tests bundled for exercise: {exercise}")
            sys.exit(2)
        if not any(tests_dir.glob("test_*.py")):
            log(
                f"ERROR: no test files found for exercise: {exercise} "
                f"in {tests_dir}"
            )
            sys.exit(2)
        os.environ["TESTS_DIR"] = str(tests_dir)

    # --- Execute ordered steps -------------------------------------------
    steps: list[dict] = []
    overall_failure = False
    stopped = False

    for step in config.steps:
        result = run_step(step, timeout)
        steps.append(result)
        if result["failure"]:
            overall_failure = True
            if step.on_failure == "stop":
                log(f"[{step.name}] failed; stopping (on_failure: stop)")
                stopped = True
                break
            else:
                log(f"[{step.name}] failed; continuing (on_failure: continue)")

    # --- Optional tests ---------------------------------------------------
    if stopped or config.tests is None:
        emit({"steps": steps, "failure": overall_failure})
        sys.exit(0)

    tests = run_tests(config.tests, timeout)
    if tests.get("failure"):
        overall_failure = True
    emit({"steps": steps, "failure": overall_failure, "tests": tests})
    sys.exit(0)


if __name__ == "__main__":
    main()
