"""Meeting 服务入口。

启动方式（项目根目录下）：
    $env:MEETING_DATABASE_URL = "sqlite:///./meeting-test.db"
    python -m uvicorn services.meeting.main:app --host 127.0.0.1 --port 8030
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.meeting.database import init_db
from services.meeting.routers import auth, bookings, rooms


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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
