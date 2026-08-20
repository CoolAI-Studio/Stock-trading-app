"""A search that finds nothing has to say which list it looked in.

The empty state named one date -- 「台股清單更新於 2026-08-19」 -- because that
was the only bundled table there was. There are two now, and a US company that
listed after the directory was bundled produces the same empty result with a
sentence that talks about Taiwan.

That matters more than it sounds. 「找不到」 has three quite different causes and
only one of them is the owner's mistake:

  a typo, which they fix by retyping;
  a company listed after the tables were built, which they fix by refreshing
    them (scripts/refresh_tw_listings.py, scripts/refresh_us_listings.py);
  a market this app does not model at all, which they cannot fix.

Naming the tables and their dates is what lets somebody tell the second from
the first. Without it the honest answer 「this is too new for the list」 is
indistinguishable from 「you typed it wrong」, and the owner retypes it five
times before giving up on a stock that exists.
"""

from app.services import symbol_search


def test_the_response_dates_both_tables(auth_client):
    body = auth_client.get("/api/symbols/search?q=zzzzzzzz").json()

    assert body["listings_generated_at"], body
    assert body["us_listings_generated_at"], body


def test_the_dates_are_the_ones_in_the_files(auth_client):
    body = auth_client.get("/api/symbols/search?q=zzzzzzzz").json()

    assert body["listings_generated_at"] == symbol_search.listings_generated_at()
    assert body["us_listings_generated_at"] == symbol_search.us_listings_generated_at()


def test_a_missing_us_table_is_not_an_error_on_the_page(monkeypatch):
    """Same rule as the Taiwanese one: without the table Latin search degrades
    to what it was, and the page somebody is using to add a stock must not 500
    because a data file went missing."""
    from pathlib import Path

    monkeypatch.setattr(symbol_search, "_US_LISTINGS_DATA", Path("no-such-file.json"))

    assert symbol_search.us_listings_generated_at() is None
