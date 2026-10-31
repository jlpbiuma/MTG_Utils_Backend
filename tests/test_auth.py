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

def test_password_hashing():
    from src.services.auth_service import hash_password, verify_password

    plain = "mox_emerald_secret_123"
    hashed = hash_password(plain)

    assert hashed != plain
    assert ":" in hashed
    assert verify_password(plain, hashed) is True
    assert verify_password("wrong_password", hashed) is False
    assert verify_password(plain, "corrupted_hash") is False

@pytest.mark.asyncio
async def test_local_signup_and_login():
    from types import SimpleNamespace
    from src.services.auth_service import hash_password

    mock_db = AsyncMock()
    mock_db.user = AsyncMock()

    # Signup: user does not exist yet
    mock_db.user.find_unique.return_value = None
    mock_created = SimpleNamespace(
        id="user-uuid-12345",
        email="urza@magic.io",
        name="urza",
        passwordHash=hash_password("karn_golem_99"),
    )
    mock_db.user.create.return_value = mock_created

    with patch("src.services.auth_service.db", mock_db):
        res = await AuthService.signup("urza@magic.io", "karn_golem_99")
        assert res.error is None
        assert res.user is not None
        assert res.user.email == "urza@magic.io"
        assert res.accessToken is not None

        # Login: user exists and password is valid
        mock_db.user.find_unique.return_value = mock_created
        login_res = await AuthService.login("urza@magic.io", "karn_golem_99")
        assert login_res.error is None
        assert login_res.user.id == "user-uuid-12345"

        # Login: wrong password
        login_fail = await AuthService.login("urza@magic.io", "wrong_pass")
        assert login_fail.error == "Correo o contraseña incorrectos."




@pytest.mark.asyncio
async def test_admin_session_can_act_as_another_user(monkeypatch):
    from src.core.config import settings
    from src.services import auth_service

    monkeypatch.setattr(settings, "ADMIN_USER_EMAILS", "admin-ia@mtg-utils.local")
    admin = UserInfo(id="admin-id", email="admin-ia@mtg-utils.local", name="admin-ia", isAuthenticated=True)
    other = UserInfo(id="other-id", email="someone@magic.io", name="someone", isAuthenticated=True)
    monkeypatch.setitem(auth_service._token_cache, "admin-token", (admin, 9e12))
    monkeypatch.setitem(auth_service._token_cache, "user-token", (other, 9e12))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        from src.core.auth import get_current_user_id
        assert await get_current_user_id(x_user_id="target-id", authorization="Bearer admin-token") == "target-id"
        # A regular session keeps its own id whatever X-User-Id says.
        assert await get_current_user_id(x_user_id="target-id", authorization="Bearer user-token") == "other-id"

        with patch("src.routers.admin.db") as mock_db:
            mock_db.query_raw = AsyncMock(return_value=[{"id": "u1", "email": "a@b.c"}])
            ok = await ac.get("/api/admin/users", headers={"Authorization": "Bearer admin-token"})
            denied = await ac.get("/api/admin/users", headers={"Authorization": "Bearer user-token"})
            anonymous = await ac.get("/api/admin/users", headers={"X-User-Id": "admin-id"})
        assert ok.status_code == 200 and ok.json()[0]["id"] == "u1"
        assert denied.status_code == 403
        assert anonymous.status_code == 403


@pytest.mark.asyncio
async def test_admin_powers_off_without_config(monkeypatch):
    from src.core.config import settings
    from src.core.auth import get_current_user_id
    from src.services import auth_service

    monkeypatch.setattr(settings, "ADMIN_USER_EMAILS", "")
    admin = UserInfo(id="admin-id", email="admin-ia@mtg-utils.local", name="admin-ia", isAuthenticated=True)
    monkeypatch.setitem(auth_service._token_cache, "admin-token", (admin, 9e12))
    assert await get_current_user_id(x_user_id="target-id", authorization="Bearer admin-token") == "admin-id"


@pytest.mark.asyncio
async def test_production_rejects_requests_without_a_session(monkeypatch):
    from fastapi import HTTPException
    from src.core.config import settings
    from src.core.auth import get_current_user_id

    monkeypatch.setattr(settings, "ALLOW_INSECURE_DEV_AUTH", False)
    for kwargs in (
        {},
        {"x_user_id": "11111111-2222-3333-4444-555555555555"},
        {"authorization": "Bearer aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
        {"authorization": "Bearer unknown-token", "x_user_id": "someone"},
    ):
        with patch("src.services.auth_service.db") as mock_db:
            mock_db.usersession.find_unique = AsyncMock(return_value=None)
            with pytest.raises(HTTPException) as exc:
                await get_current_user_id(**{"x_user_id": None, "authorization": None, **kwargs})
        assert exc.value.status_code == 401

    # The public demo session keeps working.
    assert await get_current_user_id(x_user_id=None, authorization="Bearer demo-access-token") == settings.DEMO_USER_ID

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/decks", headers={"X-User-Id": "11111111-2222-3333-4444-555555555555"})
        me = await ac.get("/api/auth/me", headers={"X-User-Id": "11111111-2222-3333-4444-555555555555"})
    assert res.status_code == 401
    assert me.json()["isAuthenticated"] is False


@pytest.mark.asyncio
async def test_sessions_are_persisted_hashed_and_survive_restart():
    from types import SimpleNamespace
    from datetime import datetime, timedelta, timezone
    from src.services import auth_service
    from src.services.auth_service import hash_password

    db_user = SimpleNamespace(id="u-1", email="urza@magic.io", name="urza", passwordHash=hash_password("karn_golem_99"))
    rows = {}

    async def create(data):
        rows[data["tokenHash"]] = SimpleNamespace(user=db_user, **data)
        return rows[data["tokenHash"]]

    async def find_unique(where, include=None):
        if "tokenHash" in where:
            return rows.get(where["tokenHash"])
        return next((r for r in rows.values() if r.refreshHash == where["refreshHash"]), None)

    async def delete(where):
        return rows.pop(where["tokenHash"], None)

    async def delete_many(where):
        for key in [k for k, r in rows.items() if where.get("tokenHash") in (None, k) and "tokenHash" in where]:
            rows.pop(key)

    mock_db = AsyncMock()
    mock_db.user.find_unique.return_value = db_user
    mock_db.usersession.create.side_effect = create
    mock_db.usersession.find_unique.side_effect = find_unique
    mock_db.usersession.delete.side_effect = delete
    mock_db.usersession.delete_many.side_effect = delete_many

    with patch("src.services.auth_service.db", mock_db):
        login = await AuthService.login("urza@magic.io", "karn_golem_99")
        token, refresh = login.accessToken, login.refreshToken
        # Only hashes are stored.
        assert token not in rows and all(token not in str(vars(r)) for r in rows.values())

        auth_service._token_cache.clear()  # API restart
        assert (await AuthService.get_user_from_token(token)).id == "u-1"
        # A user id is not a token.
        assert await AuthService.get_user_from_token("u-1") is None

        refreshed = await AuthService.refresh_session(refresh)
        assert refreshed.user.id == "u-1" and refreshed.accessToken != token
        auth_service._token_cache.clear()
        assert await AuthService.get_user_from_token(token) is None  # rotated
        assert (await AuthService.refresh_session(refresh)).error  # single use

        next(iter(rows.values())).expiresAt = datetime.now(timezone.utc) - timedelta(seconds=1)
        auth_service._token_cache.clear()
        assert await AuthService.get_user_from_token(refreshed.accessToken) is None

        await AuthService.logout(refreshed.accessToken)
        assert rows == {}
