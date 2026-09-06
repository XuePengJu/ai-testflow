"""对话流式端点（SSE，前端打字机体验）。

- POST /api/chat/stream
  入参：{ message, history?, attached_text? }
  响应：text/event-stream，逐 event 输出思考 / 回复 / 完成 / 错误

SSE 协议：
  event: thinking
  data: {"delta": "..."}

  event: reply
  data: {"delta": "..."}

  event: done
  data: {"full_thinking": "...", "full_reply": "...", "source": "user|platform|env|mock"}

  event: error
  data: {"message": "..."}

前端解析 delta 时按 <think>...</think> 切分到两个面板；后端不再区分字段，
是因为模型厂商（DeepSeek/Qwen 等）原生的"思考/回复"分隔不统一，
用 prompt 强制 <think> 切分是最兼容的方案。

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
from app.models.user import User
from app.schemas.llm_config import ChatIn
from app.services import llm_service

router = APIRouter()


def _sse(event: str, data: dict) -> bytes:
    """构造一个 SSE 事件（UTF-8 JSON）。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _split_think(text: str) -> tuple[str, str]:
    """从累积文本里切出 <think>...</think> 之外的正文。

    返回 (thinking_inside, outside_reply)。
    """
    m = re.search(r"<think>(.*?)</think>", text, flags=re.S)
    if not m:
        return "", text
    inside = m.group(1)
    outside = (text[: m.start()] + text[m.end():]).strip()
    return inside.strip(), outside


def _run(db: Session, user: User | None, body: ChatIn, source: str):
    """同步 SSE 事件流 generator。

    设计：上游 llm_service.chat_stream 只 yield ("delta", chunk)，
    本函数维护 buffer，每来一段 delta 就重新解析 buffer，
    diff 出新增的「思考」或「回复」片段推送出去。
    """
    buf = ""
    last_thinking = ""
    last_reply = ""

    try:
        for ev, payload in llm_service.chat_stream(db, user, body.message, body.history, ""):
            if ev == "delta":
                buf += payload
                thinking_now, reply_now = _split_think(buf)
                if len(thinking_now) > len(last_thinking) and thinking_now.startswith(last_thinking):
                    delta_t = thinking_now[len(last_thinking):]
                    last_thinking = thinking_now
                    yield _sse("thinking", {"delta": delta_t})
                if len(reply_now) > len(last_reply) and reply_now.startswith(last_reply):
                    delta_r = reply_now[len(last_reply):]
                    last_reply = reply_now
                    yield _sse("reply", {"delta": delta_r})
            elif ev == "done":
                full = (payload or {}).get("full") if isinstance(payload, dict) else ""
                full_thinking, full_reply = _split_think(full) if full else (last_thinking, last_reply)
                yield _sse("done", {
                    "full_thinking": full_thinking,
                    "full_reply": full_reply,
                    "source": source,
                })
            elif ev == "error":
                msg = payload if isinstance(payload, str) else str(payload)
                yield _sse("error", {"message": msg[:300]})
                # 错误后兜底发 done，避免前端永久等
                yield _sse("done", {
                    "full_thinking": last_thinking,
                    "full_reply": last_reply,
                    "source": source,
                    "had_error": True,
                })
    except Exception as e:  # noqa: BLE001
        try:
            yield _sse("error", {"message": f"流式中断：{e.__class__.__name__}: {str(e)[:120]}"})
            yield _sse("done", {
                "full_thinking": last_thinking,
                "full_reply": last_reply,
                "source": source,
                "had_error": True,
            })
        except Exception:
            pass


@router.post("/chat/stream")
def chat_stream(
    body: ChatIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """对话流式端点（thinking + reply 分离推送，SSE 协议）。"""
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