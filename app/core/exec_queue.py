"""M2 自动化执行队列 + Worker 池 + 启动恢复（照抄 task_queue 模式，contract-m2 4）。

- 执行记录状态持久化在 DB，部署重启不丢
- 固定 Worker 数 = EXEC_WORKERS（默认 1：本机串行跑浏览器最稳）
- 启动恢复：running（上次中断）重置 pending、pending 重新入队
"""
import logging
import queue
import threading

from app.core import config
from app.core.db import SessionLocal
from app.models.automation import ExecutionRun
from app.services.auto_runner import run_execution

logger = logging.getLogger("exec_queue")

WORKER_COUNT = config.EXEC_WORKERS
_exec_queue: queue.Queue[str] = queue.Queue()
_workers_started = False


def enqueue(run_id: str) -> None:
    """执行记录入队（由 API 层调用）。"""
    _exec_queue.put(run_id)
    logger.info("执行入队: %s (队列长度: %d)", run_id, _exec_queue.qsize())


def _worker_loop(worker_id: int) -> None:
    """Worker 主循环：阻塞取执行记录 → 跑 pytest → 循环。daemon 线程随进程退出。"""
    logger.info("ExecWorker-%d 启动", worker_id)
    while True:
        try:
            run_id = _exec_queue.get(timeout=1)
        except queue.Empty:
            continue
        try:
            logger.info("ExecWorker-%d 开始执行: %s", worker_id, run_id)
            run_execution(run_id)
            logger.info("ExecWorker-%d 执行结束: %s", worker_id, run_id)
        except Exception:
            # run_execution 内部已兜底，这里防御性记录（绝不让线程死掉）
            logger.exception("ExecWorker-%d 执行异常: %s", worker_id, run_id)
        finally:
            _exec_queue.task_done()


def start_workers() -> None:
    """启动 Worker 池（幂等，重复调用不重复创建）。

    采用「首次触发时懒启动」：main.py lifespan 未接入执行队列，
    由 API 层（POST run-auto）首次调用时启动；启动前先做一次 pending 恢复。
    """
    global _workers_started
    if _workers_started:
        return
    _workers_started = True
    recover_pending_runs()
    for i in range(WORKER_COUNT):
        t = threading.Thread(
            target=_worker_loop, args=(i,), daemon=True, name=f"exec-worker-{i}"
        )
        t.start()
    logger.info("执行队列启动: %d 个 Worker", WORKER_COUNT)


def recover_pending_runs() -> None:
    """启动恢复：把 running（上次部署中断）和 pending 的执行记录重新入队。"""
    db = SessionLocal()
    try:
        running = db.query(ExecutionRun).filter(ExecutionRun.status == "running").all()
        for r in running:
            r.status = "pending"
            logger.info("恢复中断执行: %s (原 running → pending)", r.id)
        if running:
            db.commit()

        pending = db.query(ExecutionRun).filter(ExecutionRun.status == "pending").all()
        for r in pending:
            _exec_queue.put(r.id)
        if pending:
            logger.info("执行启动恢复完成: 共 %d 条入队", len(pending))
        else:
            logger.info("执行启动恢复: 无待执行记录")
    finally:
        db.close()


def wait_for_drain() -> None:
    """优雅关闭：阻塞直到队列中所有执行完毕（daemon 线程由进程退出兜底）。"""
    if _exec_queue.unfinished_tasks > 0:
        logger.info("等待队列中 %d 条执行完毕...", _exec_queue.unfinished_tasks)
    _exec_queue.join()
    logger.info("执行队列已清空")
