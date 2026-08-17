import time

import pytest

from app.core import login_throttle


@pytest.fixture(autouse=True)
def _clean_throttle_state():
    login_throttle.reset_all()


def test_failures_below_the_threshold_do_not_lock():
    for _ in range(2):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    assert login_throttle.seconds_until_unlocked("a@example.com") == 0


def test_reaching_the_threshold_locks():
    for _ in range(3):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    assert login_throttle.seconds_until_unlocked("a@example.com") > 0


def test_lockout_is_per_key():
    for _ in range(3):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    assert login_throttle.seconds_until_unlocked("b@example.com") == 0


def test_expired_lockout_no_longer_blocks():
    for _ in range(3):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=-1)
    assert login_throttle.seconds_until_unlocked("a@example.com") == 0


def test_clear_resets_the_counter():
    for _ in range(2):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    login_throttle.clear("a@example.com")

    for _ in range(2):
        login_throttle.register_failure("a@example.com", max_attempts=3, lockout_seconds=60)
    assert login_throttle.seconds_until_unlocked("a@example.com") == 0


def test_old_failures_decay_instead_of_accumulating_forever():
    login_throttle.register_failure("a@example.com", max_attempts=2, lockout_seconds=0.05)
    time.sleep(0.06)
    login_throttle.register_failure("a@example.com", max_attempts=2, lockout_seconds=0.05)
    assert login_throttle.seconds_until_unlocked("a@example.com") == 0
