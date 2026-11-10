"""服务入口。

启动方式（项目根目录下）：
    uvicorn examples.mini_shop.main:app --reload

启动后可访问：
    http://127.0.0.1:8000/docs      交互式接口文档（Swagger UI）
    http://127.0.0.1:8000/openapi.json   机器可读的接口契约
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from examples.common_auth import seed_admin
from examples.mini_shop.config import DEFAULT_USER_BALANCE
from examples.mini_shop.database import SessionLocal, init_db
from examples.mini_shop.models import User
from examples.mini_shop.routers import auth, cart, coupons, inventory, orders, products


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_admin(SessionLocal, User, "APP", balance=DEFAULT_USER_BALANCE)
    yield


app = FastAPI(
    title="Mini Shop API",
    description="接口自动化测试练习用的最小电商服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(cart.router)
app.include_router(coupons.router)
app.include_router(inventory.router)


@app.get("/health", tags=["meta"])
def health():
    """给 CI 轮询用：服务起来了才开始跑测试。"""
    return {"status": "ok"}
