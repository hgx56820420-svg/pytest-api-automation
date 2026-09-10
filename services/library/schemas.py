"""Library 服务请求/响应模型。"""

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6, max_length=32)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    username: str
    deposit: float

    model_config = {"from_attributes": True}


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    author: str = Field(default="", max_length=100)
    deposit: float = Field(gt=0)
    total_copies: int = Field(ge=1)


class BookUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    author: str | None = Field(default=None, max_length=100)
    deposit: float | None = Field(default=None, gt=0)
    total_copies: int | None = Field(default=None, ge=1)


class BookResponse(BaseModel):
    id: int
    title: str
    author: str
    deposit: float
    total_copies: int
    available_copies: int
    status: str

    model_config = {"from_attributes": True}


class BorrowCreate(BaseModel):
    book_id: int


class BorrowResponse(BaseModel):
    id: int
    user_id: int
    book_id: int
    deposit_charged: float
    status: str

    model_config = {"from_attributes": True}


class PaginatedBorrows(BaseModel):
    total: int
    page: int
    size: int
    items: list[BorrowResponse]


class PaginatedBooks(BaseModel):
    total: int
    page: int
    size: int
    items: list[BookResponse]
