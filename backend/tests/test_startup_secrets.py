import sys

import pytest

from app.config import Settings, enforce_required_secrets, verify_required_secrets

# Explicit on every Settings(...) below: the developer's own backend/.env is
# read by pydantic-settings even under pytest, so anything left implicit here
# would pass or fail depending on whose machine the suite runs on.
REAL_SECRETS = {
    "JWT_SECRET": "5f2c8a1e9b7d43a6f0c1e8d2b4a67390",
    "TV_WEBHOOK_SECRET": "c41b8e07d29f4a6b83e15c9d70a2f6b8",
    # A valid Fernet key, checked at boot since the encryption key joined the
    # guard. Left implicit it came from the developer's own .env, and the
    # suite passed here and failed on CI -- which is the exact hazard the
    # comment above warns about.
    "SECRET_ENCRYPTION_KEY": "Lt9UC1IujubchFxDwwAPx8ZqfzXUnUS9KYjkXPCSxn8=",
    "ALLOW_INSECURE_SECRETS": False,
}


def test_real_secrets_are_accepted():
    verify_required_secrets(Settings(**REAL_SECRETS))


@pytest.mark.parametrize(
    "override",
    [
        {"JWT_SECRET": "dev-only-insecure-secret-change-me"},
        {"JWT_SECRET": "change-me-to-a-long-random-string"},
        {"JWT_SECRET": ""},
        {"JWT_SECRET": "   "},
        {"TV_WEBHOOK_SECRET": "change-me"},
        {"TV_WEBHOOK_SECRET": ""},
    ],
)
def test_placeholder_or_missing_secret_is_fatal(override):
    with pytest.raises(RuntimeError) as excinfo:
        verify_required_secrets(Settings(**{**REAL_SECRETS, **override}))
    assert next(iter(override)) in str(excinfo.value)


def test_guard_is_skipped_under_pytest():
    # The suite must run on a fresh checkout with no secrets configured at all.
    enforce_required_secrets(Settings(**{**REAL_SECRETS, "JWT_SECRET": "change-me"}))


def test_guard_fires_when_not_running_under_pytest(monkeypatch):
    monkeypatch.delitem(sys.modules, "pytest")
    with pytest.raises(RuntimeError):
        enforce_required_secrets(Settings(**{**REAL_SECRETS, "JWT_SECRET": "change-me"}))


def test_allow_insecure_secrets_opts_out(monkeypatch):
    monkeypatch.delitem(sys.modules, "pytest")
    enforce_required_secrets(
        Settings(
            **{**REAL_SECRETS, "JWT_SECRET": "change-me", "ALLOW_INSECURE_SECRETS": True},
        )
    )
