from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class SuperadminLoginRequest(BaseModel):
    username: str
    password: str


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)
