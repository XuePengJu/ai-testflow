"""工作流引擎：任务状态机 + 四 Agent 顺序编排 + 步骤日志。

在后台线程运行（FastAPI BackgroundTasks），前端轮询任务状态即可看到实时进度。
"""
import json
import time
from pathlib import Path

from app.core.utils import utcnow

from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.core.db import SessionLocal
from app.models.task import Task, StepLog
from app.models.user import User
from app.services import llm_service
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

        # ---- V2.4：模型解析（用户配置 > 平台默认 > 服务器 .env > mock 兜底） ----
        owner = db.get(User, task.user_id) if task.user_id else None
        eff = llm_service.resolve_effective(db, owner)
        text_cfg, vision_cfg = eff["text"], eff["vision"]
        llm_client = None
        if text_cfg:
            try:
                llm_client = llm_service.OpenAICompatClient(
                    text_cfg["base_url"], text_cfg["api_key"], text_cfg["model"])
            except llm_service.LLMError:
                llm_client = None
        model_desc = (
            f'{text_cfg["model"]} · {text_cfg["provider_label"]}' if text_cfg else "mock 兜底"
        )

        # ---- V2.4：两段式视觉理解（business 输入里的图片引用） ----
        vision_note = ""
        if task.kind == "business":
            try:
                raw = Path(input_path).read_text(encoding="utf-8")
                refs = llm_service.extract_image_refs(raw)
                if refs:
                    if vision_cfg:
                        try:
                            vclient = llm_service.OpenAICompatClient(
                                vision_cfg["base_url"], vision_cfg["api_key"], vision_cfg["model"])
                            new_text, n = llm_service.vision_enrich(raw, vclient)
                            Path(input_path).write_text(new_text, encoding="utf-8")
                            vision_note = f"；视觉模型解析 {n} 张截图"
                        except llm_service.LLMError as e:
                            vision_note = f"；截图解析失败（{e}），已忽略图片"
                    else:
                        vision_note = f"；检测到 {len(refs)} 张图片，未配置图像识别模型已忽略"
            except (OSError, UnicodeDecodeError):
                pass    # 输入不是文本文件（理论不达），跳过

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
                    if vision_note:
                        summary = f"{summary}{vision_note}"
                elif name == "generator":
                    out, summary = fn(data["units"], llm_client, model_desc)
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
