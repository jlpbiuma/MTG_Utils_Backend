import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch
from src.main import app
from src.schemas.auth import LoginRequest, SignupRequest, UserInfo, AuthResponse
from src.services.auth_service import AuthService

def test_auth_schemas():
    login = LoginRequest(email="test@magic.io", password="password123")
    assert login.email == "test@magic.io"
    assert login.password == "password123"

    signup = SignupRequest(email="new@magic.io", password="password123")
    assert signup.email == "new@magic.io"

@pytest.mark.asyncio
async def test_demo_login():
    res = await AuthService.login("demo@magic.io", "password123")
    assert res.error is None
    assert res.user is not None
    assert res.user.email == "demo@magic.io"
    assert res.accessToken == "demo-access-token"

@pytest.mark.asyncio
async def test_auth_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Test demo login via HTTP
        response = await ac.post("/api/auth/login", json={"email": "demo@magic.io", "password": "password123"})
        assert response.status_code == 200
        data = response.json()
        assert data["user"]["email"] == "demo@magic.io"
        assert data["accessToken"] == "demo-access-token"

        # Test me endpoint with demo token
        headers = {"Authorization": "Bearer demo-access-token"}
        me_res = await ac.get("/api/auth/me", headers=headers)
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["isAuthenticated"] is True

        # Test logout endpoint
        logout_res = await ac.post("/api/auth/logout", headers=headers)
        assert logout_res.status_code == 200

        # Test refresh endpoint
        refresh_res = await ac.post("/api/auth/refresh", json={"refreshToken": "demo-refresh-token"})
        assert refresh_res.status_code == 200
        refresh_data = refresh_res.json()
        assert refresh_data["accessToken"] == "demo-access-token"

        # Test me endpoint with X-User-Id header
        custom_headers = {"X-User-Id": "11111111-2222-3333-4444-555555555555"}
        uid_res = await ac.get("/api/auth/me", headers=custom_headers)
        assert uid_res.status_code == 200
        assert uid_res.json()["id"] == "11111111-2222-3333-4444-555555555555"

@pytest.mark.asyncio
async def test_get_current_user_id_dependency():
    from src.core.auth import get_current_user_id

    # 1. Bearer demo token
    uid = await get_current_user_id(authorization="Bearer demo-access-token")
    assert uid is not None

    # 2. X-User-Id header
    uid2 = await get_current_user_id(x_user_id="test-uuid-user")
    assert uid2 == "test-uuid-user"

    # 3. Direct UUID in Bearer token
    uid3 = await get_current_user_id(authorization="Bearer aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert uid3 == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

