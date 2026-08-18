"""GET /api/indicators -- the catalogue, browsable and queryable.

The owner asked for the list to be inspectable rather than folklore, so that
"which indicators do I have?" has an answer that cannot drift from the code.
"""

from app.services.indicators import catalogue


def test_the_endpoint_returns_every_registered_indicator(auth_client):
    response = auth_client.get("/api/indicators")

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body["indicators"]] == [
        spec.name for spec in catalogue()
    ]


def test_each_entry_carries_what_the_owner_needs_to_use_it(auth_client):
    body = auth_client.get("/api/indicators").json()

    rsi = next(item for item in body["indicators"] if item["name"] == "rsi")
    assert rsi["category"] == "momentum"
    assert rsi["title"]
    assert "RSI" in rsi["title"] or "強弱" in rsi["title"]
    assert rsi["description"]
    assert rsi["result"] == "series"
    assert rsi["keys"] == []
    assert rsi["params"] == [
        {"name": "values", "type": "list[float]", "required": True, "default": None},
        {"name": "period", "type": "int", "required": False, "default": 14},
    ]


def test_a_multi_line_indicator_advertises_its_keys(auth_client):
    body = auth_client.get("/api/indicators").json()

    macd = next(item for item in body["indicators"] if item["name"] == "macd")
    assert macd["result"] == "series_map"
    assert macd["keys"] == ["macd", "signal", "histogram"]

    pivots = next(item for item in body["indicators"] if item["name"] == "pivot_points")
    assert pivots["result"] == "value_map"
    assert set(pivots["keys"]) == {"p", "r1", "r2", "r3", "s1", "s2", "s3"}


def test_categories_come_back_labelled_in_traditional_chinese(auth_client):
    body = auth_client.get("/api/indicators").json()

    labels = {item["name"]: item["label"] for item in body["categories"]}
    assert set(labels) == {"trend", "momentum", "volatility", "volume", "price"}
    assert all(any("一" <= ch <= "鿿" for ch in label) for label in labels.values())
    assert sum(item["count"] for item in body["categories"]) == len(body["indicators"])


def test_the_list_can_be_filtered_to_one_category(auth_client):
    response = auth_client.get("/api/indicators", params={"category": "volume"})

    assert response.status_code == 200
    names = [item["name"] for item in response.json()["indicators"]]
    assert "obv" in names
    assert "rsi" not in names
    # The category summary is not filtered: it is how the owner discovers the
    # other categories exist.
    assert len(response.json()["categories"]) == 5


def test_an_unknown_category_is_a_422_not_an_empty_list(auth_client):
    """An empty list reads as "you have no volume indicators", which is a
    different and much more misleading answer than "no such category"."""
    response = auth_client.get("/api/indicators", params={"category": "vloume"})

    assert response.status_code == 422


def test_the_catalogue_needs_a_logged_in_user(client):
    assert client.get("/api/indicators").status_code == 401
