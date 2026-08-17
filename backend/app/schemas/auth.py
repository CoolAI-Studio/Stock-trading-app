from pydantic import BaseModel, EmailStr, field_validator


def _check_password(value: str) -> str:
    # bcrypt silently truncates past 72 *bytes* (not characters) -- reject
    # up front rather than let a non-ASCII passphrase truncate silently.
    # scripts/create_user.py applies the same 72-byte limit.
    if len(value.encode("utf-8")) > 72:
        raise ValueError("password must be at most 72 bytes")
    if len(value) < 8:
        raise ValueError("password must be at least 8 characters")
    return value


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_must_fit_bcrypt(cls, value: str) -> str:
        return _check_password(value)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_must_fit_bcrypt(cls, value: str) -> str:
        return _check_password(value)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str
