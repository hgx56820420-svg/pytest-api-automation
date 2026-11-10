"""Meeting 服务入口。

启动方式（项目根目录下）：
    $env:MEETING_DATABASE_URL = "sqlite:///./meeting-test.db"
    python -m uvicorn examples.meeting.main:app --host 127.0.0.1 --port 8030
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from examples.common_auth import seed_admin
from examples.meeting.config import DEFAULT_USER_BALANCE
from examples.meeting.database import SessionLocal, init_db
from examples.meeting.models import User
from examples.meeting.routers import auth, bookings, rooms


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_admin(SessionLocal, User, "MEETING", balance=DEFAULT_USER_BALANCE)
    yield


app = FastAPI(
    title="Meeting Room API",
    description="接口自动化测试第三个被测对象：会议室预约服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(rooms.router)
app.include_router(bookings.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
