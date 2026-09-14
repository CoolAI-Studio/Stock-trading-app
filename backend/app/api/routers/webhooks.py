import hashlib
import hmac
import json
import logging
import time
from datetime import timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.config import settings
from app.db.session import get_db
from app.enums import OrderSide, OrderSource
from app.models.mixins import utcnow
from app.models.user import User
from app.models.webhook import TradingViewWebhookLog
from app.schemas.webhook import (
    TradingViewAlert,
    TradingViewSetup,
    TradingViewWebhookLogRead,
)
from app.services import symbol_search, tradingview_url
from app.services.signals import SignalIn, create_pending_order

logger = logging.getLogger("app.webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# A TradingView alert message is a few hundred bytes. 64KB is already absurd
# for one, so anything past it is refused without being read to the end --
# the whole point of the limit is to not spend memory or a database row on
# something no legitimate caller sends.
_MAX_BODY_BYTES = 64 * 1024

# How much of an accepted payload is kept. This is an audit trail, not a
# replay log.
_MAX_LOGGED_BODY_CHARS = 8 * 1024

# Retention for the audit table. Two bounds, because they fail differently:
# the age bound keeps old alerts from lingering but does nothing about a burst
# inside the window, and the row bound is the one that actually caps disk.
# Neon's free tier is 0.5GB for the entire database, and a database with no
# space left fails *writes* -- orders, positions, the worker, all of it. An
# audit trail is not worth taking the app down for.
_LOG_RETENTION_DAYS = 30
_LOG_MAX_ROWS = 500

# Said to the owner, in the log they read, when an alert arrives at a URL they
# have since regenerated. The symptom of forgetting to update TradingView is
# 「it stopped ringing」 with nothing on screen, so this row is the only place
# that can say why.
_REGENERATED = (
    "這個網址已經重新產生過，舊的不再收件。請到「TradingView」頁複製現在的網址，"
    "換掉這則 TradingView 警報裡的 Webhook URL。"
)

# A RETIRED URL, REMEMBERED IN THE PROCESS RATHER THAN ASKED OF THE DATABASE.
#
# Its MAC is genuine, so without this every call walks all the way in: look up
# the account, write a row, prune -- about six statements. A leaked URL is the
# very reason somebody presses 「重新產生」, and whoever holds it can keep
# calling in a loop: that pins Neon awake (#95) and shoves real rows out of the
# capped log. Versions only ever go up, so 「this (account, version) is retired」
# never stops being true once learnt; the row is still written again every
# _RETIRED_URL_LOG_EVERY_SEC, because a forgotten alert should keep showing up.
_RETIRED_URL_LOG_EVERY_SEC = 3600.0
_RETIRED_URL_LOGGED: dict[tuple[int, int], float] = {}
_clock = time.monotonic


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _read_bounded_body(request: Request) -> bytes | None:
    """The request body, or None if the client sent more than the limit.

    Streamed rather than `await request.body()` so an oversized body is
    abandoned partway through instead of being buffered in full first. The
    Content-Length header is checked too, but only as a shortcut: a chunked
    request declares no length at all, so the arriving bytes are what has to
    be counted.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
        return None

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _too_large(request: Request) -> HTTPException:
    logger.warning(
        "tradingview webhook: refused a body over %d bytes from %s",
        _MAX_BODY_BYTES,
        _client_ip(request),
    )
    return HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="payload too large")


def _parse_json(raw_body: bytes) -> tuple[dict | None, str | None]:
    """The alert as a dict, or None and the reason it is not one."""
    try:
        payload = json.loads(raw_body)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return None, f"invalid JSON: {exc}"
    return payload, None


def _resolve_user(db: Session, symbol: str, strategy_name: str | None) -> User | None:
    """Whose alert this is. On a single-owner deployment: the owner. Otherwise:
    nobody, and the alert is rejected with a reason in the log.

    WHAT WAS REMOVED, AND WHY IT CANNOT COME BACK IN THIS SHAPE. This used to
    look for a Strategy whose symbol (and optionally name) matched, and
    attribute the alert to whoever owned that row:

        db.query(Strategy).filter(Strategy.symbol == symbol) ... .first()

    No user_id condition, no ORDER BY. `Strategy`'s unique key is
    `(user_id, name)`, so a second account can hold a strategy with the same
    symbol AND the same name -- and which row `.first()` returns is a fact
    about the database, not about who the alert is for.

    The attacker never needs TV_WEBHOOK_SECRET: the secret arrives on the
    OWNER'S OWN TradingView request. And the worst case was deterministic
    rather than lucky -- TradingView alerts are configured on TradingView, so
    the owner often has no matching Strategy row at all, which made the
    intruder's row the only match. Every alert would land in their ledger,
    with nothing on the owner's side saying so.

    So the guess is gone. It never carried information: with one account the
    answer is that account either way, and with two the query could only be
    right by luck.

    THE REAL FIX NOW EXISTS: every account has its own URL
    (`tradingview_personal_webhook`, #115), where the URL proves the account
    instead of anything being inferred. This function serves only the
    shared-secret door, which stays for the alerts configured before those
    URLs existed -- and for those, refusing is still the only honest answer
    when there is more than one account.
    """
    users = db.query(User).order_by(User.id).limit(2).all()
    if len(users) != 1:
        # Deliberately including the empty case: an alert for a deployment
        # with no account at all belongs to nobody.
        return None
    return users[0]


def _prune_audit_log(db: Session) -> None:
    """Enforces the two retention bounds described at _LOG_MAX_ROWS.

    Only the authenticated paths reach this, so it runs at TradingView alert
    volume -- a handful a day -- not at whatever rate a stranger can generate.
    """
    cutoff = utcnow() - timedelta(days=_LOG_RETENTION_DAYS)
    db.query(TradingViewWebhookLog).filter(TradingViewWebhookLog.received_at < cutoff).delete(
        synchronize_session=False
    )

    # Deleted by id rather than with DELETE ... LIMIT, which SQLite and
    # Postgres disagree about: "older than the Nth newest id" is plain SQL on
    # both, and ids are monotonic here.
    oldest_kept = (
        db.query(TradingViewWebhookLog.id)
        .order_by(TradingViewWebhookLog.id.desc())
        .offset(_LOG_MAX_ROWS - 1)
        .first()
    )
    if oldest_kept is not None:
        db.query(TradingViewWebhookLog).filter(TradingViewWebhookLog.id < oldest_kept[0]).delete(
            synchronize_session=False
        )
    db.commit()


def _seen_recently(db: Session, raw_body: str, user_id: int | None = None) -> bool:
    """Whether this exact body already arrived inside the replay window.

    Compared on the stored, secret-stripped body, so it is the alert's content
    that is matched rather than the credential wrapping it. A price that moved
    makes a different body and gets through, which is what keeps this from
    swallowing real signals.

    Scoped to the account when the URL has already proved which one it is:
    two accounts can legitimately send identical alert bodies, and one of them
    must not be dropped as the other's replay.
    """
    cutoff = utcnow() - timedelta(seconds=settings.TV_WEBHOOK_REPLAY_WINDOW_SEC)
    query = db.query(TradingViewWebhookLog).filter(
        TradingViewWebhookLog.raw_body == raw_body,
        TradingViewWebhookLog.signature_valid.is_(True),
        TradingViewWebhookLog.received_at >= cutoff,
        TradingViewWebhookLog.id != None,  # noqa: E711 -- exclude the unsaved row
        # A retired URL's row does not count as having been received: otherwise
        # whoever holds the leaked old URL sends a body first, and the owner's
        # identical real alert on the new URL is then dropped as its replay.
        (TradingViewWebhookLog.error.is_(None)) | (TradingViewWebhookLog.error != _REGENERATED),
    )
    if user_id is not None:
        query = query.filter(TradingViewWebhookLog.user_id == user_id)
    return query.first() is not None


def _audit_row(
    request: Request,
    user: User | None,
    payload: dict | None,
    raw_body: bytes = b"",
) -> TradingViewWebhookLog:
    """The audit row for a call that got past its credential.

    Stored re-serialized without the shared secret rather than as the bytes
    that arrived: the secret is a bearer credential, and an audit row quoting
    it back would be a second, unencrypted copy of the password guarding the
    shared door. Everything with audit value -- symbol, action, quantity, the
    alert id -- is kept.

    A body that would not parse cannot have a key removed, so the secret's
    VALUE is masked in the text instead: somebody pasting the old message,
    secret line included, into a personal URL with a typo in it must not end
    up with the shared password sitting in plain text in their log.
    """
    if payload is not None:
        audited = {key: value for key, value in payload.items() if key != "secret"}
        body = json.dumps(audited, ensure_ascii=False)
    else:
        body = raw_body.decode("utf-8", errors="replace")
        if settings.TV_WEBHOOK_SECRET:
            body = body.replace(settings.TV_WEBHOOK_SECRET, "***")
    return TradingViewWebhookLog(
        raw_body=body[:_MAX_LOGGED_BODY_CHARS],
        remote_ip=_client_ip(request),
        signature_valid=True,
        user_id=user.id if user is not None else None,
    )


def _persist_audit(db: Session, log: TradingViewWebhookLog) -> None:
    db.add(log)
    db.commit()
    _prune_audit_log(db)


def _reject_with_log(db: Session, log: TradingViewWebhookLog, error: str) -> JSONResponse:
    log.error = error
    _persist_audit(db, log)
    return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": False, "error": error})


def _accept(db: Session, request: Request, payload: dict, user: User | None):
    """From an authenticated payload to a pending signal. Both doors end here.

    `user` is the account a personal URL has already proved. None means the
    shared-secret door, where the owner still has to be inferred
    (`_resolve_user`).
    """
    # Authenticated from here, so audit rows are worth writing: their volume is
    # bounded by whoever holds the credential, and _prune_audit_log caps them
    # even if that assumption ever stops holding.
    log = _audit_row(request, user, payload)

    try:
        alert = TradingViewAlert.model_validate(payload)
    except ValidationError as exc:
        log.parsed_ok = False
        return _reject_with_log(db, log, f"invalid payload: {exc}")

    log.parsed_ok = True
    log.missing_id = not alert.id

    # An alert with an `id` is exactly idempotent -- create_pending_order's
    # unique key sees to that. One without has no such promise, so the same
    # body arriving twice in a short window is treated as a replay rather than
    # as two decisions. That covers the alerts already configured out there
    # without anybody having to change them.
    #
    # Honest about its limit: a patient attacker replaying an hour apart is
    # not stopped by this, which is why the setup panel pushes `id`.
    known_user_id = user.id if user is not None else None
    if not alert.id and _seen_recently(db, log.raw_body, known_user_id):
        return _reject_with_log(
            db, log, "重複的警報內容（短時間內收到一模一樣的訊息），已當成重放略過"
        )

    # TradingView's {{ticker}} sends 「2330」, never 「2330.TW」 -- and Yahoo
    # answers a bare 2330 with an unrelated Japanese company, so this used to
    # create an order that priced the wrong stock with complete confidence.
    # There is nobody present to pick, so it is resolved from our own registry
    # (a lookup with a unique answer, unlike Yahoo's cross-market search) and
    # the adjustment is recorded. Anything without a unique answer is refused.
    symbol, adjustment = symbol_search.resolve_incoming(alert.symbol, alert.exchange)
    if symbol is None:
        if adjustment and adjustment.startswith("__unsupported__"):
            market = adjustment.removeprefix("__unsupported__")
            return _reject_with_log(
                db,
                log,
                f"這則警報來自 {market} 市場，這個 app 只支援台股、美股與 Binance。"
                f"沒有建立訂單 —— 「{alert.symbol}」這個代號在台股也存在，"
                "硬對應過去會盯到完全不同的一家公司。",
            )
        return _reject_with_log(
            db,
            log,
            f"看不懂這個代號「{alert.symbol}」。台股請送 2330.TW 這種格式"
            "（或四碼代號，會自動對應），美股直接送代號即可。",
        )
    log.note = adjustment

    if user is None:
        user = _resolve_user(db, symbol, alert.strategy)
        if user is None:
            return _reject_with_log(db, log, "no user configured to receive this alert")

    log.user_id = user.id

    # THE ALERT'S IDENTITY, NOT ITS `id` ALONE. The template's id is {{timenow}},
    # which TradingView fills to the second -- and two stocks' closing alerts
    # firing in the same second is ordinary. Keyed on the id alone, the second
    # one came back 「duplicate idempotency_key」: no signal, no notification,
    # and a log row pointing at the first one's order. A redelivery by
    # TradingView is the same body, so it still collides with itself. Done here
    # rather than in the template so the alerts already configured are covered
    # without anybody editing them; hashed so an id of any length fits the
    # column on Postgres.
    idempotency_key = None
    if alert.id:
        identity = "\n".join((str(user.id), alert.id, symbol, alert.action, alert.strategy or ""))
        idempotency_key = "tv:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()

    result = create_pending_order(
        db,
        user,
        SignalIn(
            symbol=symbol,
            side=OrderSide.BUY if alert.action == "buy" else OrderSide.SELL,
            source=OrderSource.TRADINGVIEW,
            quantity=alert.quantity or Decimal(1),
            signal_price=alert.price,
            idempotency_key=idempotency_key,
            raw_payload={key: value for key, value in payload.items() if key != "secret"},
        ),
    )

    if result.order is not None:
        log.order_id = result.order.id
    _persist_audit(db, log)

    return {"ok": True, "created": result.created, "reason": result.reason}


@router.post("/tradingview", status_code=status.HTTP_202_ACCEPTED)
async def tradingview_webhook(request: Request, db: Session = Depends(get_db)):
    """Public endpoint, secured by a shared secret carried in the JSON body
    (not a header: TradingView alert webhooks can't send custom headers,
    and the body often arrives as text/plain).

    THE SHARED-SECRET DOOR, KEPT FOR THE ALERTS ALREADY OUT THERE. New setups
    are handed a personal URL instead (`tradingview_personal_webhook`, #115).
    This one stays because an update must never silence an alert somebody
    configured months ago and has forgotten about (#50).

    Nothing is written to the database until that secret checks out. The path
    is public and guessable, nothing else authenticates the caller, and no
    cleanup existed for the audit table -- so an audit row written before the
    check meant anyone who found the URL could append storage in a loop until
    Neon's 0.5GB free tier was full, and a database with no space left fails
    every write the app makes. Rejected requests are reported to the
    application log instead, which the hosting platform already rotates.

    Returns 202 for a genuinely accepted signal. TradingView retries any
    non-2xx response, so failures that would never succeed on retry
    (malformed JSON, an invalid payload shape, no user to attribute it to)
    return 200 with the error logged instead of a 4xx/5xx."""
    raw_body = await _read_bounded_body(request)
    if raw_body is None:
        raise _too_large(request)

    payload, problem = _parse_json(raw_body)
    if payload is None:
        # The secret travels inside the JSON, so a body that will not parse is
        # a body that cannot be authenticated. No row.
        logger.warning("tradingview webhook: unparseable body from %s", _client_ip(request))
        return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": False, "error": problem})

    secret = str(payload.get("secret", ""))
    if not hmac.compare_digest(secret, settings.TV_WEBHOOK_SECRET):
        logger.warning("tradingview webhook: invalid secret from %s", _client_ip(request))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret")

    return _accept(db, request, payload, user=None)


@router.post("/tradingview/{token}", status_code=status.HTTP_202_ACCEPTED)
async def tradingview_personal_webhook(token: str, request: Request, db: Session = Depends(get_db)):
    """One account's own TradingView URL (#115). The URL is the credential.

    THE ORDER BELOW IS THE POINT. The token is checked before anything touches
    the database: this path is public, and a URL whose validity could only be
    learnt by querying would let a stranger keep Neon awake with a loop.
    `tradingview_url.read` needs nothing but the string and the deployment's
    secret, so a guessed URL costs exactly nothing.

    Past that, it is the same pipeline as the shared-secret door -- except that
    the account is proven rather than inferred, which is also why two accounts
    on one deployment finally work.
    """
    claim = tradingview_url.read(token, settings.TV_WEBHOOK_SECRET)
    if claim is None:
        logger.warning(
            "tradingview webhook: refused an unrecognised personal URL from %s",
            _client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown webhook URL")

    raw_body = await _read_bounded_body(request)
    if raw_body is None:
        raise _too_large(request)

    user_id, version = claim
    retired_at = _RETIRED_URL_LOGGED.get((user_id, version))
    if retired_at is not None and _clock() - retired_at < _RETIRED_URL_LOG_EVERY_SEC:
        # Already known to be retired and already in the log this hour: answer
        # from memory. Not one statement -- see _RETIRED_URL_LOGGED.
        return JSONResponse(
            status_code=status.HTTP_200_OK, content={"ok": False, "error": _REGENERATED}
        )

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        # The MAC was genuine, so this URL was handed out once -- to an account
        # that is gone or switched off. There is nobody to show a row to.
        logger.warning(
            "tradingview webhook: personal URL for a missing or inactive account from %s",
            _client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unknown webhook URL")

    payload, problem = _parse_json(raw_body)

    if version != user.webhook_url_version:
        # 200, because TradingView retries any non-2xx and this will never
        # succeed. And a row, because this is the owner's own call to a URL they
        # retired: the log is the only place that can tell them to update it.
        if version < user.webhook_url_version:
            # Only an older version is retired for good. A newer one than the
            # account holds (a database restored to an earlier state) could
            # still become current, so it is not remembered.
            _RETIRED_URL_LOGGED[(user_id, version)] = _clock()
        log = _audit_row(request, user, payload, raw_body)
        log.parsed_ok = payload is not None
        return _reject_with_log(db, log, _REGENERATED)

    if payload is None:
        # Unlike the shared door, the caller is already known here, so the
        # malformed body is worth showing them -- it is exactly the row somebody
        # fixing their alert message is looking for.
        log = _audit_row(request, user, None, raw_body)
        log.parsed_ok = False
        return _reject_with_log(db, log, problem or "invalid JSON")

    return _accept(db, request, payload, user=user)


@router.get("/tradingview/logs", response_model=list[TradingViewWebhookLogRead])
def list_webhook_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> list[TradingViewWebhookLog]:
    """What TradingView actually sent.

    These rows have been written on every authenticated call since the webhook
    existed, and pruned on a schedule -- created and then deleted without
    anybody ever having been able to read them. When an alert did not become
    an order, the owner had no way to tell whether it arrived at all, whether
    the secret was wrong, whether the JSON was malformed, or whether a risk
    gate refused it.

    THE DAY HAS COME. This was deliberately unfiltered, on the reasoning that
    a deployment has one owner so there is nobody else's traffic to leak, and
    that the rows worth reading -- a call that failed the secret, or whose JSON
    would not parse -- have no user attached and would be filtered away. The
    first half stopped being safe to assume the moment a second account could
    exist at all.

    So: your own rows, plus the ones that belong to nobody. An unattributed row
    is a deployment-level diagnostic -- either the secret was wrong, in which
    case the sender was not a user of this app, or the payload never parsed --
    and it is still exactly the row somebody debugging is looking for.
    """
    return (
        db.query(TradingViewWebhookLog)
        .filter(
            (TradingViewWebhookLog.user_id == user.id) | (TradingViewWebhookLog.user_id.is_(None))
        )
        .order_by(TradingViewWebhookLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def _setup_for(user: User, response: Response) -> TradingViewSetup:
    # The URL is a credential now. A response that a browser or a proxy keeps
    # is a copy of it that nobody manages.
    response.headers["Cache-Control"] = "no-store"
    return TradingViewSetup(
        url=tradingview_url.personal_url(
            settings.public_base_url,
            user.id,
            user.webhook_url_version,
            settings.TV_WEBHOOK_SECRET,
        ),
        example_message=(
            "{\n"
            '  "symbol": "{{ticker}}",\n'
            '  "exchange": "{{exchange}}",\n'
            '  "action": "buy",\n'
            '  "quantity": 1000,\n'
            '  "price": {{close}},\n'
            '  "id": "{{timenow}}"\n'
            "}"
        ),
        notes=[
            "這條網址是你這個帳號專屬的，而且它本身就是密碼：拿到它的人可以替你送訊號。"
            "不要貼在公開的地方；萬一外洩了，按「重新產生」，舊的會立刻失效。",
            "網址貼進 TradingView 警報的 Webhook URL，上面那段貼進「訊息」欄。"
            "訊息裡不需要任何密碼。",
            "id 一定要填。同一則警報重送幾次都只建立一次訊號——沒有它，任何人只要重送一次"
            "抄到的訊息就能重複觸發。用 {{timenow}} 最省事，同一秒響的不同股票會各算一則。",
            # The old wording printed the {{ticker}} template and then said TW
            # must look like 2330.TW -- an instruction that contradicts itself,
            # because that placeholder never includes the exchange. It now says
            # what actually happens.
            "symbol 用 {{ticker}} 就好。台股圖表送出來的是四碼代號（例如 2330），"
            "系統會自動對應到 2330.TW（上櫃是 .TWO），對應結果會寫在下面的收件紀錄裡；"
            "美股送出來的本來就是正確代號。",
            "找不到對應的代號（打錯、或不是台美股）會被擋下來並記在收件紀錄，"
            "不會建立一筆指向錯誤公司的訊號。",
            # {{exchange}} only arrives if it is in the message, so every alert
            # made before this line existed keeps using the weaker path. Saying
            # so is the difference between a fix and a fix nobody applied.
            "exchange 那一行請務必留著：日股和港股的代號也是四位數，"
            "沒有它就分不出 4502 是武田藥品還是台灣的健信。",
            # The alerts configured before personal URLs existed must keep
            # working (#50), and their owner should not be left wondering
            # whether they have to redo everything.
            "以前照舊說明設定、訊息裡有 secret 那一行的警報照樣有效（它們打的是另一條共用網址）。"
            "想換成這條網址的話，換掉網址、刪掉 secret 那一行就好。",
            "重新產生之後，要回 TradingView 把每一則警報的網址換掉；還在打舊網址的，"
            "會記在下面的收件紀錄裡並說明原因。",
            "送出後可以在下面的收件紀錄看到它有沒有進來、以及被擋在哪一關。",
        ],
    )


@router.get("/tradingview/setup", response_model=TradingViewSetup)
def tradingview_setup(
    response: Response, user: User = Depends(get_current_active_user)
) -> TradingViewSetup:
    """What to paste into TradingView.

    Nothing told the owner the URL, the field names, or that the message needs
    an `id` -- which is the only thing standing between this endpoint and a
    replay of a captured alert. Served rather than documented, because a URL
    in a docs page is a URL nobody finds.

    It used to print `<你的 TV_WEBHOOK_SECRET>` where the password goes -- for a
    sound reason, since the real shared secret would land in every browser
    cache and every screenshot of this page -- with the result that the owner
    had to dig the value out of the hosting platform's settings: exactly what
    this app promises never to ask of him (#115). The URL is personal now and
    IS the credential, so there is nothing left to fetch, and a leaked one is
    fixed by regenerating rather than by redeploying.
    """
    return _setup_for(user, response)


@router.post("/tradingview/setup/rotate", response_model=TradingViewSetup)
def rotate_tradingview_url(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
) -> TradingViewSetup:
    """A new personal URL; the old one stops being accepted at once.

    For a URL that leaked. Every TradingView alert still pointed at the old one
    lands in the webhook log with the reason, instead of silently no longer
    ringing.
    """
    user.webhook_url_version += 1
    db.commit()
    return _setup_for(user, response)
