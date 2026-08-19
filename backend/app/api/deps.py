from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


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


def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return user
