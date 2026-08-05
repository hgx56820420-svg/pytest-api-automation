"""服务入口。

启动方式（项目根目录下）：
    uvicorn app.main:app --reload

启动后可访问：
    http://127.0.0.1:8000/docs      交互式接口文档（Swagger UI）
    http://127.0.0.1:8000/openapi.json   机器可读的接口契约
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import init_db
from app.routers import auth, orders, products


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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


@app.get("/health", tags=["meta"])
def health():
    """给 CI 轮询用：服务起来了才开始跑测试。"""
    return {"status": "ok"}
