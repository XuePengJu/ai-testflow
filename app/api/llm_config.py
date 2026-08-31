"""LLM 模型配置端点（V2.4 FR-I）。

- GET  /api/llm/providers        厂商预设列表（登录即可见）
- GET  /api/llm/effective        当前生效模型（任务页展示）
- GET/PUT/DELETE /api/llm/config[/{slot}]  个人配置（user/admin；guest 403）
- GET/PUT/DELETE /api/llm/platform-config[/{slot}]  平台默认（GET 所有登录用户只读；PUT/DELETE admin）
- POST /api/llm/test             连通测试（不落库）
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.db import get_db
from app.core.providers import PROVIDERS, is_provider, FREE_PROVIDERS
from app.models.llm_config import LLMConfig
from app.models.user import User
from app.schemas.llm_config import LLMConfigIn, LLMConfigOut, LLMTestIn
from app.services import llm_service

router = APIRouter()


def _to_out(row: LLMConfig) -> LLMConfigOut:
    return LLMConfigOut(
        slot=row.slot, provider=row.provider, base_url=row.base_url,
        model=row.model, api_key_masked=(f"****{row.api_key_tail}" if row.api_key_tail else ""),
    )


def _get_row(db: Session, owner_id: int, slot: str) -> LLMConfig | None:
    return (
        db.query(LLMConfig)
        .filter(LLMConfig.user_id == owner_id, LLMConfig.slot == slot)
        .first()
    )


def _upsert(db: Session, owner_id: int, body: LLMConfigIn) -> LLMConfig:
    if not is_provider(body.provider):
        raise HTTPException(400, detail="未知的厂商预设")
    row = _get_row(db, owner_id, body.slot)
    if not row:
        row = LLMConfig(user_id=owner_id, slot=body.slot)
        db.add(row)

    row.provider = body.provider
    row.base_url = body.base_url.strip()
    row.model = body.model.strip()

    if body.provider in FREE_PROVIDERS:
        # 免费厂商：以服务端环境变量 Key 为准；仅当用户显式提供 Key 时才存自定义 Key
        if body.api_key is not None and body.api_key.strip():
            k = body.api_key.strip()
            row.api_key_enc = llm_service.encrypt_key(k, owner_id)
            row.api_key_tail = k[-4:] if len(k) > 4 else "****"
        else:
            row.api_key_enc = ""      # 清空旧 Key，解析时由服务端 Key 兜底
            row.api_key_tail = ""
    elif body.api_key is None:
        pass                          # 非免费厂商未提供 → 保留原 Key
    elif body.api_key.strip() == "":
        row.api_key_enc = ""          # 显式传空 → 清除
        row.api_key_tail = ""
    else:
        k = body.api_key.strip()
        row.api_key_enc = llm_service.encrypt_key(k, owner_id)
        row.api_key_tail = k[-4:] if len(k) > 4 else "****"

    # text 槽必须带 Key 才有意义（保存后进行校验，给用户明确提示）
    db.commit()
    db.refresh(row)
    return row


# ---------- 厂商预设 ----------

@router.get("/llm/providers")
def list_providers(user: User = Depends(get_current_user)):
    return PROVIDERS


# ---------- 当前生效模型 ----------

@router.get("/llm/effective")
def get_effective(db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    eff = llm_service.resolve_effective(db, user)
    return {
        "source": eff["source"],
        "text": llm_service.public_view(eff["text"]),
        "vision": llm_service.public_view(eff["vision"]),
    }


# ---------- 个人配置 ----------

@router.get("/llm/config")
def get_config(db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    rows = db.query(LLMConfig).filter(LLMConfig.user_id == user.id).all()
    return [_to_out(r) for r in rows]


@router.put("/llm/config", response_model=LLMConfigOut)
def put_config(body: LLMConfigIn,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    row = _upsert(db, user.id, body)
    if body.slot == "text" and not row.api_key_enc:
        # 免费厂商且服务端已配 Key → 允许不填 Key（平台提供）
        if not (body.provider in FREE_PROVIDERS and llm_service._server_key(body.provider)):
            raise HTTPException(400, detail="默认文本模型必须配置 API Key（免费模型由平台提供时可不填）")
    return _to_out(row)


@router.delete("/llm/config/{slot}", status_code=204)
def delete_config(slot: str,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    if slot not in ("text", "vision"):
        raise HTTPException(400, detail="slot 只能是 text 或 vision")
    row = _get_row(db, user.id, slot)
    if row:
        db.delete(row)
        db.commit()
    return None


# ---------- 平台默认（GET 所有登录用户只读可见；PUT/DELETE 仅 admin） ----------

@router.get("/llm/platform-config", response_model=list[LLMConfigOut])
def get_platform(db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    rows = db.query(LLMConfig).filter(LLMConfig.user_id == 0).all()
    return [_to_out(r) for r in rows]


@router.put("/llm/platform-config", response_model=LLMConfigOut)
def put_platform(body: LLMConfigIn,
                 db: Session = Depends(get_db),
                 admin: User = Depends(require_admin)):
    row = _upsert(db, 0, body)
    if body.slot == "text" and not row.api_key_enc:
        # 免费厂商且服务端已配 Key → 允许不填 Key（平台提供）
        if not (body.provider in FREE_PROVIDERS and llm_service._server_key(body.provider)):
            raise HTTPException(400, detail="平台默认文本模型必须配置 API Key（免费模型由平台提供时可不填）")
    return _to_out(row)


@router.delete("/llm/platform-config/{slot}", status_code=204)
def delete_platform(slot: str,
                    db: Session = Depends(get_db),
                    admin: User = Depends(require_admin)):
    if slot not in ("text", "vision"):
        raise HTTPException(400, detail="slot 只能是 text 或 vision")
    row = _get_row(db, 0, slot)
    if row:
        db.delete(row)
        db.commit()
    return None


# ---------- 生效模型可用性测试（不落库） ----------

_ERR_LABEL = {
    "auth": "API Key 已过期或无效",
    "rate_limit": "限流 · 请稍后再试",
    "quota": "额度已用尽",
    "network": "网络错误 · 请稍后再试",
    "server": "模型服务异常 · 请稍后再试",
    "not_configured": "未配置或未生效",
    "other": "调用失败",
}


def _classify_error(msg: str) -> str:
    """从 LLMError 文本归类错误类型（HTTP 状态码优先）。"""
    import re
    m = re.search(r"HTTP (\d{3})", msg)
    if m:
        code = int(m.group(1))
        if code in (401, 403):
            return "auth"
        if code == 429:
            return "rate_limit"
        if code == 402:
            return "quota"
        if code >= 500:
            return "server"
        return "other"
    if "网络错误" in msg or "Timeout" in msg or "timed out" in msg.lower():
        return "network"
    return "other"


@router.post("/llm/test-default/{slot}")
def test_default_slot(slot: str,
                      db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """测试当前生效模型可用性（服务端解析配置并持有 Key，客户端无需提供；结果不落库）。

    - 所有登录角色可用（含访客）：解析优先级与 resolve_effective 一致（我的配置 > 平台默认）
    - 错误归类：auth / rate_limit / quota / network / server / not_configured / other
    """
    if slot not in ("text", "vision"):
        raise HTTPException(400, detail="slot 只能是 text 或 vision")
    eff = llm_service.resolve_effective(db, user)
    cfg = eff["text"] if slot == "text" else eff["vision"]
    if not cfg:
        return {"ok": False, "err_type": "not_configured",
                "error_label": _ERR_LABEL["not_configured"],
                "error": "该槽位未配置或未生效（服务器未配对应厂商 Key）", "model": None}
    result = llm_service.test_connectivity(cfg["base_url"], cfg["api_key"], cfg["model"])
    out = {**result, "model": cfg["model"], "provider_label": cfg["provider_label"]}
    if not out.get("ok"):
        et = _classify_error(out.get("error", ""))
        out["err_type"] = et
        out["error_label"] = _ERR_LABEL.get(et, _ERR_LABEL["other"])
    return out


# ---------- 连通测试 ----------

@router.post("/llm/test")
def test_llm(body: LLMTestIn,
             db: Session = Depends(get_db),
             user: User = Depends(get_current_user)):
    """测试连通（不落库）。api_key 留空 → 复用已保存的 Key（个人两槽 → admin 平台两槽）。"""
    api_key = body.api_key.strip()
    if not api_key:
        owner_ids = [user.id] + ([0] if user.role == "admin" else [])
        for oid in owner_ids:
            for slot in ("vision", "text"):
                row = _get_row(db, oid, slot)
                if row and row.api_key_enc:
                    try:
                        api_key = llm_service.decrypt_key(row.api_key_enc, oid)
                        break
                    except ValueError:
                        continue
            if api_key:
                break
    if not api_key:
        raise HTTPException(400, detail="请填写 API Key（或先保存配置后再测试）")
    if not body.base_url.strip() or not body.model.strip():
        raise HTTPException(400, detail="base_url 与 model 不能为空")
    return llm_service.test_connectivity(body.base_url.strip(), api_key, body.model.strip())
