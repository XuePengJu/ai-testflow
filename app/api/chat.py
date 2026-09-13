"""对话流式端点（SSE，前端打字机体验）。

- POST /api/chat/stream
  入参：{ message, history?, attached_text? }
  响应：text/event-stream，逐 event 输出原始 delta / 完成 / 错误

SSE 协议：
  event: delta
  data: {"content": "..."}

  event: think
  data: {"content": "..."}         思考增量（reasoning_content 类字段），直接进思考面板

  event: notice
  data: {"message": "..."}         非终态提示（降级/中断），流会继续

  event: done
  data: {"full": "...", "source": "user|platform|env|mock", "thinking": "..."}

  event: error
  data: {"message": "..."}

思考有两个来源：① 上游独立字段 → 走 think 事件；② 老模型混在正文里的
<think>/<thinking> 标签 或 mock 的 思考...思考 → 前端自行切分。

流式响应 Content-Type 是 text/event-stream，不会被 ApiCryptoMiddleware 加密
（中间件只加密 application/json）。
"""
import json
import re

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.files import load_chat_file
from app.core.db import get_db
from app.core.utils import utcnow
from app.models.conversation import Conversation, Message
from app.models.task import Task
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
    - 真实模型：<think>...</think> 或 <thinking>...</thinking>
    - mock： 思考 ... 思考 分隔符
    """
    if not full:
        return "", ""
    m = re.search(r"<think(?:ing)?>([\s\S]*?)</think(?:ing)?>", full)
    if m:
        thinking = m.group(1).strip()
        reply = re.sub(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>", "", full).strip()
        return thinking, reply
    m = re.search(r" 思考([\s\S]*?)思考", full)
    if m:
        thinking = m.group(1).strip()
        reply = (full[:m.start()] + full[m.end():]).strip()
        return thinking, reply
    return "", full.strip()


def _persist_chat(db: Session, user: User | None, body: ChatIn, full_text: str,
                  think_text: str = "") -> None:
    """流式结束后把 user + assistant 消息落库到会话（对话记录持久化）。

    think_text 是上游独立字段（reasoning_content）累积的思考内容；
    为空时回落到从正文里切 <think> 标签（老模型形态）。思考必须单独存，
    否则历史消息的思考面板会空。
    """
    if not user or not body.conversation_id:
        return
    conv = db.get(Conversation, body.conversation_id)
    if not conv or conv.user_id != user.id:
        return
    thinking, reply = _split_think(full_text)
    if think_text:
        thinking = f"{think_text.strip()}\n\n{thinking}".strip() if thinking else think_text.strip()
    db.add(Message(conversation_id=conv.id, role="user", content=body.message))
    db.add(Message(conversation_id=conv.id, role="assistant",
                   content=reply, thinking=thinking))
    conv.updated_at = utcnow()
    db.commit()


def _build_task_summary(db: Session, task_id: str, user: User | None) -> str:
    """迭代补充模式：构建任务用例的压缩摘要，附在对话上下文里。"""
    if not task_id:
        return ""
    task = db.get(Task, task_id)
    if not task or (user and task.user_id != user.id and user.role != "admin"):
        return ""
    if not task.cases_json:
        return f"【任务上下文】任务「{task.name}」暂无已生成用例"
    try:
        cases = json.loads(task.cases_json)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(cases, list) or not cases:
        return ""
    from collections import Counter
    by_module = Counter(c.get("module") or "未分类" for c in cases)
    by_type = Counter(c.get("case_type") or "正向" for c in cases)
    lines = [f"【任务上下文】正在迭代任务「{task.name}」，已有 {len(cases)} 条用例："]
    lines.append(f"类型分布：{dict(by_type)}")
    for mod, cnt in by_module.items():
        titles = [c.get("title", "")[:30] for c in cases if (c.get("module") or "未分类") == mod][:8]
        lines.append(f"- 模块「{mod}」（{cnt} 条）：{'、'.join(titles)}")
    return "\n".join(lines)[:2000]


def _build_attachment_context(loaded: tuple[str, str] | None) -> str:
    """把上传附件的抽取文本包装成注入上下文。

    loaded = (文件名, 抽取文本)，由 load_chat_file 读回（None 表示没有附件 /
    缓存已过期）。抽不出文字（扫描件 pdf / 空文档）时给一句说明，避免模型凭空猜测。
    """
    if not loaded:
        return ""
    name, text = loaded
    if not text.strip():
        return f"【附件】用户上传了文档《{name}》，但未能提取到文字内容（可能是扫描件或空文档）"
    return f"【附件《{name}》内容】\n{text}"


async def _run(db: Session, user: User | None, body: ChatIn, source: str):
    """异步 SSE 事件流 generator。

    上游 llm_service.chat_stream 输出 delta / think / notice / done / error，
    本函数做翻译转发（think 单独走 think 事件，与正文分开，避免思考混进正文）。
    流式结束后（finally）把 user + assistant 消息落库到会话，实现对话持久化。
    """
    full_text = ""
    think_text = ""
    task_summary = _build_task_summary(db, body.task_id, user)
    # 附件文本由 POST /api/files 预先抽取落盘，这里按 file_id 读回
    attached = load_chat_file(body.file_id) if body.file_id else None
    attach_name = attached[0] if attached else ""
    # 任务摘要 + 上传附件统一走 attached_text 注入（_build_messages 里拼到用户消息之后）
    context = "\n\n".join(x for x in (task_summary, _build_attachment_context(attached)) if x)
    try:
        async for ev, payload in llm_service.chat_stream(
            db, user, body.message, body.history, context, attach_name, body.thinking
        ):
            if ev == "delta":
                full_text += payload
                yield _sse("delta", {"content": payload})
            elif ev == "think":
                # 上游独立字段（reasoning_content）的思考增量，前端直接进思考面板
                think_text += payload
                yield _sse("think", {"content": payload})
            elif ev == "done":
                full = (payload or {}).get("full") if isinstance(payload, dict) else ""
                if full and not full_text:
                    full_text = full
                up_think = (payload or {}).get("thinking") if isinstance(payload, dict) else ""
                if up_think and not think_text:
                    think_text = up_think
                yield _sse("done", {"full": full, "source": source, "thinking": think_text})
            elif ev == "notice":
                # 降级/中断提示：非终态错误，前端以黄色提示条展示，流会继续
                yield _sse("notice", {"message": str(payload)[:300]})
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
        _persist_chat(db, user, body, full_text, think_text)


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