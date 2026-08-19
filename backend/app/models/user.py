from sqlalchemy import Boolean, String
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
    # Reserved for a future 2FA fast-follow (gated on the first real broker
    # adapter shipping) -- unused in v1, present now so enabling it later is
    # endpoint-only with no migration.
    totp_secret: Mapped[str | None] = mapped_column(String(64), default=None)
