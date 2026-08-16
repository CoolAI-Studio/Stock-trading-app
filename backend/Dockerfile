# Platform-agnostic -- builds and runs the same way on Render, Fly.io, or
# any other host that can run a container and inject a PORT env var.
FROM python:3.13-slim

WORKDIR /app

# bcrypt<4.1 is pinned in requirements.txt for passlib compat (see
# app/core/security.py) -- no extra system packages needed for it or for
# cryptography/psycopg2-binary on this base image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Runs the migration on every boot (idempotent -- a no-op once already at
# head) so a fresh deploy never needs a separate manual migration step.
# --workers 1 is required: the background market-data worker and the
# in-memory WS ticket store are both process-local singletons: a second
# worker process would run its own polling loop and duplicate every signal.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
