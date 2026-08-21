"""The check that runs when nobody is looking.

Every way this app can fail quietly is already visible on /healthz -- it runs
a real query, checks the worker's heartbeat, and returns 503 when any of them
is bad. Its docstring even says "UptimeRobot hits it every 5 minutes". Nothing
ever hit it. So the one failure the product cannot survive -- the worker stops
and the alerts stop with it -- was detectable only by a human opening the page,
which is precisely what somebody who set an alert is not doing.

A dying process cannot report its own death, so the check has to run somewhere
else. It runs as a scheduled GitHub Actions job (the repo is public, so the
minutes are free), and a failed scheduled workflow emails the owner. This
module is the part with judgement in it, kept out of the YAML so it can be
tested at all.

THE RETRY IS NOT POLITENESS. Render's free tier spins the process down when
idle and a cold start takes the better part of a minute, during which the
service really is unreachable and really is fine. A watchdog that mails on
that trains the owner to ignore it, and an ignored alarm is worse than none --
it costs the same attention and buys nothing.
"""

import json

import pytest

from scripts.watchdog import read_verdict, run_check


def _body(**checks) -> str:
    """A /healthz body. Spelled out from a dict rather than pasted as a long
    literal so the shape being asserted against stays readable."""
    failing = any(c.get("status") == "fail" for c in checks.values())
    return json.dumps({"status": "fail" if failing else "ok", "checks": checks})


HEALTHY = _body(
    database={"status": "ok"},
    worker={"status": "ok", "last_loop_age_sec": 4.1},
    market_data={"status": "ok", "last_poll_age_sec": 3.9},
)

WORKER_DEAD = _body(
    database={"status": "ok"},
    worker={"status": "fail"},
    market_data={"status": "fail"},
)

STARTING = _body(
    database={"status": "ok"},
    worker={"status": "starting"},
    market_data={"status": "starting"},
)


# --- reading the answer -----------------------------------------------------


def test_a_healthy_service_reports_nothing():
    assert read_verdict(200, HEALTHY) == []


def test_a_dead_worker_is_named_not_just_counted():
    """The email has to say what to go and look at. "health check failed" sends
    the owner to the dashboard to work it out from scratch."""
    problems = read_verdict(503, WORKER_DEAD)

    assert problems
    joined = "\n".join(problems)
    assert "worker" in joined
    assert "行情" in joined


def test_a_healthy_database_is_not_listed_among_the_problems():
    joined = "\n".join(read_verdict(503, WORKER_DEAD))
    assert "資料庫" not in joined


def test_a_worker_that_has_only_just_started_is_not_an_outage():
    """The grace window after a cold start is a start, not a failure -- and the
    endpoint already draws that line, so this must not draw a second one."""
    assert read_verdict(200, STARTING) == []


def test_no_response_at_all_is_the_loudest_case():
    """Nothing answered. That covers the worst outcome there is -- the whole
    deployment gone -- so it can never be silent."""
    problems = read_verdict(None, None)

    assert problems
    assert "連不上" in problems[0]


def test_an_unexpected_status_code_says_which_one():
    problems = read_verdict(502, "<html>Bad Gateway</html>")

    assert any("502" in p for p in problems)


def test_a_200_that_is_not_the_health_json_is_still_a_problem():
    """A proxy error page, a parked domain, somebody else's app on that
    hostname: all of them answer 200 with something that is not this service,
    and treating "it responded" as "it is healthy" would make the watchdog
    report green for a deployment that no longer exists."""
    problems = read_verdict(200, "<html>welcome to nginx</html>")

    assert problems
    assert any("看不懂" in p or "不是" in p for p in problems)


def test_json_that_is_missing_the_checks_block_is_a_problem_too():
    problems = read_verdict(200, '{"hello": "world"}')
    assert problems


# --- the retry --------------------------------------------------------------


def test_one_bad_answer_followed_by_a_good_one_is_not_an_outage():
    """The cold-start case, which is the common one on a free tier and is not
    a failure. Mailing on it is how a watchdog gets muted."""
    answers = [(None, None), (200, HEALTHY)]

    problems = run_check(lambda: answers.pop(0), attempts=2, wait=lambda _: None)

    assert problems == []


def test_two_bad_answers_in_a_row_is_an_outage():
    """A cold start finishes in well under a minute; still down on the second
    look is the real thing."""
    problems = run_check(lambda: (None, None), attempts=2, wait=lambda _: None)

    assert problems


def test_it_waits_between_the_two_looks_rather_than_asking_twice_at_once():
    """Two immediate requests both land inside the same cold start and both
    fail, which turns the retry into decoration."""
    slept: list[float] = []
    answers = [(None, None), (200, HEALTHY)]

    run_check(lambda: answers.pop(0), attempts=2, wait=slept.append)

    assert slept and slept[0] >= 30, "a cold start needs longer than a moment"


def test_a_healthy_first_answer_costs_no_extra_request():
    """This runs on a schedule forever; the quiet path has to stay one
    request."""
    calls = []

    def fetch():
        calls.append(1)
        return (200, HEALTHY)

    run_check(fetch, attempts=2, wait=lambda _: None)

    assert len(calls) == 1


def test_a_real_failure_is_not_retried_away():
    """503 with a dead worker is a definite answer, not a maybe. Retrying it
    only delays the email."""
    calls = []

    def fetch():
        calls.append(1)
        return (503, WORKER_DEAD)

    problems = run_check(fetch, attempts=2, wait=lambda _: None)

    assert problems
    assert len(calls) == 1, "the service answered; asking again tells us nothing new"


