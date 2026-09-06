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

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.db import get_db
from app.models.user import User
from app.schemas.llm_config import ChatIn
from app.services import llm_service

router = APIRouter()


def _sse(event: str, data: dict) -> bytes:
    """构造一个 SSE 事件（UTF-8 JSON）。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


async def _run(db: Session, user: User | None, body: ChatIn, source: str):
    """异步 SSE 事件流 generator。

    上游 llm_service.chat_stream 现在输出原始 delta，本函数只做简单转发，
    由前端自行解析 思考...思考 切分思考面板与回复面板。
    """
    try:
        async for ev, payload in llm_service.chat_stream(db, user, body.message, body.history, ""):
            if ev == "delta":
                yield _sse("delta", {"content": payload})
            elif ev == "done":
                full = (payload or {}).get("full") if isinstance(payload, dict) else ""
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