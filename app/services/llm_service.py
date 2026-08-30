"""LLM 服务层（V2.4 FR-I）。

- OpenAICompatClient：httpx 直连 {base_url}/chat/completions（OpenAI 兼容协议）
- resolve_effective：模型解析优先级 = 用户配置 > 平台默认 > 服务器环境变量 > mock 兜底
- Key 落库加密：复用 crypto 的 AES-256-GCM 原语，密钥 HKDF(JWT_SECRET, info=llm-at-rest:<owner>)
- 两段式视觉理解：图片（data: URI / http URL）先交视觉模型转文字描述，再进文本模型
"""
import json
import re

import httpx
from sqlalchemy.orm import Session

from app.core import config, crypto
from app.core.providers import provider_label
from app.models.llm_config import LLMConfig
from app.models.user import User

# 服务器环境变量兜底（兼容老部署：.env 里的 DASHSCOPE_API_KEY）
_BAILIAN_COMPAT = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_TIMEOUT = 60            # 生成用例的常规超时
_VISION_TIMEOUT = 90     # 视觉模型看图慢一些


class LLMError(Exception):
    """LLM 调用失败（网络 / 鉴权 / 响应异常）。"""


def _post_chat(base_url: str, api_key: str, payload: dict, timeout: float = _TIMEOUT) -> dict:
    """POST {base_url}/chat/completions，返回解析后的 JSON。单点便于测试 mock。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=timeout) as hc:
            r = hc.post(url, headers=headers, json=payload)
    except httpx.HTTPError as e:
        raise LLMError(f"网络错误：{e.__class__.__name__}") from e
    if r.status_code != 200:
        detail = ""
        try:
            detail = (r.json().get("error") or {}).get("message", "")
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        raise LLMError(f"HTTP {r.status_code}：{detail or '调用失败'}")
    try:
        return r.json()
    except ValueError as e:
        raise LLMError("响应不是合法 JSON") from e


class OpenAICompatClient:
    """OpenAI 兼容客户端。generate() 与旧 BailianClient 同签名，管线可直接替换。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        if not base_url or not api_key or not model:
            raise LLMError("模型配置不完整（缺 base_url / api_key / model）")
        self.base_url = base_url
        self.api_key = api_key
        self.model = model

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192) -> str:
        data = _post_chat(self.base_url, self.api_key, {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
        })
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("响应缺少 choices[0].message.content") from e
        if not isinstance(content, str):
            # 兼容部分厂商返回 content 为分段列表的形态
            if isinstance(content, list):
                content = "".join(
                    seg.get("text", "") for seg in content if isinstance(seg, dict)
                )
            else:
                content = str(content)
        # 防御：部分厂商会把思考过程以 <think>...</think> 混入 content，去除避免污染生成结果
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        return content

    def generate(self, prompt: str) -> str:
        """与旧 BailianClient.generate 同签名：prompt 进、文本出。"""
        return self.chat([{"role": "user", "content": prompt}])

    def describe_image(self, image_url: str, hint: str = "") -> str:
        """视觉理解：图片 + 指令 → 中文文字描述（两段式第一步）。"""
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": hint or "请用中文客观描述这张软件相关截图的内容，"
                    "重点说明界面元素、字段、按钮、流程或数据，供测试用例设计参考。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }]
        return self.chat(messages, temperature=0.1, max_tokens=1024)


# ============ Key 落库加密 ============

def _at_rest_key(owner_id: int) -> str:
    """静态加密密钥：HKDF(JWT_SECRET, info=llm-at-rest:<owner_id>)，与传输加密密钥相互独立。"""
    return crypto.derive_key(f"llm-at-rest:{owner_id}")


def encrypt_key(api_key: str, owner_id: int) -> str:
    return crypto.encrypt_obj({"k": api_key}, _at_rest_key(owner_id))


def decrypt_key(api_key_enc: str, owner_id: int) -> str:
    obj = crypto.decrypt_obj(api_key_enc, _at_rest_key(owner_id))
    return obj["k"]


def mask_key(api_key: str) -> str:
    if not api_key:
        return ""
    tail = api_key[-4:] if len(api_key) > 4 else "****"
    return f"****{tail}"


# ============ 生效配置解析 ============

def _row_to_cfg(row: LLMConfig, owner_id: int) -> dict:
    try:
        api_key = decrypt_key(row.api_key_enc, owner_id) if row.api_key_enc else ""
    except ValueError:
        api_key = ""   # JWT_SECRET 变更等导致解不开 → 视为无 Key
    return {
        "provider": row.provider,
        "provider_label": provider_label(row.provider),
        "base_url": row.base_url,
        "model": row.model,
        "api_key": api_key,
    }


