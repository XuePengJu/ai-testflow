"""SQLite 连接与 ORM 基类。"""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import DB_PATH

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    # timeout=30：后台任务/调度器/请求并发写时给足锁等待，避免 "database is locked"
    connect_args={"check_same_thread": False, "timeout": 30},
)

# 沙箱环境（如 WorkBuddy 沙箱）会拦截 SQLite 的 journal 文件写，
# 任何一次 DB 写都会让连接报废、进程退出。设置内存 journal 可规避该限制。
# 仅当 AITF_DB_MEMORY_JOURNAL=1 时启用；生产环境默认关闭以保证事务持久性。
if os.environ.get("AITF_DB_MEMORY_JOURNAL") == "1":
    @event.listens_for(engine, "connect")
    def _set_memory_journal(dbapi_conn, conn_record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=MEMORY")
        cur.execute("PRAGMA synchronous=OFF")
        cur.close()
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表（首次运行时调用）。"""
    import app.models.task  # noqa: F401  确保模型注册到 Base
    import app.models.user  # noqa: F401
    import app.models.category  # noqa: F401
    import app.models.llm_config  # noqa: F401
    import app.models.conversation  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """轻量幂等列迁移（SQLite create_all 不会给已存在表加新列）。"""
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(tasks)"))]
        if "category_id" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN category_id INTEGER"))
        if "is_sample" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN is_sample BOOLEAN NOT NULL DEFAULT 0"))
        if "conversation_id" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN conversation_id VARCHAR"))
        if "parent_task_id" not in cols:
            conn.execute(text("ALTER TABLE tasks ADD COLUMN parent_task_id VARCHAR"))
        conn.commit()


def get_db():
    """FastAPI 依赖：提供数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
