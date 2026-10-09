# grader-base — shared framework image for builder and grader stages.
#
# Contains the Python framework (config loader, test runner, entrypoint),
# the canonical GraderResult JSON schema, and PyYAML. Derived images
# (builder-example, grader-example) FROM this image and add only their
# stage config YAML, task tests, and example workspace.
#
# Self-contained, stateless, no network. Runs as a non-root user.
FROM python:3.12-slim

# Non-root runtime user (UID/GID 1000). Named "runner" because this base
# serves both the build and grade stages.
RUN groupadd --gid 1000 runner \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/sh runner

WORKDIR /opt/grader-base

# Install pinned Python dependencies (PyYAML only).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the shared framework and canonical schema.
COPY framework/ ./framework/
COPY grader_result.schema.json ./

# Create the tasks directory mount point (derived images populate it).
RUN mkdir -p /opt/grader-base/tasks

USER runner

# Diagnostics to stderr, the single GraderResult JSON to stdout.
# Derived images inherit this entrypoint; they only override config/tasks.
ENTRYPOINT ["python3", "/opt/grader-base/framework/run.py"]
