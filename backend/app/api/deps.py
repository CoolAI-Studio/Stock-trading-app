from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# The same scheme, but a missing Authorization header is not an error. Used by
# the setup endpoints, which have to answer a stranger during setup (nobody has
# an account yet) and only the owner afterwards.
_optional_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    subject, version = decode_token(token)
    if subject is None:
        raise credentials_error

    user = db.get(User, int(subject))
    if user is None:
        raise credentials_error

    # Where revocation actually happens. A token minted before the password
    # changed carries the older version and stops here, which is the whole
    # point -- otherwise whoever held it kept broker keys and order placement
    # for up to a day after the owner locked them out.
    if version < user.token_version:
        raise credentials_error

    return user


def optional_current_user(
    token: str | None = Depends(_optional_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """The signed-in user, or None -- never a 401.

    For routes whose AUDIENCE changes rather than whose access is refused. The
    setup endpoints are the case: during setup they must answer anybody,
    because nobody has an account yet and refusing would lock the deployer out
    of the page that exists to let them in; once the app is running they must
    answer only the owner, because they carry no authentication of their own
    and would otherwise sit open on a public URL forever.

    A malformed or revoked token is treated as no token. This never grants
    anything on its own -- the caller decides what None means -- so failing
    open here cannot become failing open somewhere that matters.
    """
    if not token:
        return None
    try:
        subject, version = decode_token(token)
        if subject is None:
            return None
        user = db.get(User, int(subject))
    except Exception:  # noqa: BLE001 -- any bad token is simply 「not signed in」
        return None
    if user is None or version < user.token_version or not user.is_active:
        return None
    return user


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return user
