from pydantic import BaseModel, EmailStr, Field
from typing import Optional

class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)

class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)

class RefreshRequest(BaseModel):
    refreshToken: str

class UserInfo(BaseModel):
    id: str
    email: str
    name: str
    isAuthenticated: bool = True

class AuthResponse(BaseModel):
    user: Optional[UserInfo] = None
    accessToken: Optional[str] = None
    refreshToken: Optional[str] = None
    needsConfirmation: bool = False
    error: Optional[str] = None
