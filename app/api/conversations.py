"""会话 API：建会话 / 列表 / 详情 / 追加消息（对话记录持久化）。

- 每个用户独立会话（user_id 隔离）
- 会话详情按 task_id 回填关联任务的节点步骤（StepLog）与用例（cases_json）
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.tasks import _parse_cases, _delete_task_cascade
from app.core.config import OUTPUT_DIR
from app.core.db import get_db
from app.core.utils import utcnow
from app.models.conversation import Conversation, Message
from app.models.task import Task, StepLog
from app.models.user import User
from app.schemas.conversation import ConversationOut, MessageOut

router = APIRouter(prefix="/conversations", tags=["会话"])


class ConversationIn(BaseModel):
    title: str = Field(default="新会话", max_length=80)


class MessageIn(BaseModel):
    role: str = Field(default="user", pattern="^(user|assistant)$")
    content: str = ""
    thinking: str = ""
    task_id: str | None = None


def _own_conversation(db: Session, user: User, conv_id: str) -> Conversation:
    c = db.get(Conversation, conv_id)
    if not c or c.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return c


def _task_brief(db: Session, task_id: str | None) -> dict | None:
    """任务摘要（含节点步骤 steps + 用例 cases），供会话详情回填 message.task。"""
    if not task_id:
        return None
    t = db.get(Task, task_id)
    if not t:
        return None
    steps = db.query(StepLog).filter_by(task_id=task_id).order_by(StepLog.id).all()
    return {
        "id": t.id,
        "name": t.name,
        "status": t.status,
        "cases_count": t.cases_count,
        "duration_ms": t.duration_ms,
        "created_at": t.created_at,
        "steps": [
            {"name": s.name, "title": s.title, "status": s.status,
             "duration_ms": s.duration_ms, "output_summary": s.output_summary,
             "input_summary": s.input_summary,
             "error": s.error}
            for s in steps
        ],
        "cases": _parse_cases(t.cases_json),
    }


def _to_out(db: Session, c: Conversation, include_messages: bool = False) -> ConversationOut:
    count = db.query(Message).filter_by(conversation_id=c.id).count()
    task_count = db.query(Task).filter(Task.conversation_id == c.id).count()
    out = ConversationOut(
        id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at,
        message_count=count, task_count=task_count, messages=[],
    )
    if include_messages:
        msgs = db.query(Message).filter_by(conversation_id=c.id).order_by(Message.id).all()
        out.messages = [
            MessageOut(
                id=m.id, role=m.role, content=m.content, thinking=m.thinking,
                task_id=m.task_id, created_at=m.created_at,
                task=_task_brief(db, m.task_id),
            )
            for m in msgs
        ]
    return out


@router.post("", response_model=ConversationOut, status_code=201)
def create_conversation(body: ConversationIn,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    c = Conversation(id=uuid.uuid4().hex[:12], user_id=user.id,
                     title=body.title.strip() or "新会话")
    db.add(c)
    db.commit()
    db.refresh(c)
    return _to_out(db, c)


@router.get("", response_model=list[ConversationOut])
def list_conversations(user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    rows = (db.query(Conversation)
            .filter(Conversation.user_id == user.id)
            .order_by(Conversation.updated_at.desc()).all())
    return [_to_out(db, c) for c in rows]


@router.get("/{conv_id}", response_model=ConversationOut)
def get_conversation(conv_id: str, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    c = _own_conversation(db, user, conv_id)
    return _to_out(db, c, include_messages=True)


@router.post("/{conv_id}/messages", response_model=MessageOut, status_code=201)
def add_message(conv_id: str, body: MessageIn,
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    c = _own_conversation(db, user, conv_id)
    m = Message(conversation_id=c.id, role=body.role, content=body.content,
                thinking=body.thinking, task_id=body.task_id)
    db.add(m)
    c.updated_at = utcnow()
    db.commit()
    db.refresh(m)
    return MessageOut(
        id=m.id, role=m.role, content=m.content, thinking=m.thinking,
        task_id=m.task_id, created_at=m.created_at,
        task=_task_brief(db, m.task_id),
    )


@router.delete("/{conv_id}")
def delete_conversation(conv_id: str,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """删除整个会话：连带删该会话下所有任务（StepLog + 导出文件）+ 所有消息 + 会话本身。
    对话一旦删除不可恢复——前端应弹确认窗。"""
    c = _own_conversation(db, user, conv_id)
    # 1. 连带删除该会话下所有任务
    tasks = db.query(Task).filter(Task.conversation_id == conv_id).all()
    deleted_files = 0
    for t in tasks:
        deleted_files += _delete_task_cascade(db, t)
        db.delete(t)
    # 2. 删该会话的所有消息
    db.query(Message).filter(Message.conversation_id == conv_id).delete(synchronize_session=False)
    # 3. 删会话本身
    db.delete(c)
    db.commit()
    return {"ok": True, "deleted_tasks": len(tasks), "deleted_files": deleted_files}
