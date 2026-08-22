"""Which build is this process, and since when.

The release path is automatic (CI deploys once every check is green), and
automatic is when nobody looks. The failure that motivated this was silent by
nature: a backend running code older than main answers every health check
perfectly, because an old build is not a sick one. Twice in one session the
only way to find out was to remember pressing a button.

So the liveness probe carries the build's identity. Two fields, because they
fail in different ways:

  commit     -- exact, but only if the host says so. Never invented.
  started_at -- always available, and enough on its own to see that a deploy
                landed: the process it replaced cannot have started later.

NOTHING HERE IS HOST-SPECIFIC BY REQUIREMENT. The commit arrives in an
environment variable and this module does not care whose. `APP_GIT_COMMIT` is
the documented name -- set it from a build arg, a start script, anything. The
rest of the list is recognised purely so that a deployer whose platform
already injects one has nothing at all to configure; adding a name to it is a
one-line courtesy, not a dependency.
"""

import os
import re
from datetime import UTC, datetime

# In precedence order. The app's own name first: a self-hoster has to be able
# to override a host variable that is wrong or stale, and a list that let the
# platform win would make that impossible.
_COMMIT_ENV_NAMES = (
    "APP_GIT_COMMIT",  # this app's own; documented, set it anywhere
    "GIT_COMMIT",  # Jenkins and most generic CI images
    "GITHUB_SHA",  # GitHub Actions, when the image is built there
    "RENDER_GIT_COMMIT",  # Render
    "SOURCE_VERSION",  # Heroku, Dokku
    "RAILWAY_GIT_COMMIT_SHA",  # Railway
    "KOYEB_GIT_SHA",  # Koyeb
    "VERCEL_GIT_COMMIT_SHA",  # Vercel
)

# Abbreviated (7) through full (40). Anything else is not a commit and is not
# repeated: this value is read from the environment and served on an endpoint
# that asks for no credentials, so the shape check is the boundary between
# 「report the build」 and 「echo whatever was in that variable to the
# internet」. A wrong paste lands in exactly such a variable.
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")

_SHORT = 7

# Import time is process start: this module is imported while the app is being
# built, before uvicorn binds a port. A restart is the one signal that needs no
# cooperation from the host.
STARTED_AT = datetime.now(UTC)


def commit() -> str | None:
    """The short hash of the build running, or None if nothing here knows.

    None, never a placeholder. 「unknown」 in this field reads as an answer at
    a glance, and the one mistake this must not cause is somebody believing a
    deploy landed when nothing measured it.
    """
    for name in _COMMIT_ENV_NAMES:
        raw = (os.environ.get(name) or "").strip()
        if raw and _SHA.fullmatch(raw):
            return raw.lower()[:_SHORT]
    return None


def started_at() -> str:
    """Process start, as UTC with the Z said out loud.

    Naive timestamps get read in the reader's own timezone, and 「is this
    newer than my push?」 is precisely a comparison across timezones.
    """
    return STARTED_AT.isoformat(timespec="seconds").replace("+00:00", "Z")


def version() -> dict[str, str | None]:
    """The block both /healthz answers carry -- the router's and, in setup
    mode, the middleware's."""
    return {"commit": commit(), "started_at": started_at()}
