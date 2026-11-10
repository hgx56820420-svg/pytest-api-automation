"""Library 服务入口。

启动方式（项目根目录下）：
    $env:LIB_DATABASE_URL = "sqlite:///./library-test.db"
    python -m uvicorn examples.library.main:app --host 127.0.0.1 --port 8020
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from examples.common_auth import seed_admin
from examples.library.config import DEFAULT_USER_DEPOSIT
from examples.library.database import SessionLocal, init_db
from examples.library.models import User
from examples.library.routers import auth, books, borrows


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_admin(SessionLocal, User, "LIB", deposit=DEFAULT_USER_DEPOSIT)
    yield


app = FastAPI(
    title="Library API",
    description="接口自动化测试二次验证用的最小图书借阅服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(books.router)
app.include_router(borrows.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
