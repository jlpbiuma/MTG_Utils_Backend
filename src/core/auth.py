import logging
from fastapi import Header, HTTPException, status
from typing import Optional
from src.core.config import settings
from src.schemas.auth import UserInfo
from src.services.auth_service import AuthService

logger = logging.getLogger("mtg_backend.auth")


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        if token and token not in ["undefined", "null", ""]:
            return token
    return None


def is_admin(user: Optional[UserInfo]) -> bool:
    return bool(user and user.email and user.email.lower() in settings.admin_emails)


async def get_current_user_id(
    x_user_id: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> str:
    """
    Resolves the current user from the session token (Authorization: Bearer).
    X-User-Id only selects another user for an admin session (ADMIN_USER_EMAILS).
    Without a valid session the request is rejected with 401, unless
    ALLOW_INSECURE_DEV_AUTH is set for local development, which restores the
    old fallbacks: X-User-Id, a bare user UUID as token, then DEMO_USER_ID.
    """
    bearer_token = _bearer(authorization)
    target_id = x_user_id.strip() if isinstance(x_user_id, str) and x_user_id.strip() else None
    if bearer_token:
        user = await AuthService.get_user_from_token(bearer_token)
        if user:
            if target_id and target_id != user.id and is_admin(user):
                logger.info("Admin %s acting as user %s", user.email, target_id)
                return target_id
            return user.id

    if settings.ALLOW_INSECURE_DEV_AUTH:
        if target_id:
            return target_id
        if bearer_token and len(bearer_token) == 36 and bearer_token.count("-") == 4:
            return bearer_token
        return settings.DEMO_USER_ID

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Sesión no válida o caducada. Vuelve a iniciar sesión.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_admin(authorization: Optional[str] = Header(None)) -> UserInfo:
    """Only an authenticated session whose email is in ADMIN_USER_EMAILS."""
    token = _bearer(authorization)
    user = await AuthService.get_user_from_token(token) if token else None
    if not is_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acceso solo para administradores.")
    return user
