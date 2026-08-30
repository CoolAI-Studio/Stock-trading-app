import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.config import fernet_key, settings


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
        # 走 config.fernet_key，不要自己 Fernet(key)：那個值可以是部署平台自動產生的
        # 普通隨機字串（形狀不是 Fernet 的），而**這裡是唯一真的拿它去加解密的地方**。
        # 判準跟開機檢查、設定頁分開寫的話，會出現「開機說沒問題、存的時候才爆」。
        key = settings.SECRET_ENCRYPTION_KEY
        usable = fernet_key(key) if key else None
        if usable is None:
            raise RuntimeError(
                "SECRET_ENCRYPTION_KEY is not set or is too short -- cannot "
                "encrypt/decrypt stored secrets. Any random string of 24+ characters "
                "works; a hosting platform's 「generate a value」 button produces one."
            )
        return Fernet(usable)

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
