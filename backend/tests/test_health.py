def test_healthz_returns_ok(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


def test_docs_available(client):
    response = client.get("/docs")

    assert response.status_code == 200
