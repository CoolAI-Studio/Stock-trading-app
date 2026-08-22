"""稽查員自己有沒有在稽查。

WHY THIS FILE EXISTS. scripts/audit.py exists because the isolation tests are
hand-written lists and a list cannot miss what was never put on it. That
argument applies to the auditor too, and harder: an auditor that reports
「沒有發現」 because it is broken looks exactly like an auditor that reports
「沒有發現」 because there is nothing wrong. It is the more dangerous of the
two, because somebody acts on it.

MEASURED, NOT HYPOTHETICAL. The first working version of that script sent the
intruder's sweep straight through POST /api/auth/logout-everywhere -- one of
the operations it enumerates, sorted near the front -- which bumped the
token_version and turned the remaining 73 requests into anonymous ones. Every
door was shut, nothing leaked, the report said 「沒有發現」. The only reason
anybody noticed was a status-code histogram printed next to it.

So each case below plants something and demands the auditor find it.
"""

import importlib.util
from decimal import Decimal
from pathlib import Path

import pytest

AUDIT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "audit.py"


def _load_audit():
    """scripts/ is not a package; load the file the way a script is run."""
    spec = importlib.util.spec_from_file_location("audit_under_test", AUDIT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit_module = _load_audit()


@pytest.fixture
def auditor():
    a = audit_module.Audit()
    a.canaries = {
        "JWT_SECRET": "CANARY-JWT-testtest",
        "OWNER_DATA": "CANARY-OWNERDATA-testtest",
        "OWNER_DATA_SHORT": "CNRYtesttest",
    }
    return a


class _FakeApp:
    def __init__(self, paths: dict) -> None:
        self._paths = paths

    def openapi(self) -> dict:
        return {"paths": self._paths}


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}


class _FakeClient:
    """Answers every request the same way, which is all these cases need."""

    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def request(self, verb: str, url: str, **_) -> _FakeResponse:
        self.calls.append((verb, url))
        return self.response


# --- 一、清冊 ------------------------------------------------------------------------


def test_a_new_public_endpoint_is_a_finding(auditor):
    """The actual thing this method is for: somebody adds an endpoint, forgets
    the dependency, and nothing in the test suite mentions it because nothing
    knows it exists."""
    app = _FakeApp({"/api/brand-new": {"get": {}}})

    auditor.census(app)

    assert any("brand-new" in f.what for f in auditor.findings)


def test_an_endpoint_that_is_public_on_purpose_is_not(auditor):
    """Otherwise the answer to a red build is to delete the check."""
    app = _FakeApp({"/healthz": {"get": {}}})

    auditor.census(app)

    assert auditor.findings == []


def test_an_endpoint_that_asks_for_a_token_is_not_examined_further(auditor):
    app = _FakeApp({"/api/watchlist": {"get": {"security": [{"OAuth2PasswordBearer": []}]}}})

    auditor.census(app)

    assert auditor.findings == []


def test_a_permission_for_something_that_no_longer_exists_is_pointed_out(auditor):
    """Stale permission reads as coverage: it says 「this one is fine」 about
    something nobody can reach any more."""
    auditor.census(_FakeApp({"/healthz": {"get": {}}}))

    assert any("已經不存在" in note for note in auditor.notes)


# --- 二、三：誘餌 --------------------------------------------------------------------


def test_a_canary_in_a_response_is_a_finding(auditor):
    """The whole premise. If a secret or another account's row comes back in a
    body, this must be the thing that says so."""
    leaky = _FakeClient(_FakeResponse(200, '{"secret": "CANARY-JWT-testtest"}'))

    auditor.sweep(leaky, "另一個帳號", "三", None, [("GET", "/api/anything", {})], {})

    assert any("JWT_SECRET" in f.what for f in auditor.findings)


def test_a_clean_response_is_not(auditor):
    clean = _FakeClient(_FakeResponse(200, '{"items": []}'))

    auditor.sweep(clean, "另一個帳號", "三", None, [("GET", "/api/anything", {})], {})

    assert auditor.findings == []


