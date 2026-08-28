"""访客清理：APScheduler 每小时 + 启动兜底 + 访问时懒清理。

清理一个过期 guest = 删 tasks / step_logs（DB）+ 删临时目录（文件）+ 物理删用户记录
+ 写 clean_log 审计。username 走 guest_<ip_hash>_<seq> 规则，物理删不撞唯一约束。
"""
import logging
import shutil
from app.core.utils import utcnow

from sqlalchemy.orm import Session

from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.models.task import Task, StepLog
from app.models.user import User, CleanLog

logger = logging.getLogger("guest_cleaner")


def clean_guest(db: Session, guest: User, trigger: str) -> dict:
    """清理单个 guest：级联任务/日志/文件/目录/用户记录，返回统计。"""
    deleted_tasks = db.query(Task).filter(Task.user_id == guest.id).count()

    # DB 级联：step_logs → tasks → user
    task_ids = [t.id for t in db.query(Task.id).filter(Task.user_id == guest.id).all()]
    if task_ids:
        db.query(StepLog).filter(StepLog.task_id.in_(task_ids)).delete(synchronize_session=False)
        db.query(Task).filter(Task.user_id == guest.id).delete(synchronize_session=False)

    # 文件：删 uploads / outputs 下的 guest 临时目录
    deleted_files = 0
    for base in (UPLOAD_DIR, OUTPUT_DIR):
        d = base / guest.data_dir if guest.data_dir else None
        if d and d.exists() and d.name == guest.data_dir:  # 防路径逃逸
            deleted_files += sum(1 for _ in d.rglob("*") if _.is_file())
            shutil.rmtree(d, ignore_errors=True)  # ignore_errors 防并发占用

    db.add(CleanLog(
        guest_ip_hash=guest.ip_hash or "",
        guest_username=guest.username,
        deleted_tasks=deleted_tasks,
        deleted_files=deleted_files,
        trigger=trigger,
    ))
    db.delete(guest)
    db.commit()
    logger.info("cleaned guest %s (%s tasks, %s files, trigger=%s)",
                guest.username, deleted_tasks, deleted_files, trigger)
    return {"username": guest.username, "deleted_tasks": deleted_tasks,
            "deleted_files": deleted_files, "trigger": trigger}


def clean_expired(db: Session, trigger: str = "scheduler") -> list[dict]:
    """删除所有过期 guest。启动兜底 / 定时任务 / admin 手动触发共用。"""
    now = utcnow()
    guests = db.query(User).filter(
        User.role == "guest", User.expires_at < now,
    ).all()
    return [clean_guest(db, g, trigger) for g in guests]


def start_scheduler() -> None:
    """随 FastAPI lifespan 启动：每小时清理一次 + 启动时兜底跑一遍。"""
    from apscheduler.schedulers.background import BackgroundScheduler
    from app.core.db import SessionLocal

    def _job():
        db = SessionLocal()
        try:
            clean_expired(db, trigger="scheduler")
        except Exception as e:  # noqa: BLE001
            logger.error("scheduled clean failed: %s", e)
        finally:
            db.close()

    # 启动兜底：服务重启间隔可能超 1h
    _job()

    sched = BackgroundScheduler(daemon=True)
    sched.add_job(_job, "interval", hours=1, id="guest_cleaner")
    sched.start()
    logger.info("guest cleaner scheduler started (interval=1h, boot clean done)")
