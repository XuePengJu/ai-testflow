"""对话流式端点（SSE，前端打字机体验）。

- POST /api/chat/stream
  入参：{ message, history?, attached_text? }
  响应：text/event-stream，逐 event 输出原始 delta / 完成 / 错误

SSE 协议：
  event: delta
  data: {"content": "..."}

  event: done
  data: {"full": "...", "source": "user|platform|env|mock"}

  event: error
  data: {"message": "..."}

前端收到 delta 后按 思考...思考 切分，把思考内容放进折叠面板，其余渲染为正式回复。

流式响应 Content-Type 是 text/event-stream，不会被 ApiCryptoMiddleware 加密
（中间件只加密 application/json）。
"""
import json
import re

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.utils import utcnow
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.schemas.llm_config import ChatIn
from app.services import llm_service

router = APIRouter()


def _sse(event: str, data: dict) -> bytes:
    """构造一个 SSE 事件（UTF-8 JSON）。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _split_think(full: str) -> tuple[str, str]:
    """切分思考与回复，返回 (thinking, reply)。与前端 splitThink 保持一致。

    支持两种上游格式：
    - 真实模型：<think>...</think>
    - mock： 思考 ... 思考 分隔符
    """
    if not full:
        return "", ""
    m = re.search(r"<think>([\s\S]*?)</think>", full)
    if m:
        thinking = m.group(1).strip()
        reply = re.sub(r"<think>[\s\S]*?</think>", "", full).strip()
        return thinking, reply
    m = re.search(r" 思考([\s\S]*?)思考", full)
    if m:
        thinking = m.group(1).strip()
        reply = (full[:m.start()] + full[m.end():]).strip()
        return thinking, reply
    return "", full.strip()


def _persist_chat(db: Session, user: User | None, body: ChatIn, full_text: str) -> None:
    """流式结束后把 user + assistant 消息落库到会话（对话记录持久化）。"""
    if not user or not body.conversation_id:
        return
    conv = db.get(Conversation, body.conversation_id)
    if not conv or conv.user_id != user.id:
        return
    thinking, reply = _split_think(full_text)
    db.add(Message(conversation_id=conv.id, role="user", content=body.message))
    db.add(Message(conversation_id=conv.id, role="assistant",
                   content=reply, thinking=thinking))
    conv.updated_at = utcnow()
    db.commit()


async def _run(db: Session, user: User | None, body: ChatIn, source: str):
    """异步 SSE 事件流 generator。

    上游 llm_service.chat_stream 现在输出原始 delta，本函数只做简单转发，
    由前端自行解析 思考...思考 切分思考面板与回复面板。
    流式结束后（finally）把 user + assistant 消息落库到会话，实现对话持久化。
    """
    full_text = ""
    try:
        async for ev, payload in llm_service.chat_stream(db, user, body.message, body.history, ""):
            if ev == "delta":
                full_text += payload
                yield _sse("delta", {"content": payload})
            elif ev == "done":
                full = (payload or {}).get("full") if isinstance(payload, dict) else ""
                if full and not full_text:
                    full_text = full
                yield _sse("done", {"full": full, "source": source})
            elif ev == "error":
                msg = payload if isinstance(payload, str) else str(payload)
                yield _sse("error", {"message": msg[:300]})
                yield _sse("done", {"full": "", "source": source, "had_error": True})
    except Exception as e:  # noqa: BLE001
        try:
            yield _sse("error", {"message": f"流式中断：{e.__class__.__name__}: {str(e)[:120]}"})
            yield _sse("done", {"full": "", "source": source, "had_error": True})
        except Exception:
            pass
    finally:
        _persist_chat(db, user, body, full_text)


@router.post("/chat/stream")
async def chat_stream(
    body: ChatIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """对话流式端点（SSE 协议）。"""
    eff = llm_service.resolve_effective(db, user)
    source = eff.get("source", "mock")
    return StreamingResponse(
        _run(db, user, body, source),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # Nginx / Cloudflare 反代时不缓冲流式响应
        },
    )