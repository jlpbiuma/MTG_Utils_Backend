import pytest
from httpx import AsyncClient, ASGITransport
from src.main import app

@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "mtg-backend"

@pytest.mark.asyncio
async def test_root_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/")
        assert response.status_code == 200
        data = response.json()
        assert "MTG Utils API" in data["message"]

@pytest.mark.asyncio
async def test_import_parse_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post("/api/import/parse", json={"text": "4 Lightning Bolt\n1 Sol Ring"})
        assert response.status_code == 200
        data = response.json()
        assert data["totalCards"] == 5
        assert len(data["mainboard"]) == 2

@pytest.mark.asyncio
async def test_cors_local_network_192_168_0_x():
    transport = ASGITransport(app=app)
    headers = {
        "Origin": "http://192.168.0.45:3000",
        "Access-Control-Request-Method": "GET",
    }
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.options("/api/health", headers=headers)
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://192.168.0.45:3000"

