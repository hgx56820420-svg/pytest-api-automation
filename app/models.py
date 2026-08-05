"""ORM 数据模型：User / Product / Order。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    orders: Mapped[list["Order"]] = relationship(back_populates="user")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    price: Mapped[float] = mapped_column(Float)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    # on_sale / off_sale
    status: Mapped[str] = mapped_column(String(20), default="on_sale")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)
    # created -> paid，或 created -> cancelled
    status: Mapped[str] = mapped_column(String(20), default="created")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    user: Mapped["User"] = relationship(back_populates="orders")
    product: Mapped["Product"] = relationship()
