import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.config import settings


class EncryptedJSON(TypeDecorator):
    """Fernet-encrypts a JSON-serializable dict at rest.

    Every write to a secret-bearing column (bot tokens, SMTP passwords) goes
    through this so it's impossible to accidentally persist one in cleartext.
    The key is read from settings at call time (not cached at import) so it
    fails only when actually used, not merely when the column is declared.
    """

    impl = String
    cache_ok = True

    def _fernet(self) -> Fernet:
        key = settings.SECRET_ENCRYPTION_KEY
        if not key:
            raise RuntimeError(
                "SECRET_ENCRYPTION_KEY is not set -- cannot encrypt/decrypt stored "
                "secrets. Generate one with: python -c \"from cryptography.fernet "
                'import Fernet; print(Fernet.generate_key().decode())"'
            )
        return Fernet(key.encode() if isinstance(key, str) else key)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        payload = json.dumps(value).encode("utf-8")
        return self._fernet().encrypt(payload).decode("utf-8")

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            payload = self._fernet().decrypt(value.encode("utf-8"))
        except InvalidToken as exc:
            raise RuntimeError(
                "Stored secret could not be decrypted -- wrong SECRET_ENCRYPTION_KEY?"
            ) from exc
        return json.loads(payload)
