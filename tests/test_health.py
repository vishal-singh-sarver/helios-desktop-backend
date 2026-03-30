def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "version" in r.json()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_version(client):
    r = client.get("/version")
    assert r.status_code == 200
    assert "version" in r.json()
