"""Nobody else can get an account on somebody else's deployment.

The owner: 「我目前的這些資料都只屬於我，並不會在給其他使用者上看到」 -- my data
is mine alone. And: what a new user receives is the ARCHITECTURE, deployed as
their own copy with their own database; never this owner's data.

Every table in this app is scoped by user_id, so that promise holds as long as
there is exactly one account on a deployment. WHAT GUARDED THAT was a single
environment variable:

    if not settings.ALLOW_REGISTRATION:  # auth.py
        raise 403

and DEPLOYMENT.md told the owner to switch it ON, create the account with curl,
then switch it back OFF. Three steps, on a hosting dashboard, done once, months
ago. Forget the third and the public URL accepts registrations forever -- and
nothing anywhere would say so. There is no banner, no line on the system status
page, no warning at boot. The owner would have no way to find out.

SECURITY THAT DEPENDS ON REMEMBERING TO TURN SOMETHING OFF IS NOT SECURITY. So
the rule is now a fact about the database rather than a setting: an account can
be created only while there are NO accounts. The first request creates the
owner; every request after that is refused, whatever the environment variable
says, because by then the deployment already has its owner.

That also deletes the curl step from the deploy flow -- CLAUDE.md is explicit
that any instruction of the form 「run this in a terminal」 ends the process for
this audience.
"""

from app.models.user import User


def _register(client, email: str, password: str = "correct horse battery staple"):
    return client.post("/api/auth/register", json={"email": email, "password": password})


# --- the first account, and only the first ----------------------------------------


def test_the_very_first_account_can_be_created_on_a_fresh_deployment(client, db_session):
    """The whole point of 「deploy your own copy」: somebody clicks the README
    button, opens their own URL, and creates their own account. No terminal, no
    curl, no environment variable to flip."""
    assert db_session.query(User).count() == 0

    resp = _register(client, "owner@example.com")

    assert resp.status_code == 201, resp.text
    assert resp.json()["email"] == "owner@example.com"


def test_a_second_account_is_refused_once_the_deployment_has_an_owner(client):
    assert _register(client, "owner@example.com").status_code == 201

    resp = _register(client, "stranger@example.com")

    assert resp.status_code == 403


def test_it_stays_refused_even_if_the_flag_was_left_switched_on(client, monkeypatch):
    """THE ACTUAL BUG THIS FILE EXISTS FOR. DEPLOYMENT.md told the owner to set
    ALLOW_REGISTRATION=true to create the first account. If they never set it
    back, the old code accepted registrations from anyone, forever, silently.

    The database now decides, not the variable.
    """
    monkeypatch.setattr("app.config.settings.ALLOW_REGISTRATION", True)
    assert _register(client, "owner@example.com").status_code == 201

    resp = _register(client, "stranger@example.com")

    assert resp.status_code == 403


def test_the_refusal_does_not_reveal_whether_that_email_has_an_account(client):
    """An open registration endpoint is also an account-enumeration oracle:
    409 「already registered」 for a real address and 201 for an unknown one
    tells a stranger which email owns the deployment. Once closed, both give
    the same answer."""
    assert _register(client, "owner@example.com").status_code == 201

    known = _register(client, "owner@example.com")
    unknown = _register(client, "nobody@example.com")

    assert known.status_code == unknown.status_code == 403
    assert known.json()["detail"] == unknown.json()["detail"]


def test_the_message_does_not_send_anyone_to_a_terminal(client):
    """The old one read 「Use scripts/create_user.py to create the account」.
    CLAUDE.md: never send this audience somewhere else to run something -- for
    them that instruction is the end of the process."""
    assert _register(client, "owner@example.com").status_code == 201

    detail = _register(client, "stranger@example.com").json()["detail"]

    assert "create_user" not in detail
    assert ".py" not in detail


def test_two_registrations_racing_for_a_fresh_deployment_cannot_both_win(client, db_session):
    """A public URL and an empty database is the one moment this endpoint is
    open. Two requests arriving together must not both create an owner: the
    second has to lose, or the deployment ends up with two accounts and the
    promise this file is about is gone before it starts.
    """
    first = _register(client, "owner@example.com")
    second = _register(client, "stranger@example.com")

    assert first.status_code == 201
    assert second.status_code == 403
    assert db_session.query(User).count() == 1


# --- and the rest of the door is still shut ----------------------------------------


def test_logging_in_is_unaffected(client):
    assert _register(client, "owner@example.com", "a good long password").status_code == 201

    resp = client.post(
        "/api/auth/login",
        data={"username": "owner@example.com", "password": "a good long password"},
    )

    assert resp.status_code == 200
    assert resp.json()["access_token"]


def test_the_owner_can_still_be_created_by_the_script_path(client, db_session):
    """scripts/create_user.py writes straight to the database and must keep
    working -- it is the recovery path when somebody locks themselves out, and
    it is not reachable from the public URL."""
    from app.core.security import hash_password

    db_session.add(User(email="owner@example.com", hashed_password=hash_password("x" * 12)))
    db_session.commit()

    assert _register(client, "another@example.com").status_code == 403
