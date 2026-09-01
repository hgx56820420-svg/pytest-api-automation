"""请求/响应的数据契约（Pydantic 模型）。

这一层是接口测试最该关注的东西：它定义了"合法输入长什么样"和
"响应结构长什么样"。FastAPI 会据此自动做参数校验（不合法直接 422），
也会据此生成 OpenAPI 文档。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------- 认证 ----------

class RegisterRequest(BaseModel):
    # min_length / max_length 就是测试里的边界值来源
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6, max_length=32)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    balance: float
    created_at: datetime


# ---------- 商品 ----------

class ProductCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    price: float = Field(gt=0)
    stock: int = Field(ge=0)


class ProductUpdateRequest(BaseModel):
    """所有字段可选，只更新传了的字段。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    price: float | None = Field(default=None, gt=0)
    stock: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, pattern="^(on_sale|off_sale)$")


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    price: float
    stock: int
    status: str
    created_at: datetime


class ProductListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[ProductResponse]


# ---------- 订单 ----------

class OrderCreateRequest(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=100)
    coupon_code: str | None = Field(default=None, min_length=1, max_length=40)


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    product_id: int
    quantity: int
    amount: float
    coupon_code: str | None = None
    status: str
    created_at: datetime


class OrderListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[OrderResponse]


class CartItemRequest(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=100)


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    quantity: int
    product: ProductResponse


class CartResponse(BaseModel):
    items: list[CartItemResponse]


class CouponCreateRequest(BaseModel):
    code: str = Field(min_length=3, max_length=40, pattern="^[A-Z0-9_-]+$")
    discount_percent: float = Field(gt=0, le=100)
    max_uses: int = Field(gt=0, le=100000)


class CouponResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    discount_percent: float
    max_uses: int
    used_count: int
    status: str


class InventoryTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_id: int
    quantity_change: int
    reason: str
    reference_id: int | None
    created_at: datetime
