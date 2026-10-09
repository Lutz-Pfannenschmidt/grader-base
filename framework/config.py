#!/usr/bin/env python3
"""Simple YAML config loader for the grader-base framework.

Loads a stage config that describes an ordered list of build/check steps
and an optional tests block. Uses PyYAML (pinned in requirements.txt); no
hand-rolled YAML parsing.

Config format (minimal)::

    steps:
      - name: compile
        command: [python3, "-c", "print('hi')"]
        on_failure: stop          # "stop" (default) or "continue"
        timeout: 60               # optional; defaults to COMMAND_TIMEOUT
    tests:
      command: [python3, testrunner.py, submission, tests]
      timeout: 60                 # optional; defaults to COMMAND_TIMEOUT

Each ``command`` is a YAML list of strings (an argv array). The framework
executes it directly via ``subprocess`` — **no shell** is involved. String
arguments may contain ``${VAR}`` placeholders which are expanded from the
process environment (e.g. ``${SUBMISSION}``, ``${TESTS_DIR}``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Step:
    """One ordered build/check step."""

    name: str
    command: list[str]
    on_failure: str = "stop"  # "stop" or "continue"
    timeout: int | None = None  # None → fall back to COMMAND_TIMEOUT env var


@dataclass
class TestsConfig:
    """Optional test block; runs after steps unless a step stopped."""

    command: list[str]
    timeout: int | None = None


@dataclass
class Config:
    """A fully loaded stage config."""

    steps: list[Step] = field(default_factory=list)
    tests: TestsConfig | None = None


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def load_config(path: Path) -> Config:
    """Load and validate a stage config from ``path``.

    Raises ``ValueError`` (or ``FileNotFoundError``) on any problem; the
    caller is expected to surface these as invocation errors (exit code 2).
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    _require(isinstance(raw, dict), "config must be a YAML mapping")

    steps: list[Step] = []
    for i, s in enumerate(raw.get("steps", []) or []):
        _require(isinstance(s, dict), f"steps[{i}] must be a mapping")
        _require("name" in s, f"steps[{i}] missing 'name'")
        _require(isinstance(s["name"], str), f"steps[{i}].name must be a string")
        _require("command" in s, f"steps[{i}] missing 'command'")
        _require(
            isinstance(s["command"], list)
            and all(isinstance(c, str) for c in s["command"]),
            f"steps[{i}].command must be a list of strings",
        )
        on_failure = s.get("on_failure", "stop")
        _require(
            on_failure in ("stop", "continue"),
            f"steps[{i}].on_failure must be 'stop' or 'continue'",
        )
        timeout = s.get("timeout")
        if timeout is not None:
            _require(
                isinstance(timeout, int) and not isinstance(timeout, bool),
                f"steps[{i}].timeout must be an int (not a bool)",
            )
            _require(timeout > 0, f"steps[{i}].timeout must be positive")
        steps.append(
            Step(
                name=s["name"],
                command=list(s["command"]),
                on_failure=on_failure,
                timeout=timeout,
            )
        )

    tests: TestsConfig | None = None
    raw_tests = raw.get("tests")
    if raw_tests is not None:
        _require(isinstance(raw_tests, dict), "'tests' must be a mapping")
        _require("command" in raw_tests, "tests missing 'command'")
        _require(
            isinstance(raw_tests["command"], list)
            and all(isinstance(c, str) for c in raw_tests["command"]),
            "tests.command must be a list of strings",
        )
        timeout = raw_tests.get("timeout")
        if timeout is not None:
            _require(
                isinstance(timeout, int) and not isinstance(timeout, bool),
                "tests.timeout must be an int (not a bool)",
            )
            _require(timeout > 0, "tests.timeout must be positive")
        tests = TestsConfig(
            command=list(raw_tests["command"]),
            timeout=timeout,
        )

    return Config(steps=steps, tests=tests)
