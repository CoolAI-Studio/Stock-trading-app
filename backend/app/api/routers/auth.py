import math

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.config import settings
from app.core import login_throttle
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.mixins import utcnow
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest, RegisterRequest, Token
from app.schemas.user import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is closed. Use scripts/create_user.py to create the account.",
        )

    existing = db.query(User).filter(User.email == payload.email).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=payload.email, hashed_password=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> Token:
    # Throttled per submitted email rather than per source IP: this is a
    # single-user dashboard, so an attacker who knows the email can lock the
    # owner out for LOGIN_LOCKOUT_MINUTES -- a nuisance the owner can wait out,
    # and far cheaper than letting the one password be guessed at line rate
    # from a rotating pool of addresses.
    throttle_key = form_data.username.strip().lower()
    locked_for = login_throttle.seconds_until_unlocked(throttle_key)
    if locked_for > 0:
        retry_after = math.ceil(locked_for)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )

    user = db.query(User).filter(User.email == form_data.username).first()
    if user is None or not verify_password(form_data.password, user.hashed_password):
        login_throttle.register_failure(
            throttle_key,
            max_attempts=settings.LOGIN_MAX_FAILED_ATTEMPTS,
            lockout_seconds=settings.LOGIN_LOCKOUT_MINUTES * 60,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    login_throttle.clear(throttle_key)
    # Recorded before the token is minted, and the previous one kept: "last
    # login" showing the login happening right now tells the owner nothing,
    # while the one before it is something they can recognise or not.
    user.previous_login_at = user.last_login_at
    user.last_login_at = utcnow()
    db.commit()
    token = create_access_token(subject=str(user.id), token_version=user.token_version)
    return Token(access_token=token)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> None:
    """Lets the owner rotate the one password without re-registering -- a new
    account would get a new user_id and an empty dashboard, since every order,
    position and strategy is scoped to the existing one."""
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    user.hashed_password = hash_password(payload.new_password)
    # Every token issued before now stops working. Without this, changing the
    # password did nothing to whoever already held one -- they kept full
    # access until it expired on its own, which makes "change your password"
    # useless as a response to a compromise.
    user.token_version += 1
    db.commit()


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_active_user)) -> User:
    return user


@router.post("/logout-everywhere", status_code=status.HTTP_204_NO_CONTENT)
def logout_everywhere(
    db: Session = Depends(get_db), user: User = Depends(get_current_active_user)
) -> None:
    """Invalidate every token, including the one that asked.

    Signing out other devices while leaving the one in your hand signed in has
    not done what it says, and the owner reaching for this is not in a mood to
    be reassured incorrectly. They log in again afterwards.
    """
    user.token_version += 1
    db.commit()