def test_writing_to_someone_elses_resource_is_a_finding_even_with_an_empty_body(auditor):
    """A successful DELETE answers 204 with nothing in it. No canary search can
    see that, and it is worse than a read."""
    accepted = _FakeClient(_FakeResponse(204, ""))

    auditor.sweep(
        accepted, "另一個帳號", "三", None, [("DELETE", "/api/strategies/{id}", {})], {"s": [7]}
    )

    assert any("寫得進去" in f.detail for f in auditor.findings)


def test_a_sweep_that_was_locked_out_reports_itself_as_broken(auditor, monkeypatch):
    """THE REGRESSION. A logged-in sweep that is refused everywhere finds
    nothing -- and 「found nothing」 is what it prints. Measured on the first
    working version of the script: 73 of 82 operations answered 401 because the
    sweep had called logout-everywhere on its own token.
    """
    monkeypatch.setattr(audit_module.Audit, "headers_for", lambda self, uid: {})
    shut = _FakeClient(_FakeResponse(401, '{"detail": "Not authenticated"}'))
    operations = [("GET", f"/api/thing-{i}", {}) for i in range(8)]

    auditor.sweep(shut, "另一個帳號", "三", 2, operations, {})

    assert any("等於沒做" in f.what for f in auditor.findings)


def test_an_anonymous_sweep_being_refused_everywhere_is_correct_not_broken(auditor):
    """Every door shut is the RIGHT answer when nobody is logged in. The same
    reading must not be a finding here, or the auditor cries on a healthy app.
    """
    shut = _FakeClient(_FakeResponse(401, '{"detail": "Not authenticated"}'))
    operations = [("GET", f"/api/thing-{i}", {}) for i in range(8)]

    auditor.sweep(shut, "沒有登入的人", "二", None, operations, {})

    assert auditor.findings == []


def test_the_owners_own_ids_go_into_the_url(auditor):
    """Asking for /api/strategies/1 when the owner's row is 44 audits nothing."""
    filled = auditor._fill_path("/api/strategies/{strategy_id}", {"strategies": [44]})

    assert filled == "/api/strategies/44"


# --- 四、覆蓋 ------------------------------------------------------------------------


def test_a_decimal_column_gets_a_number(auditor):
    """MEASURED: Numeric is not a float subclass, so price and quantity used to
    receive the text canary, the insert raised, and orders and alerts were
    never audited at all -- while the report still said 「沒有發現」."""
    from sqlalchemy import Column, Numeric

    assert auditor._column_value(Column("price", Numeric(10, 2))) == Decimal(1)


def test_a_short_text_column_gets_a_canary_that_still_fits(auditor):
    """A canary truncated to fit is a canary that can never be found again."""
    from sqlalchemy import Column, String

    value = auditor._column_value(Column("symbol", String(20)))

    assert value == auditor.canaries["OWNER_DATA_SHORT"]
    assert len(value) <= 20


def test_a_roomy_text_column_gets_the_long_one(auditor):
    from sqlalchemy import Column, String

    assert auditor._column_value(Column("name", String(200))) == auditor.canaries["OWNER_DATA"]


# --- 六、倉庫 ------------------------------------------------------------------------


def test_a_real_looking_connection_string_matches():
    pattern = next(p for p, why in audit_module.REPO_FORBIDDEN if "postgres" in p)
    import re

    assert re.search(pattern, "postgresql://trader:hunter2secret@ep-cold.neon.tech/db")


def test_the_documentation_example_is_recognised_as_one():
    """DEPLOYMENT.md shows people what a connection string looks like. An
    auditor that fails the build over that is one nobody reads twice."""
    found = "postgresql://user:password@"

    assert any(word in found.lower() for word in audit_module.PLACEHOLDER_WORDS)


def test_a_real_secret_is_not_excused_by_the_placeholder_list():
    found = "postgresql://trader:hunter2secret@"

    assert not any(word in found.lower() for word in audit_module.PLACEHOLDER_WORDS)
