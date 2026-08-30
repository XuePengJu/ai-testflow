"""LLM 模型配置端点（V2.4 FR-I）。

- GET  /api/llm/providers        厂商预设列表（登录即可见）
- GET  /api/llm/effective        当前生效模型（任务页展示）
- GET/PUT/DELETE /api/llm/config[/{slot}]  个人配置（user/admin；guest 403）
- GET/PUT/DELETE /api/llm/platform-config[/{slot}]  平台默认（admin 专属）
- POST /api/llm/test             连通测试（不落库）
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.db import get_db
from app.core.providers import PROVIDERS, is_provider
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

    if body.api_key is None:
        pass                          # 未提供 → 保留原 Key
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
    if user.role == "guest":
        raise HTTPException(403, detail="访客使用平台默认模型，注册后可配置自己的 API Key")
    rows = db.query(LLMConfig).filter(LLMConfig.user_id == user.id).all()
    return [_to_out(r) for r in rows]


@router.put("/llm/config", response_model=LLMConfigOut)
def put_config(body: LLMConfigIn,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    if user.role == "guest":
        raise HTTPException(403, detail="访客使用平台默认模型，注册后可配置自己的 API Key")
    row = _upsert(db, user.id, body)
    if body.slot == "text" and not row.api_key_enc:
        raise HTTPException(400, detail="默认文本模型必须配置 API Key")
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


# ---------- 平台默认（admin） ----------

@router.get("/llm/platform-config", response_model=list[LLMConfigOut])
def get_platform(db: Session = Depends(get_db),
                 admin: User = Depends(require_admin)):
    rows = db.query(LLMConfig).filter(LLMConfig.user_id == 0).all()
    return [_to_out(r) for r in rows]


@router.put("/llm/platform-config", response_model=LLMConfigOut)
def put_platform(body: LLMConfigIn,
                 db: Session = Depends(get_db),
                 admin: User = Depends(require_admin)):
    row = _upsert(db, 0, body)
    if body.slot == "text" and not row.api_key_enc:
        raise HTTPException(400, detail="平台默认文本模型必须配置 API Key")
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
