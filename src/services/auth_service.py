import hashlib
import secrets
import logging
import time
import uuid
from typing import Optional, Dict, Any
from src.core.config import settings
from src.core.db import db
from src.schemas.auth import AuthResponse, UserInfo

logger = logging.getLogger("mtg_backend.auth")

# In-memory session store: token -> (UserInfo, expiry_timestamp)
_token_cache: Dict[str, tuple[UserInfo, float]] = {}
TOKEN_CACHE_TTL = 7 * 24 * 3600  # 7 days

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

class AuthService:
    @staticmethod
    async def login(email: str, password: str) -> AuthResponse:
        clean_email = email.strip().lower()

        # Offline / Demo account support
        if clean_email == "demo@magic.io":
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

        try:
            # Query local PostgreSQL database
            db_user = await db.user.find_unique(where={"email": clean_email})
            if not db_user:
                return AuthResponse(error="Correo o contraseña incorrectos.")

            if not verify_password(password, db_user.passwordHash):
                return AuthResponse(error="Correo o contraseña incorrectos.")

            user = UserInfo(
                id=db_user.id,
                email=db_user.email,
                name=db_user.name or db_user.email.split("@")[0],
                isAuthenticated=True,
            )

            access_token = str(uuid.uuid4())
            refresh_token = str(uuid.uuid4())

            _token_cache[access_token] = (user, time.time() + TOKEN_CACHE_TTL)

            return AuthResponse(
                user=user,
                accessToken=access_token,
                refreshToken=refresh_token,
            )
        except Exception as e:
            logger.error(f"Local login error: {e}", exc_info=True)
            return AuthResponse(error=f"Error al iniciar sesión: {str(e)}")

    @staticmethod
    async def signup(email: str, password: str) -> AuthResponse:
        clean_email = email.strip().lower()

        if len(password) < 6:
            return AuthResponse(error="La contraseña debe tener al menos 6 caracteres.")

        # If demo email
        if clean_email == "demo@magic.io":
            user = UserInfo(
                id=settings.DEMO_USER_ID,
                email=clean_email,
                name="Planeswalker",
                isAuthenticated=True,
            )
            return AuthResponse(user=user, accessToken="demo-access-token")

        try:
            # Check if user already exists in local database
            existing = await db.user.find_unique(where={"email": clean_email})
            if existing:
                return AuthResponse(error="Ya existe una cuenta con este correo electrónico.")

            p_hash = hash_password(password)
            user_name = clean_email.split("@")[0]

            new_user = await db.user.create(
                data={
                    "email": clean_email,
                    "name": user_name,
                    "passwordHash": p_hash,
                }
            )

            user = UserInfo(
                id=new_user.id,
                email=new_user.email,
                name=new_user.name or user_name,
                isAuthenticated=True,
            )

            access_token = str(uuid.uuid4())
            refresh_token = str(uuid.uuid4())

            _token_cache[access_token] = (user, time.time() + TOKEN_CACHE_TTL)

            return AuthResponse(
                user=user,
                accessToken=access_token,
                refreshToken=refresh_token,
            )
        except Exception as e:
            logger.error(f"Local signup error: {e}", exc_info=True)
            return AuthResponse(error=f"Error al registrar usuario: {str(e)}")

    @staticmethod
    async def logout(token: Optional[str]) -> bool:
        if token and token in _token_cache:
            del _token_cache[token]
        return True

    @staticmethod
    async def get_user_from_token(token: Optional[str]) -> Optional[UserInfo]:
        if not token or not token.strip():
            return None

        # Check memory session cache
        now = time.time()
        if token in _token_cache:
            user, exp = _token_cache[token]
            if now < exp:
                return user

        if token == "demo-access-token":
            return UserInfo(
                id=settings.DEMO_USER_ID,
                email="planeswalker@magic.io",
                name="Planeswalker",
                isAuthenticated=True,
            )

        # If token is a user ID directly (UUID)
        if len(token) == 36 and token.count("-") == 4:
            try:
                db_user = await db.user.find_unique(where={"id": token})
                if db_user:
                    user = UserInfo(
                        id=db_user.id,
                        email=db_user.email,
                        name=db_user.name or db_user.email.split("@")[0],
                        isAuthenticated=True,
                    )
                    _token_cache[token] = (user, now + TOKEN_CACHE_TTL)
                    return user
            except Exception:
                pass

        return None

    @staticmethod
    async def refresh_session(refresh_token: str) -> AuthResponse:
        if refresh_token == "demo-refresh-token":
            user = UserInfo(
                id=settings.DEMO_USER_ID,
                email="planeswalker@magic.io",
                name="Planeswalker",
                isAuthenticated=True,
            )
            return AuthResponse(user=user, accessToken="demo-access-token", refreshToken="demo-refresh-token")

        # Generate a new token
        new_token = str(uuid.uuid4())
        user = UserInfo(
            id=settings.DEMO_USER_ID,
            email="planeswalker@magic.io",
            name="Planeswalker",
            isAuthenticated=True,
        )
        _token_cache[new_token] = (user, time.time() + TOKEN_CACHE_TTL)
        return AuthResponse(user=user, accessToken=new_token, refreshToken=refresh_token)
