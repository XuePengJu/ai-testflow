"""工作流引擎：任务状态机 + 四 Agent 顺序编排 + 步骤日志。

在后台线程运行（FastAPI BackgroundTasks），前端轮询任务状态即可看到实时进度。
"""
import json
import time
from app.core.utils import utcnow

from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.core.db import SessionLocal
from app.models.task import Task, StepLog
from app.services.pipeline_lib import cases_to_json
from app.workflow.agents import (
    parser_agent, generator_agent, reviewer_agent, exporter_agent,
)

# 步骤编排：(name, title, 函数, 流转数据key)
STEPS = [
    ("parser", "解析规格", parser_agent.run_parser, "units"),
    ("generator", "AI 生成用例", generator_agent.run_generator, "cases"),
    ("reviewer", "质量校验", reviewer_agent.run_reviewer, "report"),
    ("exporter", "导出文件", exporter_agent.run_exporter, "files"),
]


def _prepare_input(task: Task, data_dir: str) -> str:
    """返回供 ParserAgent 读取的文件路径；text 类型落盘为 .md。"""
    base = UPLOAD_DIR / data_dir if data_dir else UPLOAD_DIR
    base.mkdir(parents=True, exist_ok=True)
    if task.source_type == "text":
        p = base / f"{task.id}.md"
        p.write_text(task.input_ref, encoding="utf-8")
        return str(p)
    return str(base / task.input_ref)


def run_task(task_id: str) -> None:
    """执行整个工作流。异常时把任务标记为 failed 并记录错误步骤。"""
    db = SessionLocal()
    t0 = time.time()
    try:
        task = db.get(Task, task_id)
        if not task:
            return
        task.status = "running"
        db.commit()

        data_dir = task.user_data_dir(db)
        out_dir = OUTPUT_DIR / data_dir if data_dir else OUTPUT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)

        input_path = _prepare_input(task, data_dir)
        data: dict = {}

        for name, title, fn, key in STEPS:
            step = StepLog(
                task_id=task_id, name=name, title=title,
                status="running", started_at=utcnow(),
            )
            db.add(step)
            db.commit()
            s0 = time.time()
            try:
                if name == "parser":
                    out, summary = fn(input_path, task.kind)
                elif name == "generator":
                    out, summary = fn(data["units"])
                elif name == "reviewer":
                    out, summary = fn(data["cases"])
                else:  # exporter
                    fmts = [f.strip() for f in task.formats.split(",") if f.strip()]
                    out, summary = fn(data["cases"], str(out_dir / task.id), fmts)
                data[key] = out
                step.status = "completed"
                step.output_summary = summary
                step.finished_at = utcnow()
                step.duration_ms = round((time.time() - s0) * 1000, 1)
                db.commit()
            except Exception as e:  # noqa: BLE001
                step.status = "failed"
                step.error = str(e)
                step.finished_at = utcnow()
                step.duration_ms = round((time.time() - s0) * 1000, 1)
                task.status = "failed"
                db.commit()
                return

        cases = data["cases"]
        task.status = "completed"
        task.cases_count = len(cases)
        task.cases_json = cases_to_json(cases)
        task.report_json = json.dumps(data["report"], ensure_ascii=False)
        task.finished_at = utcnow()
        task.duration_ms = round((time.time() - t0) * 1000, 1)
        db.commit()
    finally:
        db.close()
