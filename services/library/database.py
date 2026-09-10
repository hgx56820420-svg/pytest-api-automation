"""Library 服务数据库会话管理（与 app.database 同构但完全隔离）。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from services.library.config import LIB_DATABASE_URL

engine = create_engine(
    LIB_DATABASE_URL,
    connect_args={"check_same_thread": False} if LIB_DATABASE_URL.startswith("sqlite") else {},
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
    from services.library import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
