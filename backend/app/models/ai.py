from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedJSON
from app.models.mixins import TimestampMixin


class AiSettings(TimestampMixin, Base):
    """One row per user: which model to ask, and the key to ask it with.

    AI was the only secret in this codebase that lived in an environment
    variable. Telegram tokens, LINE tokens, SMTP passwords and broker
    credentials are all here, Fernet-encrypted, managed on a page with a 測試
    button -- and AI's absence from that pattern had exactly the consequences
    the pattern exists to prevent: nothing in the app said the feature existed,
    adding it meant a Render page the app never mentions, and CHANGING it meant
    a redeploy, because Render restarts the service on every environment
    change. A minute of downtime to fix a typo in a model name, on the product
    whose whole promise is not going down.

    This row is an OVERRIDE, not a replacement: a deployment that already set
    AI_API_KEY keeps working untouched, and deleting the row falls back to it
    rather than switching the feature off. See services/ai_settings.py.
    """

    __tablename__ = "ai_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Unique, like risk_settings: one set of preferences per person, not a
    # pile of rows whose order decides the answer.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    provider: Mapped[str] = mapped_column(String(32))
    base_url: Mapped[str] = mapped_column(String(255))
    # May name several models separated by commas; openai_compatible tries them
    # in order, which is what makes a free-tier setup usable at all.
    model: Mapped[str] = mapped_column(String(255))
    # {"api_key": "..."} -- a dict because EncryptedJSON stores JSON, and the
    # same reason every other secret column in this app uses it: a key in
    # cleartext in the database is a key in every backup of it.
    api_key_encrypted: Mapped[dict] = mapped_column(EncryptedJSON)
