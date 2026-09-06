from fastapi import Header, Depends
from typing import Optional
from src.core.config import settings

async def get_current_user_id(
    x_user_id: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> str:
    """
    Resolves current user ID from X-User-Id header or Authorization token.
    Defaults to DEMO_USER_ID for local development.
    """
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()

    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        # In future, can verify Supabase JWT if SUPABASE_JWT_SECRET is set
        # For now, if token has a payload or user id, return it
        if token and token != "undefined" and token != "null":
            # If the token itself is a uuid or valid identifier
            return token

    return settings.DEMO_USER_ID
