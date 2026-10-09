#!/usr/bin/env python3
"""Minimal dependency-free test runner (shared framework component).

Usage::

    python testrunner.py <submission_dir> <tests_dir>

It discovers ``test_*`` functions in every ``test_*.py`` file under
``tests_dir``, imports the student's ``solution`` module from
``submission_dir`` and runs each test. A plain ``assert`` failure becomes a
*failure*; any other exception becomes an *error*. It prints exactly one
JSON object on stdout shaped like the ``tests`` member of a GraderResult::

    {
      "run": 4,
      "passed": ["test_add_pair.test_basic", ...],
      "failures": [{"method": "...", "traceback": "..."}],
      "errors":   [{"method": "...", "traceback": "..."}],
      "failure": true
    }

Diagnostics go to stderr. The process always exits 0 when it managed to
produce a result (test failures are data, not an exit code); it exits 2
only on a usage error.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def empty_result() -> dict:
    return {
        "run": 0,
        "passed": [],
        "failures": [],
        "errors": [],
        "failure": False,
    }


def run_tests(submission_dir: Path, tests_dir: Path) -> dict:
    result = empty_result()
    solution_path = submission_dir / "solution.py"

    # Make the submission importable as a package-less `solution` module.
    if submission_dir.is_dir():
        sys.path.insert(0, str(submission_dir))

    if not solution_path.is_file():
        log(f"warning: no solution.py in {submission_dir}")
        result["errors"].append(
            {
                "method": "solution.import",
                "traceback": (
                    f"ModuleNotFoundError: no solution.py in {submission_dir}"
                ),
            }
        )
        result["failure"] = True
        return result

    try:
        load_module(solution_path, "solution")
    except Exception:
        tb = "".join(traceback.format_exception(*sys.exc_info()))
        result["errors"].append({"method": "solution.import", "traceback": tb})
        result["failure"] = True
        return result

    test_files = sorted(Path(tests_dir).glob("test_*.py"))
    if not test_files:
        log(f"no test files found in {tests_dir}")
        result["errors"].append(
            {
                "method": "testrunner",
                "traceback": f"no test files found in {tests_dir}",
            }
        )
        result["failure"] = True
        return result

    for tf in test_files:
        mod_name = tf.stem
        log(f"loading {mod_name}")
        try:
            mod = load_module(tf, mod_name)
        except Exception:
            tb = "".join(traceback.format_exception(*sys.exc_info()))
            result["errors"].append(
                {"method": f"{mod_name}.<import>", "traceback": tb}
            )
            result["failure"] = True
            continue

        for fname in sorted(dir(mod)):
            if not fname.startswith("test_"):
                continue
            fn = getattr(mod, fname)
            if not callable(fn):
                continue
            method = f"{mod_name}.{fname}"
            result["run"] += 1
            log(f"running {method}")
            try:
                fn()
            except AssertionError:
                tb = "".join(traceback.format_exception(*sys.exc_info()))
                result["failures"].append({"method": method, "traceback": tb})
            except Exception:
                tb = "".join(traceback.format_exception(*sys.exc_info()))
                result["errors"].append({"method": method, "traceback": tb})
            else:
                result["passed"].append(method)

    result["failure"] = bool(result["failures"] or result["errors"])
    return result


def main() -> None:
    if len(sys.argv) != 3:
        log("usage: python testrunner.py <submission_dir> <tests_dir>")
        sys.exit(2)
    submission_dir = Path(sys.argv[1]).resolve()
    tests_dir = Path(sys.argv[2]).resolve()
    result = run_tests(submission_dir, tests_dir)
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()
    sys.exit(0)


if __name__ == "__main__":
    main()
