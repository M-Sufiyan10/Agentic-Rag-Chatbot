async def test_health_returns_ok(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "mcp": {}, "langfuse": "off", "email": "off"}


async def test_health_reports_langfuse_off(client):
    r = await client.get("/api/health")
    assert r.json()["langfuse"] == "off"
