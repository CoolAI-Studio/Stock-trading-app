"""The only endpoints a deployment serves before it has been configured.

Unauthenticated, because there is nothing to authenticate against: a login
needs a JWT_SECRET, and a missing JWT_SECRET is one of the things this page
exists to fix. What keeps that safe is narrowness, not a password --

  it reports WHICH settings are missing, never what any of them contains;
  it generates fresh random values, which an attacker could generate for
    themselves and which are worth nothing until a human pastes them into
    Render;
  it writes nothing, anywhere. This process cannot persist its own
    configuration and does not try.

Once the deployment is configured these routes answer 404. Not 403: there is
nothing here to be forbidden from, and a route that exists but refuses invites
somebody to keep knocking.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.config import settings
from app.services import setup_state

router = APIRouter(prefix="/setup", tags=["setup"])


class MissingSettingRead(BaseModel):
    """One blank, and what the person filling it in needs to know.

    `generator` is the difference between a two-minute setup and an install of
    Python: non-null means this app can produce the value itself. None means
    only the deployer can supply it -- DATABASE_URL points at somebody else's
    service, and offering a button for it would be a lie.
    """

    name: str
    why: str
    how: str
    generator: str | None


class SetupStatus(BaseModel):
    missing: list[MissingSettingRead]
    # Where to paste the answers. Named rather than assumed, because the whole
    # audience for this page is somebody who has just met Render for the first
    # time and does not know that env vars live under Settings -> Environment.
    where: str


class GenerateRequest(BaseModel):
    kind: str


def _guard() -> list:
    """The list, or 404 once there is nothing left to configure."""
    missing = setup_state.missing_settings(settings)
    if not missing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="這個部署已經設定完成。")
    return missing


@router.get("/status", response_model=SetupStatus)
def setup_status() -> SetupStatus:
    return SetupStatus(
        missing=[MissingSettingRead(**vars(item)) for item in _guard()],
        where=(
            "Render 後台 → 你的服務 → 左邊選單 Environment → 找到同名的欄位貼上去 → "
            "存檔之後 Render 會自動重新部署，大約一兩分鐘。"
        ),
    )


@router.post("/generate")
def generate(payload: GenerateRequest) -> dict[str, str]:
    """A fresh value of the right shape, for the deployer to copy.

    Produced with the same library the boot check validates against, so a
    generated value cannot be one this app then refuses to start on.
    """
    _guard()
    try:
        return setup_state.generate(payload.kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
