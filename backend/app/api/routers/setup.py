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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.api.deps import optional_current_user
from app.config import settings
from app.models.user import User
from app.services import hosting, setup_state

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupOptionRead(BaseModel):
    """一個可以選的做法。

    這一格上的選擇，雲端使用者只有在這裡做得到：資料庫還是容器裡的檔案時整個
    app 是鎖住的，他連帳號都還沒有，走不到登入之後的設定引導。
    """

    # 從 services 那邊的 dataclass 直接讀，不用先轉成 dict：那一層才是這份清單
    # 的事實來源，中間多一次手抄就是多一個會漂的地方。
    model_config = ConfigDict(from_attributes=True)

    kind: str
    label: str
    detail: str
    url: str | None


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
    # False means the app boots and works, but something the owner expects to
    # work will not. Shown apart from the blocking ones, because 「it will not
    # start」 and 「TradingView will send to the wrong address」 are not the same
    # urgency and a page that mixes them teaches people to skim.
    model_config = ConfigDict(from_attributes=True)

    blocking: bool
    # 可以選的做法，空的代表這一格沒有「選哪一種」的問題。
    options: list[SetupOptionRead] = []
    # 這一格還要一起貼哪幾個環境變數。畫面要把它們跟 name 一起顯示——只照標題走
    # 的人會漏掉另一半，而推播少一半就是每一則都失敗。
    also: list[str] = []
    # Which step of the deploy flow this belongs to. Seven parallel blanks is
    # what render.yaml already gave them; the order is the part that was
    # missing, and three of these cannot even be known until the step before
    # them has happened.
    step: int


class SetupStatus(BaseModel):
    missing: list[MissingSettingRead]
    # Where to paste the answers. Named rather than left to the reader,
    # because the audience for this page has just met their hosting platform
    # for the first time and does not know that environment variables live
    # behind a menu -- and named for the platform they are ACTUALLY on, which
    # services.hosting works out from the environment.
    where: str


class GenerateRequest(BaseModel):
    kind: str


def _guard(user: User | None = None) -> list:
    """The list, or 404 once a stranger has no business being here.

    GATED ON THE BLOCKING LIST, not on everything missing. These endpoints have
    no authentication -- deliberately, because during setup nobody has an
    account yet -- so the window in which they answer has to be exactly the
    window in which they are the only way in.

    Gating on the full list kept them open on any deployment with an optional
    value unset, which is most of them: the push keys are blank by default, and
    so are CORS_ORIGINS and PUBLIC_BASE_URL. That left an unauthenticated
    endpoint telling any passer-by which settings this deployment never
    configured.

    A LOGGED-IN OWNER still gets the list afterwards, and that is not a
    loophole -- it is the other half of the fix. Somebody who skipped push
    notifications during setup has to be able to turn them on later, and this
    generator is the only thing that produces a pair the app will boot on.
    """
    if setup_state.blocking_settings(settings) or user is not None:
        missing = setup_state.missing_settings(settings)
        if missing:
            return missing
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="這個部署已經設定完成。")


@router.get("/status", response_model=SetupStatus)
def setup_status(user: User | None = Depends(optional_current_user)) -> SetupStatus:
    return SetupStatus(
        missing=[MissingSettingRead.model_validate(item) for item in _guard(user)],
        # Asked, not assumed. This used to be Render's menu path for
        # everybody, which is a wrong instruction for anybody who deployed
        # somewhere else -- and being wrong here is worse than being vague,
        # because they will go looking for the page it names.
        where=hosting.detect().env_where,
    )


@router.post("/generate")
def generate(
    payload: GenerateRequest,
    user: User | None = Depends(optional_current_user),
) -> dict[str, str]:
    """A fresh value of the right shape, for the deployer to copy.

    Produced with the same library the boot check validates against, so a
    generated value cannot be one this app then refuses to start on.
    """
    _guard(user)
    try:
        return setup_state.generate(payload.kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
