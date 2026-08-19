from pydantic import BaseModel, ConfigDict, EmailStr

from app.schemas.common import UtcDatetime


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_active: bool
    timezone: str
    # Shown on the account page so an unrecognised sign-in is noticeable at
    # all. Two of them, because "last login" showing the session you are
    # sitting in tells the owner nothing -- the one before it is what they can
    # recognise or fail to.
    last_login_at: UtcDatetime | None
    previous_login_at: UtcDatetime | None


class UserUpdate(BaseModel):
    is_active: bool | None = None
