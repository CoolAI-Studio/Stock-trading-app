from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import EncryptedJSON
from app.models.mixins import TimestampMixin


class BrokerCredential(TimestampMixin, Base):
    """Encrypted storage for a broker/exchange's API credentials. No adapter
    reads this yet -- v1 only supports ManualConfirmBroker (see
    app/services/broker/). This exists so a user (or an AI-assisted future
    session) has somewhere safe to park credentials while wiring up their
    own BrokerAdapter, without inventing a new secrets story per broker.

    broker_name and config are both free-form (unlike NotificationChannel's
    fixed per-type schemas) because we don't know ahead of time which
    broker's API shape a given user will need."""

    __tablename__ = "broker_credentials"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    label: Mapped[str] = mapped_column(String(120))
    broker_name: Mapped[str] = mapped_column(String(120))
    # Fernet-encrypted at rest -- arbitrary key/value pairs, shape depends on
    # whichever broker this is for. See app/db/types.py::EncryptedJSON.
    config_encrypted: Mapped[dict] = mapped_column(EncryptedJSON)
