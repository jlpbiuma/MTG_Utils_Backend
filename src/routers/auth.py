from fastapi import APIRouter, Header, HTTPException, status
from typing import Optional
from src.schemas.auth import LoginRequest, SignupRequest, RefreshRequest, AuthResponse, UserInfo
from src.services.auth_service import AuthService
from src.core.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login", response_model=AuthResponse)
async def login(data: LoginRequest):
    res = await AuthService.login(data.email, data.password)
    if res.error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=res.error)
    return res

@router.post("/signup", response_model=AuthResponse)
async def signup(data: SignupRequest):
    res = await AuthService.signup(data.email, data.password)
    if res.error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=res.error)
    return res

@router.post("/logout")
async def logout(authorization: Optional[str] = Header(None)):
    token = None
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
    await AuthService.logout(token)
    return {"success": True}

@router.get("/me", response_model=UserInfo)
async def get_me(
    authorization: Optional[str] = Header(None),
    x_user_id: Optional[str] = Header(None)
):
    token = None
    if isinstance(authorization, str) and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]

    if token:
        user = await AuthService.get_user_from_token(token)
        if user:
            return user

    # If X-User-Id is passed
    if isinstance(x_user_id, str) and x_user_id.strip():
        return UserInfo(
            id=x_user_id.strip(),
            email="user@magic.io",
            name="Usuario",
            isAuthenticated=True,
        )

    # Fallback to guest / demo
    return UserInfo(
        id=settings.DEMO_USER_ID,
        email="guest@magic.io",
        name="Invitado",
        isAuthenticated=False,
    )

@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(data: RefreshRequest):
    res = await AuthService.refresh_session(data.refreshToken)
    if res.error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=res.error)
    return res
