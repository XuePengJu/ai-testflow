"""任务管理 REST 端点。"""
import os
import uuid

from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import UPLOAD_DIR, OUTPUT_DIR
from app.core.db import get_db
from app.models.task import Task, StepLog
from app.schemas.task import TaskOut, StepLogOut
from app.workflow.engine import run_task

router = APIRouter()


def _to_out(db: Session, task: Task) -> TaskOut:
    steps = (
        db.query(StepLog).filter_by(task_id=task.id).order_by(StepLog.id).all()
    )
    return TaskOut(
        id=task.id, name=task.name, kind=task.kind, source_type=task.source_type,
        status=task.status, cases_count=task.cases_count, duration_ms=task.duration_ms,
        created_at=task.created_at, finished_at=task.finished_at,
        steps=[
            StepLogOut(
                name=s.name, title=s.title, status=s.status,
                duration_ms=s.duration_ms, input_summary=s.input_summary,
                output_summary=s.output_summary, error=s.error,
            )
            for s in steps
        ],
    )


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    file: UploadFile | None = File(None),
    text: str = Form(""),
    kind: str = Form("api"),
    formats: str = Form("xlsx,json"),
    name: str = Form(""),
):
    """提交一个测试用例生成任务。可上传规格文件或粘贴文本。"""
    task_id = uuid.uuid4().hex[:12]
    source_type = "file" if file else "text"
    input_ref = ""

    if file:
        ext = os.path.splitext(file.filename or "")[1] or ".json"
        fname = f"{task_id}{ext}"
        (UPLOAD_DIR / fname).write_bytes(await file.read())
        input_ref = fname
    elif text.strip():
        input_ref = text
    else:
        raise HTTPException(status_code=400, detail="file 与 text 至少提供一个")

    task = Task(
        id=task_id,
        name=name or f"任务-{task_id}",
        kind=kind,
        source_type=source_type,
        input_ref=input_ref,
        formats=formats,
        status="pending",
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    background_tasks.add_task(run_task, task_id)
    return _to_out(db, task)


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(db: Session = Depends(get_db)):
    tasks = db.query(Task).order_by(Task.created_at.desc()).all()
    return [_to_out(db, t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _to_out(db, task)


@router.get("/tasks/{task_id}/download")
def download(task_id: str, fmt: str = "xlsx"):
    """下载导出文件（fmt=xlsx/json/xmind）。"""
    ext = fmt if fmt.startswith(".") else "." + fmt
    path = OUTPUT_DIR / f"{task_id}{ext}"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"未找到 {fmt} 导出文件")
    return FileResponse(path, filename=f"{task_id}{ext}")
