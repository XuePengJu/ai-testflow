"""任务管理 REST 端点（V2：登录 + 数据隔离）。"""
import json
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import UPLOAD_DIR, OUTPUT_DIR, GUEST_MAX_TASKS
from app.core.db import get_db
from app.models.task import Task, StepLog
from app.models.user import User
from app.schemas.task import TaskOut, StepLogOut
from app.workflow.engine import run_task

router = APIRouter()


def _parse_cases(cases_json: str | None) -> list[dict]:
    """cases_json 字段是 Task 模型里的 Text 列，存储原始 JSON 字符串。

    返回结构化用例列表。解析失败时兜底返回 []，不让单条脏数据把整个详情接口炸掉。
    """
    if not cases_json:
        return []
    try:
        obj = json.loads(cases_json)
        return obj if isinstance(obj, list) else []
    except (ValueError, TypeError):
        return []


def _to_out(db: Session, task: Task, include_cases: bool = False) -> TaskOut:
    steps = (
        db.query(StepLog).filter_by(task_id=task.id).order_by(StepLog.id).all()
    )
    return TaskOut(
        id=task.id, name=task.name, kind=task.kind, source_type=task.source_type,
        status=task.status, cases_count=task.cases_count, duration_ms=task.duration_ms,
        formats=task.formats, category_id=task.category_id,
        created_at=task.created_at, finished_at=task.finished_at,
        steps=[
            StepLogOut(
                name=s.name, title=s.title, status=s.status,
                duration_ms=s.duration_ms, input_summary=s.input_summary,
                output_summary=s.output_summary, error=s.error,
            )
            for s in steps
        ],
        # 仅详情接口为 True：77 条用例 ≈ 几十 KB，列表页不背这个 payload
        cases=_parse_cases(task.cases_json) if include_cases else [],
    )


def _own_task(db: Session, task_id: str, user: User) -> Task:
    """非本人且非 admin → 404（不暴露存在性）。"""
    task = db.get(Task, task_id)
    if not task or (task.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    file: UploadFile | None = File(None),
    text: str = Form(""),
    kind: str = Form("business"),
    formats: str = Form("xlsx,json"),
    name: str = Form(""),
):
    """提交一个测试用例生成任务。可上传规格文件或粘贴文本。"""
    # 访客任务上限（防滥用）
    if user.role == "guest":
        count = db.query(Task).filter(
            Task.user_id == user.id, Task.is_sample.is_(False)
        ).count()
        if count >= GUEST_MAX_TASKS:
            raise HTTPException(status_code=429, detail=f"访客最多 {GUEST_MAX_TASKS} 个任务，注册后无限制")

    task_id = uuid.uuid4().hex[:12]
    source_type = "file" if file else "text"
    input_ref = ""

    # 文件落 data_dir 目录（用户隔离）
    user_dir = UPLOAD_DIR / user.data_dir
    user_dir.mkdir(parents=True, exist_ok=True)
    if file:
        ext = os.path.splitext(file.filename or "")[1] or ".json"
        fname = f"{task_id}{ext}"
        (user_dir / fname).write_bytes(await file.read())
        input_ref = fname
    elif text.strip():
        input_ref = text
    else:
        raise HTTPException(status_code=400, detail="file 与 text 至少提供一个")

    (OUTPUT_DIR / user.data_dir).mkdir(parents=True, exist_ok=True)

    task = Task(
        id=task_id,
        name=name or f"任务-{task_id}",
        kind=kind,
        source_type=source_type,
        input_ref=input_ref,
        formats=formats,
        status="pending",
        user_id=user.id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    background_tasks.add_task(run_task, task_id)
    return _to_out(db, task)


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db),
               user: User = Depends(get_current_user),
               all: bool = False):
    """只返回当前用户的任务；admin 可带 ?all=true 看全部。"""
    q = db.query(Task)
    if not (user.role == "admin" and all):
        q = q.filter(Task.user_id == user.id)
    tasks = q.order_by(Task.created_at.desc()).all()
    return [_to_out(db, t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """任务详情：默认带上结构化用例（用于网页思维导图 + 测试用例 tab）。"""
    return _to_out(db, _own_task(db, task_id, user), include_cases=True)


@router.get("/tasks/{task_id}/download")
def download(task_id: str, fmt: str = "xlsx",
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """下载导出文件（fmt=xlsx/json/xmind）。非本人任务 → 404。"""
    task = _own_task(db, task_id, user)
    ext = fmt if fmt.startswith(".") else "." + fmt
    path = OUTPUT_DIR / task.user_data_dir(db) / f"{task_id}{ext}"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"未找到 {fmt} 导出文件")
    return FileResponse(path, filename=f"{task_id}{ext}")
