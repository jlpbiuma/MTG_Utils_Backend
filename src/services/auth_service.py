import httpx
import logging
import time
from typing import Optional, Dict, Any
from src.core.config import settings
from src.schemas.auth import AuthResponse, UserInfo

logger = logging.getLogger("mtg_backend.auth")

# In-memory token cache: token -> (UserInfo, expiry_timestamp)
_token_cache: Dict[str, tuple[UserInfo, float]] = {}
TOKEN_CACHE_TTL = 300  # 5 minutes

class AuthService:
    @staticmethod
    def _get_headers(token: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "apikey": settings.SUPABASE_ANON_KEY,
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    @staticmethod
    async def login(email: str, password: str) -> AuthResponse:
        clean_email = email.strip().lower()

        # Dev / Offline demo fallback
        if clean_email == "demo@magic.io" or not settings.SUPABASE_URL:
            user = UserInfo(
                id=settings.DEMO_USER_ID,
                email=clean_email,
                name="Planeswalker",
                isAuthenticated=True,
            )
            return AuthResponse(
                user=user,
                accessToken="demo-access-token",
                refreshToken="demo-refresh-token",
            )

        url = f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=password"
        headers = AuthService._get_headers()

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, headers=headers, json={"email": clean_email, "password": password})
                data = res.json()

                if res.status_code != 200:
                    err_msg = data.get("error_description") or data.get("msg") or data.get("message") or "Error al iniciar sesión"
                    return AuthResponse(error=err_msg)

                raw_user = data.get("user", {})
                user_id = raw_user.get("id")
                user_email = raw_user.get("email", clean_email)
                user_name = user_email.split("@")[0] if user_email else "Usuario"

                user = UserInfo(
                    id=user_id,
                    email=user_email,
                    name=user_name,
                    isAuthenticated=True,
                )

                access_token = data.get("access_token")
                refresh_token = data.get("refresh_token")

                if access_token:
                    _token_cache[access_token] = (user, time.time() + TOKEN_CACHE_TTL)

                return AuthResponse(
                    user=user,
                    accessToken=access_token,
                    refreshToken=refresh_token,
                )
            except Exception as e:
                logger.error(f"Login error connecting to Supabase: {e}")
                return AuthResponse(error=f"Error conectando con el servicio de autenticación: {str(e)}")

    @staticmethod
    async def signup(email: str, password: str) -> AuthResponse:
        clean_email = email.strip().lower()

        if not settings.SUPABASE_URL:
            user = UserInfo(
                id=settings.DEMO_USER_ID,
                email=clean_email,
                name="Planeswalker",
                isAuthenticated=True,
            )
            return AuthResponse(user=user, accessToken="demo-access-token")

        url = f"{settings.SUPABASE_URL}/auth/v1/signup"
        headers = AuthService._get_headers()

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, headers=headers, json={"email": clean_email, "password": password})
                data = res.json()

                if res.status_code != 200 and res.status_code != 201:
                    err_msg = data.get("error_description") or data.get("msg") or data.get("message") or "Error al registrar usuario"
                    return AuthResponse(error=err_msg)

                raw_user = data.get("user") or data
                user_id = raw_user.get("id")
                user_email = raw_user.get("email", clean_email)
                user_name = user_email.split("@")[0] if user_email else "Usuario"

                user = UserInfo(
                    id=user_id,
                    email=user_email,
                    name=user_name,
                    isAuthenticated=True,
                )

                access_token = data.get("access_token")
                refresh_token = data.get("refresh_token")
                needs_confirmation = bool(user_id and not access_token)

                if access_token:
                    _token_cache[access_token] = (user, time.time() + TOKEN_CACHE_TTL)

                return AuthResponse(
                    user=user,
                    accessToken=access_token,
                    refreshToken=refresh_token,
                    needsConfirmation=needs_confirmation,
                )
            except Exception as e:
                logger.error(f"Signup error: {e}")
                return AuthResponse(error=f"Error en registro: {str(e)}")

    @staticmethod
    async def logout(token: Optional[str]) -> bool:
        if token and token in _token_cache:
            del _token_cache[token]

        if not token or not settings.SUPABASE_URL or token == "demo-access-token":
            return True

        url = f"{settings.SUPABASE_URL}/auth/v1/logout"
        headers = AuthService._get_headers(token)

        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                await client.post(url, headers=headers)
                return True
            except Exception as e:
                logger.warning(f"Error notifying Supabase logout: {e}")
                return True

    @staticmethod
    async def get_user_from_token(token: Optional[str]) -> Optional[UserInfo]:
        if not token or not token.strip():
            return None

        # Check cache
        now = time.time()
        if token in _token_cache:
            user, exp = _token_cache[token]
            if now < exp:
                return user

        if token == "demo-access-token" or not settings.SUPABASE_URL:
            return UserInfo(
                id=settings.DEMO_USER_ID,
                email="planeswalker@magic.io",
                name="Planeswalker",
                isAuthenticated=True,
            )

        url = f"{settings.SUPABASE_URL}/auth/v1/user"
        headers = AuthService._get_headers(token)

        async with httpx.AsyncClient(timeout=6.0) as client:
            try:
                res = await client.get(url, headers=headers)
                if res.status_code != 200:
                    return None

                data = res.json()
                user_id = data.get("id")
                user_email = data.get("email", "")
                user_name = user_email.split("@")[0] if user_email else "Usuario"

                user = UserInfo(
                    id=user_id,
                    email=user_email,
                    name=user_name,
                    isAuthenticated=True,
                )
                _token_cache[token] = (user, now + TOKEN_CACHE_TTL)
                return user
            except Exception as e:
                logger.error(f"Error validating token with Supabase: {e}")
                return None

    @staticmethod
    async def refresh_session(refresh_token: str) -> AuthResponse:
        if not settings.SUPABASE_URL or refresh_token == "demo-refresh-token":
            user = UserInfo(
                id=settings.DEMO_USER_ID,
                email="planeswalker@magic.io",
                name="Planeswalker",
                isAuthenticated=True,
            )
            return AuthResponse(user=user, accessToken="demo-access-token", refreshToken="demo-refresh-token")

        url = f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=refresh_token"
        headers = AuthService._get_headers()

        async with httpx.AsyncClient(timeout=8.0) as client:
            try:
                res = await client.post(url, headers=headers, json={"refresh_token": refresh_token})
                data = res.json()
                if res.status_code != 200:
                    return AuthResponse(error=data.get("message", "Error al refrescar token"))

                raw_user = data.get("user", {})
                user = UserInfo(
                    id=raw_user.get("id"),
                    email=raw_user.get("email", ""),
                    name=raw_user.get("email", "").split("@")[0] or "Usuario",
                    isAuthenticated=True,
                )
                return AuthResponse(
                    user=user,
                    accessToken=data.get("access_token"),
                    refreshToken=data.get("refresh_token"),
                )
            except Exception as e:
                return AuthResponse(error=str(e))
