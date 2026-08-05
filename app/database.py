"""数据库会话管理。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

# check_same_thread=False 是 SQLite 在多线程 Web 服务下的必要配置。
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每个请求拿一个独立 session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from app import models  # noqa: F401  导入以注册表定义

    Base.metadata.create_all(bind=engine)