def resolve_effective(db: Session, user: User | None) -> dict:
    """返回 {"source", "text", "vision"}。

    source: user（用户自配） / platform（admin 平台默认） / env（服务器 .env 兜底） / mock
    text / vision: None 表示该槽位不可用（text 为 None → mock 生成）。
    """
    if user is not None:
        own = {r.slot: r for r in db.query(LLMConfig).filter(LLMConfig.user_id == user.id).all()}
    else:
        own = {}
    platform = {r.slot: r for r in db.query(LLMConfig).filter(LLMConfig.user_id == 0).all()}

    def pick(slot: str) -> tuple[dict | None, str | None]:
        if slot in own:
            return _row_to_cfg(own[slot], user.id), "user"
        if slot in platform:
            return _row_to_cfg(platform[slot], 0), "platform"
        return None, None

    # 生效优先级（方案3：魔搭 env 兜底 高于 平台默认 GLM）：
    #   用户自配(user) > 魔搭 env 兜底 > 平台默认(DB, GLM) > 百炼 env 兜底 > mock
    text_cfg, src = None, None
    if "text" in own:
        text_cfg, src = _row_to_cfg(own["text"], user.id), "user"
    elif config.MODELSCOPE_API_KEY:
        text_cfg = {
            "provider": "modelscope",
            "provider_label": provider_label("modelscope"),
            "base_url": config.MODELSCOPE_BASE_URL,
            "model": config.MODELSCOPE_MODEL,
            "api_key": config.MODELSCOPE_API_KEY,
        }
        src = "env"
    elif "text" in platform:
        text_cfg, src = _row_to_cfg(platform["text"], 0), "platform"
    elif config.DASHSCOPE_API_KEY:
        text_cfg = {
            "provider": "bailian",
            "provider_label": provider_label("bailian"),
            "base_url": _BAILIAN_COMPAT,
            "model": config.MODEL_NAME or "qwen-plus",
            "api_key": config.DASHSCOPE_API_KEY,
        }
        src = "env"

    vision_cfg, vsrc = pick("vision")

    if text_cfg is None:
        return {"source": "mock", "text": None, "vision": None}

    # text 槽有 Key 才真正可用
    if not text_cfg.get("api_key"):
        return {"source": "mock", "text": None, "vision": None}

    return {
        "source": src or "platform",
        "text": text_cfg,
        "vision": vision_cfg if (vision_cfg and vision_cfg.get("api_key")) else None,
    }


def public_view(cfg: dict | None) -> dict | None:
    """对外展示形态（不含 Key）。"""
    if not cfg:
        return None
    return {k: cfg[k] for k in ("provider", "provider_label", "base_url", "model")}


# ============ 视觉增强（两段式第一步） ============

_IMG_MD = re.compile(r"!\[([^\]]*)\]\((\s*(?:data:image/|https?://)[^)\s]+)\)")
_IMG_HTTP = re.compile(r'(?:data:image/|https?://)[^\s)"\']+', re.I)


def extract_image_refs(text: str) -> list[str]:
    """提取图片引用：markdown 图片语法（data: URI / http URL）+ 裸贴的图片 URL / data URI。"""
    refs = [m.group(2).strip() for m in _IMG_MD.finditer(text)]
    refs += _IMG_HTTP.findall(text)   # 裸贴的引用；md 里的重复项由下方去重消除
    # 去重 + 上限（防止文档几十张图把视觉模型跑爆）
    seen, out = set(), []
    for u in refs:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:8]


def vision_enrich(text: str, vision_client: OpenAICompatClient) -> tuple[str, int]:
    """把文本中的图片引用替换为视觉模型产出的文字描述。返回 (新文本, 处理图片数)。"""
    refs = extract_image_refs(text)
    if not refs:
        return text, 0
    for ref in refs:
        try:
            desc = vision_client.describe_image(ref)
            desc = " ".join(desc.split())[:1500]
            block = f"\n\n【截图解读】{desc}\n"
            text = text.replace(ref, block)
        except LLMError:
            # 单图失败不阻断流程，保留原引用
            continue
    return text, len(refs)


# ============ 连通测试 ============

def test_connectivity(base_url: str, api_key: str, model: str) -> dict:
    """发一条最小请求验证 Key / 端点 / 模型可用。"""
    import time
    t0 = time.time()
    try:
        reply = OpenAICompatClient(base_url, api_key, model).chat(
            [{"role": "user", "content": "回复「ok」两个字即可。"}],
            temperature=0, max_tokens=16,
        )
        return {
            "ok": True,
            "latency_ms": round((time.time() - t0) * 1000),
            "reply": (reply or "")[:80],
        }
    except LLMError as e:
        return {"ok": False, "latency_ms": round((time.time() - t0) * 1000), "error": str(e)[:300]}
