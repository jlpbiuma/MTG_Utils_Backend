import hashlib
import secrets
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from src.core.config import settings
from src.core.db import db
from src.schemas.auth import AuthResponse, UserInfo

logger = logging.getLogger("mtg_backend.auth")

# Sessions live in user_sessions (hashed tokens); this is only a cache in front
# of it: token -> (UserInfo, expiry_timestamp).
_token_cache: Dict[str, tuple[UserInfo, float]] = {}
TOKEN_CACHE_TTL = 7 * 24 * 3600  # 7 days, same as the web session cookie
REFRESH_TTL = 30 * 24 * 3600  # 30 days

def hash_password(password: str) -> str:
    """Hashes password using PBKDF2 with SHA-256 and a random 16-byte salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000)
    return f"{salt}:{key.hex()}"

def verify_password(password: str, stored_hash: str) -> bool:
    """Verifies a plain password against the stored salt:key hash."""
    try:
        if ":" not in stored_hash:
            return False
        salt, key = stored_hash.split(":", 1)
        check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100000)
        return secrets.compare_digest(check.hex(), key)
    except Exception as e:
        logger.warning(f"Error during password verification: {e}")
        return False

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_info(db_user) -> UserInfo:
    return UserInfo(
        id=db_user.id,
        email=db_user.email,
        name=db_user.name or db_user.email.split("@")[0],
        isAuthenticated=True,
    )


def _demo_user(email: str = "planeswalker@magic.io") -> UserInfo:
    return UserInfo(id=settings.DEMO_USER_ID, email=email, name="Planeswalker", isAuthenticated=True)


async def _create_session(user: UserInfo) -> AuthResponse:
    """Stores a new session (hashes only) and returns its tokens."""
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    await db.usersession.delete_many(where={"userId": user.id, "refreshExpiresAt": {"lt": now}})
    await db.usersession.create(
        data={
            "tokenHash": _token_hash(access_token),
            "refreshHash": _token_hash(refresh_token),
            "userId": user.id,
            "expiresAt": now + timedelta(seconds=TOKEN_CACHE_TTL),
            "refreshExpiresAt": now + timedelta(seconds=REFRESH_TTL),
        }
    )
    _token_cache[access_token] = (user, time.time() + TOKEN_CACHE_TTL)
    return AuthResponse(user=user, accessToken=access_token, refreshToken=refresh_token)


class AuthService:
    @staticmethod
    async def login(email: str, password: str) -> AuthResponse:
        clean_email = email.strip().lower()

        # Offline / Demo account support
        if clean_email == "demo@magic.io":
            return AuthResponse(
                user=_demo_user(clean_email),
                accessToken="demo-access-token",
                refreshToken="demo-refresh-token",
            )

        try:
            db_user = await db.user.find_unique(where={"email": clean_email})
            if not db_user:
                return AuthResponse(error="Correo o contraseña incorrectos.")

            if not verify_password(password, db_user.passwordHash):
                return AuthResponse(error="Correo o contraseña incorrectos.")

            return await _create_session(_user_info(db_user))
        except Exception as e:
            logger.error(f"Local login error: {e}", exc_info=True)
            return AuthResponse(error=f"Error al iniciar sesión: {str(e)}")

    @staticmethod
    async def signup(email: str, password: str) -> AuthResponse:
        clean_email = email.strip().lower()

        if len(password) < 6:
            return AuthResponse(error="La contraseña debe tener al menos 6 caracteres.")

        if clean_email == "demo@magic.io":
            return AuthResponse(user=_demo_user(clean_email), accessToken="demo-access-token")

        try:
            existing = await db.user.find_unique(where={"email": clean_email})
            if existing:
                return AuthResponse(error="Ya existe una cuenta con este correo electrónico.")

            new_user = await db.user.create(
                data={
                    "email": clean_email,
                    "name": clean_email.split("@")[0],
                    "passwordHash": hash_password(password),
                }
            )
            return await _create_session(_user_info(new_user))
        except Exception as e:
            logger.error(f"Local signup error: {e}", exc_info=True)
            return AuthResponse(error=f"Error al registrar usuario: {str(e)}")

    @staticmethod
    async def logout(token: Optional[str]) -> bool:
        if not token:
            return True
        _token_cache.pop(token, None)
        try:
            await db.usersession.delete_many(where={"tokenHash": _token_hash(token)})
        except Exception as e:
            logger.warning(f"Could not delete session: {e}")
        return True

    @staticmethod
    async def get_user_from_token(token: Optional[str]) -> Optional[UserInfo]:
        if not token or not token.strip():
            return None

        now = time.time()
        cached = _token_cache.get(token)
        if cached:
            user, exp = cached
            if now < exp:
                return user
            _token_cache.pop(token, None)

        if token == "demo-access-token":
            return _demo_user()

        try:
            session = await db.usersession.find_unique(
                where={"tokenHash": _token_hash(token)}, include={"user": True}
            )
        except Exception as e:
            logger.warning(f"Session lookup failed: {e}")
            return None
        if not session or not session.user or session.expiresAt <= datetime.now(timezone.utc):
            return None
        user = _user_info(session.user)
        _token_cache[token] = (user, min(session.expiresAt.timestamp(), now + TOKEN_CACHE_TTL))
        return user

    @staticmethod
    async def refresh_session(refresh_token: str) -> AuthResponse:
        if refresh_token == "demo-refresh-token":
            return AuthResponse(user=_demo_user(), accessToken="demo-access-token", refreshToken="demo-refresh-token")

        try:
            session = await db.usersession.find_unique(
                where={"refreshHash": _token_hash(refresh_token)}, include={"user": True}
            )
            if not session or not session.user or session.refreshExpiresAt <= datetime.now(timezone.utc):
                return AuthResponse(error="La sesión ha caducado. Vuelve a iniciar sesión.")
            # Rotate: the old access and refresh tokens stop working.
            await db.usersession.delete(where={"tokenHash": session.tokenHash})
            for token, (_, _) in list(_token_cache.items()):
                if _token_hash(token) == session.tokenHash:
                    _token_cache.pop(token, None)
            return await _create_session(_user_info(session.user))
        except Exception as e:
            logger.error(f"Refresh error: {e}", exc_info=True)
            return AuthResponse(error="No se pudo renovar la sesión.")
