import pytest
from cryptography.fernet import Fernet

from app.db.types import EncryptedJSON


class _FakeDialect:
    pass


def test_encrypted_json_round_trips_a_dict(monkeypatch):
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    col = EncryptedJSON()

    bound = col.process_bind_param({"bot_token": "abc123", "chat_id": 42}, _FakeDialect())
    assert bound is not None
    assert "abc123" not in bound  # must not be stored in cleartext

    restored = col.process_result_value(bound, _FakeDialect())
    assert restored == {"bot_token": "abc123", "chat_id": 42}


def test_encrypted_json_handles_none(monkeypatch):
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", Fernet.generate_key().decode())
    col = EncryptedJSON()

    assert col.process_bind_param(None, _FakeDialect()) is None
    assert col.process_result_value(None, _FakeDialect()) is None


def test_encrypted_json_raises_without_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.SECRET_ENCRYPTION_KEY", "")
    col = EncryptedJSON()

    with pytest.raises(RuntimeError):
        col.process_bind_param({"a": 1}, _FakeDialect())
