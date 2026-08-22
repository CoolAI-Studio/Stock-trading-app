# Platform-agnostic -- builds and runs the same way on Render, Fly.io, or
# any other host that can run a container and inject a PORT env var.
FROM python:3.13-slim

WORKDIR /app

# bcrypt<4.1 is pinned in requirements.txt for passlib compat (see
# app/core/security.py) -- no extra system packages needed for it or for
# cryptography/psycopg2-binary on this base image.
# requirements.lock, not requirements.txt: the ranges in requirements.txt say
# what this app depends on, and the lock says what it was last tested with.
# Building from the ranges meant every Manual Deploy pulled whatever the
# upstreams had published that day, months after the version anyone had run --
# and one breaking release stops this container starting, which with the CMD
# below is the whole service. CI installs the same file, so the tests run
# against the build that ships.
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY . .

ENV PYTHONUNBUFFERED=1

# Which build is this? /healthz reports it, so 「did my deploy actually land?」
# has an answer instead of a guess -- an out-of-date backend passes every
# health check, because an old build is not a sick one.
#
# Optional, and nothing breaks without it: hosts that already inject their own
# (RENDER_GIT_COMMIT, SOURCE_VERSION, RAILWAY_GIT_COMMIT_SHA, ...) are read at
# runtime instead, and a build with neither reports no commit rather than a
# wrong one. For a hand-built image:
#     docker build --build-arg APP_GIT_COMMIT=$(git rev-parse HEAD) ...
ARG APP_GIT_COMMIT=""
ENV APP_GIT_COMMIT=${APP_GIT_COMMIT}

EXPOSE 8000

# Migrations still run on every boot (idempotent -- a no-op once already at
# head), but through a script rather than `alembic ... && uvicorn ...`.
#
# THE `&&` WAS THE BUG. A DATABASE_URL with one character wrong makes alembic
# exit non-zero, so uvicorn never ran and no port was ever bound: the deployer
# got a dead URL. And DATABASE_URL is the one value on that form the app cannot
# generate -- it has to be copied from somebody else's console -- so it is the
# single most likely field to be wrong.
#
# scripts/start.py reports a failed migration instead of dying on it, then
# starts the server anyway: the setup page exists precisely to explain this
# situation, and it can only do that from a process that is alive.
#
# --workers 1 is set inside that script: the background market-data worker and
# the in-memory WS ticket store are process-local singletons, and a second
# worker process would run its own polling loop and duplicate every signal.
CMD ["python", "scripts/start.py"]
