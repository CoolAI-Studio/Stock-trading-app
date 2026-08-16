from app.ws.tickets import issue_ticket, redeem_ticket


def test_issued_ticket_redeems_to_the_right_user():
    ticket = issue_ticket(user_id=42, ttl_seconds=30)
    assert redeem_ticket(ticket) == 42


def test_ticket_is_single_use():
    ticket = issue_ticket(user_id=42, ttl_seconds=30)
    assert redeem_ticket(ticket) == 42
    assert redeem_ticket(ticket) is None


def test_unknown_ticket_is_refused():
    assert redeem_ticket("not-a-real-ticket") is None


def test_expired_ticket_is_refused():
    ticket = issue_ticket(user_id=42, ttl_seconds=-1)  # already expired
    assert redeem_ticket(ticket) is None