# --- the thing it is guarding against ---------------------------------------


def test_the_check_names_the_product_failure_rather_than_the_http_one():
    """Whoever reads this email at 2am needs to know that alerts have stopped,
    not that a probe returned a number."""
    problems = read_verdict(503, WORKER_DEAD)

    assert any("提醒" in p for p in problems), problems


@pytest.mark.parametrize("body", ["", "null", "[]"])
def test_degenerate_bodies_do_not_crash_the_watchdog(body):
    """It must fail loudly, never fail to run -- a watchdog that raises is a
    watchdog that reports nothing."""
    assert read_verdict(200, body)


# --- the URL it is pointed at -----------------------------------------------
#
# The address comes from outside the script: a GitHub repo variable
# (HEALTH_URL) on the scheduled run, argv on a manual one. urllib.urlopen
# accepts more than http -- `file:///etc/passwd` opens a local file and
# `ftp://` opens a socket -- so whatever is in that variable decides what this
# process reads, and it reads it inside a job with a checkout of the repo.
#
# Nothing here is exploitable today: the variable is set by the repo owner, who
# already has far more direct ways to run code in their own Actions job. The
# reason to close it anyway is that this is the ONE script that keeps watch
# when nobody is looking, and 「the watchdog opened a local file and reported it
# healthy」 is a failure mode with no other detector behind it.


def _never_called(monkeypatch):
    """A urlopen that fails the test if anything reaches it.

    Asserting on the RETURN VALUE would not test anything here: fetch() catches
    everything and answers (None, None), and on this machine
    file:///etc/passwd does not exist, so the bad-scheme tests passed green
    before any check existed. What has to be true is that the call never
    happens at all.
    """
    from scripts import watchdog

    opened: list[str] = []
    monkeypatch.setattr(
        watchdog.urllib.request,
        "urlopen",
        lambda url, **_kw: opened.append(url) or _raise_stop(),
    )
    return opened


def test_a_local_file_is_never_opened(monkeypatch):
    from scripts.watchdog import fetch

    opened = _never_called(monkeypatch)

    assert fetch("file:///etc/passwd") == (None, None)
    assert opened == [], "the watchdog opened a local file"


def test_nor_any_other_scheme(monkeypatch):
    from scripts.watchdog import fetch

    opened = _never_called(monkeypatch)

    assert fetch("ftp://example.com/x") == (None, None)
    assert opened == []


def test_a_scheme_less_address_is_refused_rather_than_guessed(monkeypatch):
    """Prepending 「https://」 to whatever was typed is the kind of helpfulness
    that silently points the watchdog at something else."""
    from scripts.watchdog import fetch

    opened = _never_called(monkeypatch)

    assert fetch("example.com/healthz") == (None, None)
    assert opened == []


def test_the_ordinary_case_still_goes_through(monkeypatch):
    from scripts import watchdog

    class _Response:
        status = 200

        @staticmethod
        def read():
            return b'{"status": "ok", "checks": {}}'

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(watchdog.urllib.request, "urlopen", lambda *_a, **_kw: _Response())

    assert watchdog.fetch("https://example.onrender.com/healthz")[0] == 200


def test_plain_http_is_allowed_too(monkeypatch):
    """A self-hosted copy on a LAN has no certificate. Refusing http would
    make the watchdog unusable there, and no watchdog is worse than one on a
    plaintext connection to a health endpoint that carries no secrets."""
    from scripts import watchdog

    called = []
    monkeypatch.setattr(
        watchdog.urllib.request,
        "urlopen",
        lambda url, **_kw: called.append(url) or _raise_stop(),
    )

    watchdog.fetch("http://192.168.1.10:8000/healthz")

    assert called == ["http://192.168.1.10:8000/healthz"]


def _raise_stop():
    raise RuntimeError("stop here; the call itself is what is under test")


def test_the_watchdog_explains_an_unconfigured_deployment():
    """The state a stranger's brand-new deploy is in. 「health check failed」
    would send them to a dashboard to work it out; what they need is 「go and
    finish the setup page」."""
    from scripts.watchdog import read_verdict

    problems = read_verdict(503, '{"checks": {"setup": {"status": "fail"}}}')

    assert len(problems) == 1
    assert "設定" in problems[0], problems[0]


# --- the log this prints into is public ---------------------------------------------


def test_the_backend_url_is_not_printed_into_a_public_actions_log(capsys):
    """The watchdog runs as a GitHub Actions schedule every 15 minutes, and
    this repository is PUBLIC -- anyone can read those logs. Printing the URL
    published the owner's deployment address to the internet 96 times a day.

    It is not a credential, but it is theirs: it is the address somebody would
    need before they could try anything at all against it. And the line bought
    nothing, because the owner has exactly one backend and configured its URL
    themselves.
    """
    from scripts import watchdog

    watchdog.main(["watchdog.py", "https://not-a-real-host.example/healthz"])

    printed = capsys.readouterr().out
    assert "not-a-real-host.example" not in printed


def test_it_still_says_clearly_whether_the_backend_is_up(capsys, monkeypatch):
    """Redacting the URL must not cost the message its meaning -- this is the
    only thing that tells the owner their alerts have stopped."""
    from scripts import watchdog

    monkeypatch.setattr(watchdog, "fetch", lambda url: (503, None))
    code = watchdog.main(["watchdog.py", "https://not-a-real-host.example/healthz"])

    printed = capsys.readouterr().out
    assert code == 1
    assert "提醒" in printed or "問題" in printed
