from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    # Where this person actually is, which is not where the server is. The
    # container runs in UTC; quiet hours, and anything else the owner sets by
    # the clock, have to be read in their own zone or 23:00 means 07:00.
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Taipei")
    # Bumped when the password changes or the owner signs out everywhere.
    # Every token carries the version it was minted at, so raising this
    # revokes all of them at once -- the only way to actually take an account
    # back, since a JWT is otherwise valid until it expires.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    # The version of this account's personal TradingView URL (#115). The URL is
    # computed from this and a key derived from SECRET_ENCRYPTION_KEY and never
    # stored, so regenerating it is this number going up -- and every alert
    # still using the old URL is refused from that moment, with the reason
    # written into the webhook log.
    webhook_url_version: Mapped[int] = mapped_column(Integer, default=0)
    # Whether the OLD shared TradingView URL -- the one whose secret is written
    # into every legacy alert message -- still takes alerts. Once every alert
    # uses the personal URL, closing it makes a leaked copy of that secret
    # worthless. Open by default: an existing owner's old alerts still call it,
    # and an update must never silence them (#50).
    shared_webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # When an alert carrying the right secret last arrived there -- the one fact
    # the owner decides by: is anything still using it?
    shared_webhook_last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Two, not one: "last login" showing the login happening right now tells
    # the owner nothing. The one before it is what they can recognise or not.
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    previous_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    # Reserved for a future 2FA fast-follow (gated on the first real broker
    # adapter shipping) -- unused in v1, present now so enabling it later is
    # endpoint-only with no migration.
    totp_secret: Mapped[str | None] = mapped_column(String(64), default=None)
