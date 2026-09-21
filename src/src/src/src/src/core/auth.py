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
    1. Bearer token validated via AuthService (token cache or DB user)
    2. X-User-Id header
    3. Direct UUID in Bearer token if no X-User-Id provided (for tests/direct API calls)
    4. DEMO_USER_ID for local development
    """
    bearer_token = None
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        if token and token not in ["undefined", "null", ""]:
            bearer_token = token
            user = await AuthService.get_user_from_token(token)
            if user:
                return user.id

    if isinstance(x_user_id, str) and x_user_id.strip():
        return x_user_id.strip()

    if bearer_token and len(bearer_token) == 36 and bearer_token.count("-") == 4:
        return bearer_token

    return settings.DEMO_USER_ID

