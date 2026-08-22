"""安裝的最後一步，必須能在網頁上做完。

WHAT THIS FIXES. A person clicks Deploy, fills in every blank the setup page
asks for, presses the buttons that generate the keys -- and then reaches this,
which is what the setup page told them (SetupPage.tsx):

    第一次使用要先建立帳號 —— 詳細步驟見 DEPLOYMENT.md

DEPLOYMENT.md then told them to switch an environment variable on, create the
account with curl, and switch it back off. CLAUDE.md is explicit about what
that means for this audience:

    任何「請在你的電腦上跑這支腳本」的指示，對這個使用者等於流程到此結束。

So the install ended one step from the finish line, at the step they were
least able to improvise. The frontend never called /api/auth/register at all
-- there was no screen for it.

WHAT THE BROWSER NEEDS FROM THE BACKEND. Only one thing: whether this
deployment has an owner yet. It cannot find that out from /api/setup/status,
which answers 404 once the environment variables are filled in -- and 「設定
填完了，但還沒有人認領」 is exactly the state a fresh deploy sits in.

NOT A NEW DISCLOSURE. POST /api/auth/register already answers 403 to a
stranger once an owner exists, so 「this deployment is claimed」 is public
today. This says the same thing without asking anybody to attempt a write.
"""


def test_a_fresh_deployment_says_it_is_waiting_for_an_owner(client):
    body = client.get("/api/auth/registration-open").json()

    assert body["open"] is True


def test_and_stops_saying_so_the_moment_it_has_one(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    created = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "correct-horse-battery"},
    )
    assert created.status_code == 201, created.text

    body = client.get("/api/auth/registration-open").json()

    assert body["open"] is False


def test_it_answers_without_a_token(client):
    """The person asking has no account -- that is the question. A gate here
    would make the endpoint useless for the one moment it exists for."""
    assert client.get("/api/auth/registration-open").status_code == 200


def test_it_says_nothing_else_at_all(client, monkeypatch):
    """One boolean. Not the owner's address, not how many accounts there are,
    not which settings are unset -- this is served to anyone who asks, and
    every extra field is something a stranger learns about somebody's
    deployment."""
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "correct-horse-battery"},
    )

    response = client.get("/api/auth/registration-open")

    assert response.json() == {"open": False}
    assert "owner@example.com" not in response.text


def test_the_answer_and_the_door_agree(client, monkeypatch):
    """A flag that says 「closed」 while the door opens would be worse than no
    flag, and a flag that says 「open」 onto a 403 sends somebody in circles."""
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    assert client.get("/api/auth/registration-open").json()["open"] is True

    first = client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "correct-horse-battery"},
    )
    assert first.status_code == 201

    assert client.get("/api/auth/registration-open").json()["open"] is False
    second = client.post(
        "/api/auth/register",
        json={"email": "someone-else@example.com", "password": "another-password-1"},
    )
    assert second.status_code == 403


def test_the_flag_is_a_fact_about_the_database_not_a_setting(client, monkeypatch):
    """ALLOW_REGISTRATION cannot re-open a claimed deployment (that was the
    whole point of closing registration on a fact instead of a flag), so it
    must not be able to make this say otherwise either."""
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    client.post(
        "/api/auth/register",
        json={"email": "owner@example.com", "password": "correct-horse-battery"},
    )

    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)

    assert client.get("/api/auth/registration-open").json()["open"] is False
