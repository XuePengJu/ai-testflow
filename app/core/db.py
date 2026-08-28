"""SQLite 连接与 ORM 基类。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    # timeout=30：后台任务/调度器/请求并发写时给足锁等待，避免 "database is locked"
    connect_args={"check_same_thread": False, "timeout": 30},
)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表（首次运行时调用）。"""
    import app.models.task  # noqa: F401  确保模型注册到 Base
    import app.models.user  # noqa: F401
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
