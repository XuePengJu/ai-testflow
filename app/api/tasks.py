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
from app.models.conversation import Message
from app.models.task import Task, StepLog
from app.models.user import User
from app.schemas.task import TaskOut, StepLogOut
from app.workflow.engine import run_task
from app.workflow.iterate import run_iterate

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
        parent_task_id=task.parent_task_id,
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
    conversation_id: str = Form(""),
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
        conversation_id=conversation_id or None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)

    # 回填该会话下最后一条 assistant 消息的 task_id（聊天流回放时据此渲染节点/用例卡）
    if conversation_id:
        last_msg = (db.query(Message)
                    .filter(Message.conversation_id == conversation_id,
                            Message.role == "assistant",
                            Message.task_id.is_(None))
                    .order_by(Message.id.desc()).first())
        if last_msg:
            last_msg.task_id = task_id
            db.commit()

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


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str,
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """删除任务：级联删 StepLog + 导出文件 + 清除关联 assistant 消息的 task_id。
    对话本身保留（只是这条消息不再渲染节点卡/用例卡）。"""
    t = _own_task(db, task_id, user)
    deleted_files = _delete_task_cascade(db, t)
    db.delete(t)
    db.commit()
    return {"ok": True, "deleted_task_id": task_id, "deleted_files": deleted_files}


def _delete_task_cascade(db: Session, t: Task) -> int:
    """删除任务的级联副作用：StepLog + 导出文件 + 清除 messages.task_id。
    返回删除的导出文件数。调用方负责 db.commit() 和 db.delete(t)。"""
    db.query(StepLog).filter(StepLog.task_id == t.id).delete(synchronize_session=False)
    data_dir = t.user_data_dir(db)
    deleted_files = 0
    for ext in ("xlsx", "json", "xmind"):
        p = OUTPUT_DIR / data_dir / f"{t.id}.{ext}"
        if p.exists():
            try:
                p.unlink()
                deleted_files += 1
            except OSError:
                pass
    # 清除关联 assistant 消息的 task_id（消息本身保留，只是解除关联）
    db.query(Message).filter(Message.task_id == t.id).update(
        {"task_id": None}, synchronize_session=False)
    return deleted_files


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


@router.post("/tasks/{task_id}/iterate", response_model=TaskOut, status_code=201)
async def iterate_task(
    task_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    file: UploadFile | None = File(None),
    instruction: str = Form(""),
    conversation_id: str = Form(""),
):
    """对已完成任务进行迭代补充，生成新版本子任务。

    - instruction：补充要求（必填）
    - file：可选，上传本地用例文件（xmind/xlsx/json），导入后与原用例合并
    - conversation_id：可选，关联会话（默认继承原任务的会话）
    """
    parent = _own_task(db, task_id, user)
    if parent.status not in ("completed", "failed"):
        raise HTTPException(status_code=400, detail=f"任务状态为 {parent.status}，仅 completed/failed 任务可迭代")
    if not instruction.strip() and not file:
        raise HTTPException(status_code=400, detail="补充要求（instruction）与用例文件至少提供一个")

    # 并发保护：同一原任务下不能有正在运行的子任务
    running_child = (db.query(Task)
                     .filter(Task.parent_task_id == task_id, Task.status == "running")
                     .first())
    if running_child:
        raise HTTPException(status_code=409, detail="该任务已有迭代正在进行中，请等待完成")

    # 上传文件落盘
    uploaded_path = None
    uploaded_ext = None
    if file:
        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in (".xmind", ".xlsx", ".json"):
            raise HTTPException(status_code=400, detail=f"不支持的用例文件格式 {ext}（支持 xmind/xlsx/json）")
        user_dir = UPLOAD_DIR / user.data_dir
        user_dir.mkdir(parents=True, exist_ok=True)
        uploaded_path = str(user_dir / f"iter_{uuid.uuid4().hex[:8]}{ext}")
        with open(uploaded_path, "wb") as f:
            f.write(await file.read())
        uploaded_ext = ext.lstrip(".")

    eff_conv = conversation_id or parent.conversation_id

    # 预创建新任务空壳（status=pending），后台 run_iterate 填充
    new_task_id = uuid.uuid4().hex[:12]
    new_task = Task(
        id=new_task_id,
        name=parent.name,
        kind=parent.kind,
        source_type="iterate",
        input_ref=instruction[:500],
        formats=parent.formats,
        status="pending",
        user_id=parent.user_id,
        conversation_id=eff_conv,
        parent_task_id=task_id,
        category_id=parent.category_id,
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)

    background_tasks.add_task(
        run_iterate, new_task_id, task_id, instruction.strip(), uploaded_path, uploaded_ext, eff_conv)
    return _to_out(db, new_task)
