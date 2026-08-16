"""Guards against model/migration drift: if someone edits a model but forgets
to generate a migration, `alembic upgrade head` on a fresh DB no longer
matches Base.metadata, and this test catches it immediately instead of
surfacing as a confusing runtime error later."""

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

import app.models  # noqa: F401
from alembic import command
from app.db.base import Base

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def test_alembic_head_matches_models(tmp_path, monkeypatch):
    db_path = tmp_path / "migration_check.db"
    scratch_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    # alembic/env.py does `from app.db.session import engine` -- since alembic
    # re-executes env.py fresh on every command, patching the attribute here
    # is enough to redirect it at a scratch DB instead of the real dev DB.
    monkeypatch.setattr("app.db.session.engine", scratch_engine)

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")

    with scratch_engine.connect() as connection:
        migration_ctx = MigrationContext.configure(connection)
        diff = compare_metadata(migration_ctx, Base.metadata)

    assert diff == [], f"Model/migration drift detected: {diff}"
