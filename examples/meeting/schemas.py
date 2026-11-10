"""Meeting 服务请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


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
    balance: float

    model_config = {"from_attributes": True}


class RoomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    capacity: int = Field(ge=1)
    hourly_price: float = Field(gt=0)


class RoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    capacity: int | None = Field(default=None, ge=1)
    hourly_price: float | None = Field(default=None, gt=0)


class RoomResponse(BaseModel):
    id: int
    name: str
    capacity: int
    hourly_price: float
    status: str

    model_config = {"from_attributes": True}


class BookingCreate(BaseModel):
    room_id: int
    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def check_time_range(self):
        """时间范围属于请求体约束，走 Pydantic 校验以返回标准 422 错误数组。"""
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class BookingResponse(BaseModel):
    id: int
    user_id: int
    room_id: int
    start_time: datetime
    end_time: datetime
    amount: float
    status: str

    model_config = {"from_attributes": True}


class PaginatedRooms(BaseModel):
    total: int
    page: int
    size: int
    items: list[RoomResponse]


class PaginatedBookings(BaseModel):
    total: int
    page: int
    size: int
    items: list[BookingResponse]
