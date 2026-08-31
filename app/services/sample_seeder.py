"""新账号示例数据播种（V2.5）。

- 触发点：register（注册用户/首位管理员）+ guest/token（新建访客）
- 内容：3 个顶级分类 × 各 2 条「示例 ·」任务（status=completed，含用例/报告/四步日志）
- 导出文件：复用 lib_export 现场生成 xlsx/json/xmind，保证详情可看、下载不 404
- 模板：app/assets/sample_tasks.json（来自真实生成任务固化，进 git，本地/线上通用）
- 容错：播种失败只回滚播种本身，绝不影响注册/发 token 主流程
"""
import json
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import OUTPUT_DIR
from app.core.utils import utcnow
from app.models.category import Category
from app.models.task import StepLog, Task
from app.models.user import User
from app.services import pipeline_lib  # noqa: F401  副作用：注入 generator_core 到 sys.path
from src.models.testcase import TestCase

_ASSET = Path(__file__).resolve().parent.parent / "assets" / "sample_tasks.json"


def seed_sample_tasks(db: Session, user: User) -> int:
    """为新账号播种分类与示例任务，返回播种任务数（失败返回 0，不抛异常）。"""
    try:
        data = json.loads(_ASSET.read_text(encoding="utf-8"))
        now = utcnow()
        out_dir = OUTPUT_DIR / user.data_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        count = 0

        for cat_spec in data["categories"]:
            cat = Category(user_id=user.id, name=cat_spec["name"], parent_id=None)
            db.add(cat)
            db.flush()  # 拿 cat.id

            for t in cat_spec["tasks"]:
                tpl = data["templates"][t["template"]]
                task_id = uuid.uuid4().hex[:12]
                db.add(Task(
                    id=task_id,
                    name=t["name"],
                    kind=t["kind"],
                    source_type="text",
                    input_ref=t.get("summary", "平台预置示例数据"),
                    formats="xlsx,json,xmind",
                    status="completed",
                    input_summary=t.get("summary", ""),
                    cases_count=len(tpl["cases"]),
                    duration_ms=t.get("duration_ms", tpl.get("duration_ms", 0.0)),
                    cases_json=json.dumps(tpl["cases"], ensure_ascii=False),
                    report_json=json.dumps(tpl["report"], ensure_ascii=False),
                    created_at=now,
                    finished_at=now + timedelta(milliseconds=t.get("duration_ms", 0)),
                    user_id=user.id,
                    category_id=cat.id,
                ))
                for s in tpl["steps"]:
                    db.add(StepLog(
                        task_id=task_id,
                        name=s.get("name", ""), title=s.get("title", ""),
                        status="completed",
                        started_at=now, finished_at=now,
                        duration_ms=s.get("duration_ms"),
                        output_summary=s.get("output_summary", ""),
                    ))
                # 现场生成导出三件套（失败不阻断播种，仅下载会 404）
                try:
                    fields = set(TestCase.model_fields)
                    cases = [TestCase(**{k: c[k] for k in c if k in fields})
                             for c in tpl["cases"]]
                    pipeline_lib.lib_export(cases, str(out_dir / task_id),
                                            ["xlsx", "json", "xmind"])
                except Exception:  # noqa: BLE001
                    pass
                count += 1

        db.commit()
        return count
    except Exception:  # noqa: BLE001  播种失败绝不影响注册主流程
        db.rollback()
        return 0
