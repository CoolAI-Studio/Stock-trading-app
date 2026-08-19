"""A window in which a channel stays silent.

US market hours are the middle of the night in Taipei, so a strategy firing at
03:00 makes the phone ring. The only control the owner had was disabling the
whole channel -- which takes the stop-loss alerts with it. That is this
product's critical failure reached through the front door: the owner switches
the warnings off themselves, because the warnings are unusable.

**Deferred, never dropped.** A notification raised inside the window is held
and delivered when the window ends, reusing the retry queue that already
exists for failed deliveries. Dropping it would produce the same silence,
just chosen by us rather than by them -- and the event that fires at 3am is
often the one that mattered most.
"""

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.mixins import utcnow

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Taipei"


def _zone(timezone: str | None) -> ZoneInfo:
    """Never raises. A bad value in the column must not take the notification
    path down -- the failure mode there is no alerts at all, which is worse
    than a window read in the wrong zone."""
    try:
        return ZoneInfo(timezone or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown timezone %r; falling back to %s", timezone, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


def is_quiet(
    start_hour: int | None,
    end_hour: int | None,
    timezone: str | None,
    at: datetime | None = None,
) -> bool:
    if start_hour is None or end_hour is None or start_hour == end_hour:
        return False

    moment = (at or utcnow()).astimezone(_zone(timezone))
    hour = moment.hour

    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    # Wraps midnight -- 23:00 to 07:00 is the ordinary case, and the one a
    # plain start <= hour < end comparison gets exactly backwards.
    return hour >= start_hour or hour < end_hour


def window_ends_at(
    start_hour: int,
    end_hour: int,
    timezone: str | None,
    at: datetime | None = None,
) -> datetime:
    """When the held notification becomes due.

    The next occurrence of `end_hour` in the owner's own timezone, which for
    an evening start is tomorrow morning.
    """
    zone = _zone(timezone)
    moment = (at or utcnow()).astimezone(zone)
    ends = moment.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if ends <= moment:
        ends += timedelta(days=1)
    # Returned in UTC, like every other timestamp this app stores. Handing
    # back a Taipei-local value put the wall clock into the column and SQLite
    # dropped the offset, so 07:00 Taipei was read back as 07:00 UTC and the
    # notification came out eight hours late.
    return ends.astimezone(UTC)
