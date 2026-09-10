"""Meeting 服务数据库会话管理（独立 engine，与其他服务隔离）。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from services.meeting.config import MEETING_DATABASE_URL

engine = create_engine(
    MEETING_DATABASE_URL,
    connect_args={"check_same_thread": False} if MEETING_DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from services.meeting import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
