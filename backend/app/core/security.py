from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, token_version: int = 0) -> str:
    """`ver` is what makes a token revocable.

    Without it, changing the password did nothing to whoever already held one:
    they kept full access -- broker keys, notification tokens, the ability to
    place orders -- until it expired on its own, up to a day later. Bumping
    the account's version invalidates every token minted before that moment,
    which is what makes changing the password an actual answer to "somebody
    has my account".
    """
    expire = datetime.now(UTC) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": subject, "exp": expire, "ver": token_version}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> tuple[str | None, int]:
    """The subject and the token's account version, or (None, 0) if invalid.

    A token minted before this claim existed has no `ver`; it reads as 0,
    which is where every account starts. Rejecting those instead would sign
    the owner out on the deploy that adds this, for no security gain.
    """
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None, 0
    return payload.get("sub"), int(payload.get("ver", 0))
