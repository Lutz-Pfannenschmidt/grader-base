# grader-base

The shared framework base image for the **builder** and **grader** stages.
Derived images (`builder-example`, `grader-example`) `FROM grader-base:latest`
and add only their stage config YAML, task tests, example workspace, smoke
tests, stage README, and (for graders) the task structure manifest at
`/hopper/manifest.yaml` — no `run.py`, `testrunner.py`, schema, or framework
logic is duplicated.

## What it contains

```
grader-base/
  Dockerfile                  non-root python:3.12-slim, installs PyYAML
  .dockerignore
  requirements.txt            PyYAML==6.0.2 (the only third-party dep)
  grader_result.schema.json   canonical GraderResult JSON Schema
  framework/
    run.py                    entrypoint: loads config, runs steps, emits GraderResult
    config.py                 simple YAML config loader (PyYAML, no hand-rolled parser)
    testrunner.py             dependency-free test discovery + execution
  smoke.sh                    build + end-to-end smoke tests
  README.md
```

## Stage config format (YAML)

A derived image provides a `config.yaml` that the framework reads at
`CONFIG_PATH` (default `/opt/grader-base/config.yaml`):

```yaml
steps:
  - name: compile
    command:                       # argv array — NO shell, executed directly
      - python3
      - "-c"
      - "import sys; print('ok')"
      - "${SUBMISSION}/solution.py"
    on_failure: stop               # "stop" (default) or "continue"
    timeout: 60                    # optional; defaults to COMMAND_TIMEOUT env var

tests:                             # optional; omitted => no tests block in result
  command:
    - python3
    - /opt/grader-base/framework/testrunner.py
    - "${SUBMISSION}"
    - "${TESTS_DIR}"
  timeout: 60                      # optional; defaults to COMMAND_TIMEOUT
```

### Fields

| Section | Field        | Type       | Required | Default             | Notes                          |
| ------- | ------------ | ---------- | -------- | ------------------ | ------------------------------ |
| `steps` | `name`       | string     | yes      | —                  | Step label in the result.      |
|         | `command`    | string[]   | yes      | —                  | argv array; no shell.          |
|         | `on_failure` | `stop`/`continue` | no | `stop`             | `stop` halts and omits tests.  |
|         | `timeout`    | int        | no       | `COMMAND_TIMEOUT`  | Per-step timeout in seconds. Must be a plain integer; YAML booleans (`true`/`false`) are rejected.   |
| `tests` | `command`    | string[]   | yes      | —                  | argv array; stdout must be JSON. |
|         | `timeout`    | int        | no       | `COMMAND_TIMEOUT`  | Per-test timeout in seconds. Must be a plain integer; YAML booleans (`true`/`false`) are rejected.   |

### Variable interpolation

Command arguments may contain `${VAR}` placeholders, expanded from the
process environment before execution. The framework exports two computed
variables in addition to the inherited env vars:

| Variable     | Value                                   |
| ------------ | --------------------------------------- |
| `SUBMISSION` | `$WORKSPACE/$EXERCISE`                  |
| `TESTS_DIR`  | `$TASKS_BASE/$EXERCISE/tests`           |

No shell is ever invoked — commands are passed directly to
`subprocess.run` as argv arrays.

## GraderResult contract

The framework emits **exactly one** `GraderResult` JSON object on stdout
(the last line). All diagnostics go to stderr. The contract is preserved
exactly from the original builder/grader images:

| Field           | Type             | Rules                                              |
| --------------- | ---------------- | -------------------------------------------------- |
| `steps`         | array            | Named stages; empty allowed. Each `{name, logs[], failure}`. |
| `failure`       | bool             | Overall outcome (true if any step or tests failed). |
| `tests` | object, optional | Omitted when no `tests` block is configured, or when a step with `on_failure: stop` failed. |
| `tests.run`     | int              | Total tests run (runner-provided, not derived).    |
| `tests.passed`  | string[]         | Names of passing tests.                            |
| `tests.failures`| object[]         | `{"method","traceback"}` — failed assertions.      |
| `tests.errors`  | object[]         | `{"method","traceback"}` — setup/teardown errors.  |
| `tests.failure` | bool             | true when `failures` or `errors` are non-empty.    |

There are **no** `points`/`score`/`grade` fields anywhere. See
`grader_result.schema.json` for the machine-readable schema.

## Environment variables

| Var               | Required | Default                    | Purpose                                  |
| ----------------- | -------- | -------------------------- | ---------------------------------------- |
| `EXERCISE`        | yes      | —                          | Selects `$WORKSPACE/$EXERCISE` + tests.  |
| `COURSE`          | no       | `""`                       | Identification / diagnostics only.       |
| `STUDENT`         | no       | `""`                       | Identification / diagnostics only.       |
| `WORKSPACE`       | no       | `/workspace`               | Submission root.                         |
| `COMMAND_TIMEOUT` | no       | `300`                      | Per-step/per-test timeout in seconds.    |
| `JOB_KIND`        | no       | `""`                       | `build` or `grade`; passed through.      |
| `CONFIG_PATH`     | no       | `/opt/grader-base/config.yaml` | Stage config file.                  |
| `TASKS_BASE`      | no       | `/opt/grader-base/tasks`   | Bundled tests root.                      |

## Execution flow

1. Read and validate env vars (`EXERCISE` required).
2. Resolve submission at `$WORKSPACE/$EXERCISE`; error (exit 2) if missing.
   Export `SUBMISSION` for `${SUBMISSION}` interpolation in commands.
3. Load and validate `config.yaml`; error (exit 2) if missing/invalid
   (a `timeout` of `true`/`false` is invalid — it must be an integer).
4. If the config has a `tests` block, resolve the tests dir at
   `$TASKS_BASE/$EXERCISE/tests`; error (exit 2) if missing **or empty**
   (no `test_*.py` files). An empty tests dir is a configuration error,
   not a silent pass. Export `TESTS_DIR` for `${TESTS_DIR}` interpolation.
   A no-tests image (one whose config omits `tests`) is **not** required
   to bundle a tests directory — it never runs tests.
5. Execute steps in order. Each step's stdout+stderr become `logs`.
   - If a step fails and `on_failure: stop`, emit `{steps, failure:true}` and exit 0 (no tests).
   - If `on_failure: continue`, record the failure and proceed.
6. If `tests` is configured and no step stopped, run the test command.
   Its stdout JSON becomes the `tests` member. Timeouts and parse errors
   produce a structured error result.
7. Emit `{steps, failure, tests}` and exit 0.

Exit codes: `0` for completed runs (failures are in the JSON); `2` for
invocation/internal errors.

## Security / runtime

- Runs as a **non-root** user `runner` (UID/GID 1000).
- **No network** assumptions; the image does not clone or call out.
- **No secrets** are read or required.
- **Stateless**: no data is persisted between runs.
- `COMMAND_TIMEOUT` bounds each step and the test run via subprocess timeouts.
- Commands are argv arrays — **no shell** is invoked.

## Build

```bash
docker build -t grader-base:latest ./grader-base
```

Derived images must build the base first (see `build-images.sh` at the repo
root, or each derived image's `smoke.sh` which builds the base automatically).

## Smoke tests

```bash
./grader-base/smoke.sh
```

Builds the base image and runs five scenarios with a minimal mounted config:
correct submission, broken solution, syntax error, missing env, and timeout
enforcement — validating both exit codes and the `GraderResult` shape.
