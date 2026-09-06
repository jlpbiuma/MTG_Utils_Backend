from fastapi import Header, Depends
from typing import Optional
from src.core.config import settings
from src.services.auth_service import AuthService

async def get_current_user_id(
    x_user_id: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> str:
    """
    Resolves current user ID with priority:
    1. Bearer token validated against Supabase Auth via AuthService
    2. X-User-Id header
    3. DEMO_USER_ID for local development
    """
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        if token and token not in ["undefined", "null", ""]:
            if len(token) == 36 and token.count("-") == 4:
                return token
            user = await AuthService.get_user_from_token(token)
            if user:
                return user.id

    if isinstance(x_user_id, str) and x_user_id.strip():
        return x_user_id.strip()

    return settings.DEMO_USER_ID
