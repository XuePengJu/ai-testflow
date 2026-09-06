"""LLM 服务层（V2.4 FR-I）。

- OpenAICompatClient：httpx 直连 {base_url}/chat/completions（OpenAI 兼容协议）
- resolve_effective：模型解析优先级 = 用户配置 > 平台默认 > 服务器环境变量 > mock 兜底
- Key 落库加密：复用 crypto 的 AES-256-GCM 原语，密钥 HKDF(JWT_SECRET, info=llm-at-rest:<owner>)
- 两段式视觉理解：图片（data: URI / http URL）先交视觉模型转文字描述，再进文本模型
- chat_stream：流式输出（前端打字机体验），yield 内容片段（prompt 强制 <think>…</think> 切分）
"""
import asyncio
import asyncio
import json
import re
import time

import httpx
from sqlalchemy.orm import Session

from app.core import config, crypto
from app.core.providers import provider_label, FREE_PROVIDERS
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

    def chat_stream(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192, timeout: float = _TIMEOUT):
        """流式 chat_completions：yield (event, payload)。

        event in {"delta", "done", "error"}：
        - delta：payload=str，本次增量内容（前端用 <think> 标签自行切分思考/回复）
        - done： payload={"full": str}，本次完整内容
        - error：payload=str，错误描述

        实现：httpx.stream() 逐行解析 SSE，按 data: {...} 取 choices[0].delta.content。
        """
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens, "stream": True,
        }
        buf = ""
        full = ""
        try:
            with httpx.Client(timeout=timeout) as hc:
                with hc.stream("POST", url, headers=headers, json=payload) as r:
                    if r.status_code != 200:
                        body = r.read().decode("utf-8", errors="ignore")[:300]
                        yield ("error", f"HTTP {r.status_code}：{body}")
                        return
                    for line in r.iter_lines():
                        if not line:
                            continue
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                obj = json.loads(data)
                                delta = obj["choices"][0].get("delta", {}).get("content") or ""
                            except (KeyError, IndexError, ValueError):
                                continue
                            if delta:
                                full += delta
                                yield ("delta", delta)
        except httpx.HTTPError as e:
            yield ("error", f"网络错误：{e.__class__.__name__}")
            return
        except Exception as e:  # noqa: BLE001
            yield ("error", f"流式中断：{e.__class__.__name__}: {str(e)[:80]}")
            return
        # 防御：去除混入的 <think> 思考过程（虽然前端会切分，但完整存档里也清掉避免污染）
        clean = re.sub(r"<think>.*?</think>", "", full, flags=re.S).strip()
        yield ("done", {"full": full, "clean": clean})

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


def _server_key(provider: str) -> str:
    """免费厂商(modelscope / zhipu)的服务端兜底 Key。

    平台默认或个人配置选择免费模型且未填 Key 时，由平台环境变量提供 Key，
    界面无需暴露密钥。
    """
    if provider == "modelscope":
        return config.MODELSCOPE_API_KEY
    if provider in ("zhipu", "zhipu_coding"):
        return config.ZHIPU_API_KEY
    return ""


# ============ 生效配置解析 ============

def _row_to_cfg(row: LLMConfig, owner_id: int) -> dict:
    try:
        api_key = decrypt_key(row.api_key_enc, owner_id) if row.api_key_enc else ""
    except ValueError:
        api_key = ""   # JWT_SECRET 变更等导致解不开 → 视为无 Key
    # 免费厂商且未存 Key → 由服务端环境变量兜底（界面不暴露密钥）
    if not api_key and row.provider in FREE_PROVIDERS:
        api_key = _server_key(row.provider)
    return {
        "provider": row.provider,
        "provider_label": provider_label(row.provider),
        "base_url": row.base_url,
        "model": row.model,
        "api_key": api_key,
    }


def resolve_effective(db: Session, user: User | None) -> dict:
    """返回 {"source", "text", "vision"}。

    source: user（用户自配） / platform（admin 平台默认） / env（百炼 .env 兜底） / mock
    text / vision: None 表示该槽位不可用（text 为 None → mock 生成）。

    生效优先级：
        用户自配(user) > 平台默认(platform，免费厂商由服务端 Key 兜底) > 百炼 env > mock
    平台默认对免费厂商(魔搭 / GLM)可不填 Key，解析时由服务端环境变量补全，
    因此"平台默认"是模型选择的唯一权威来源，徽标与模型管理页展示完全一致。
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

    # 文本槽优先级：用户自配 > 平台默认 > 百炼 env 兜底
    text_cfg, src = None, None
    if "text" in own:
        text_cfg, src = _row_to_cfg(own["text"], user.id), "user"
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

    # text 槽有 Key 才真正可用（免费厂商已由服务端 Key 兜底）
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


# ============ 对话流式 chat_stream（前端打字机体验） ============

_SYSTEM_PROMPT = """你是 Buddy，资深软件测试工程师，专精测试用例设计。

【输出格式要求】严格按下述结构：
<think>
- 需求理解：...
- 覆盖维度：...
- 风险点 / 边界条件：...
</think>
（正式回复，正面回答用户，先复述需求理解，再列出覆盖维度，每条简短解释，最后给 1-3 个澄清问题）

要求：
1. 思考过程用 <think>...</think> 包裹，可折叠不打扰用户阅读正式回复
2. 正式回复要可直接生成测试用例，澄清问题要具体（如"主要覆盖正面/反面/边界？"）
3. 简洁专业，避免客套；用 markdown 列表"""


def _build_messages(user_text: str, history: list | None, attached_text: str) -> list:
    """组装 messages：system + history + 当前用户消息（附文档摘要在消息里）。"""
    msgs = [{"role": "system", "content": _SYSTEM_PROMPT}]
    if history:
        msgs.extend(history[-10:])  # 截断最多 10 轮避免超 token
    user_content = user_text or "（用户仅发送了附件）"
    if attached_text:
        user_content += f"\n\n【附件摘要】\n{attached_text[:3000]}"
    msgs.append({"role": "user", "content": user_content})
    return msgs


def _mock_thinking_for(user_text: str) -> str:
    """mock 模式：根据用户输入动态拼一段思考过程。"""
    u = (user_text or "").strip()
    if not u:
        u = "（无文字描述，仅附件）"
    short = u[:60] + ("…" if len(u) > 60 else "")
    return (
        "- 需求理解：用户描述「" + short + "」\n"
        "- 覆盖维度：输入边界 / 错误处理 / 权限控制 / 数据一致性 / 异常兼容\n"
        "- 风险点：未明确业务类型（功能 / 接口 / App），未明确测试范围与通过标准\n"
    )


def _mock_reply_for(user_text: str) -> str:
    """mock 模式：正式回复模板。"""
    u = (user_text or "").strip() or "你描述的场景"
    return (
        f"好的，关于「{u[:30]}{'…' if len(u) > 30 else ''}」，我先理一下：\n\n"
        "**可能覆盖的测试维度：**\n"
        "1. **输入边界**：空值、最大长度、特殊字符、emoji、SQL 注入\n"
        "2. **错误处理**：异常返回、错误码覆盖、错误提示文案\n"
        "3. **权限控制**：未登录、不同角色、跨用户访问\n"
        "4. **数据一致性**：并发修改、删除后引用、外键约束\n"
        "5. **异常兼容**：网络中断、超时、重试机制\n\n"
        "为了生成更精准的用例，能否告诉我：\n"
        "1. 这属于哪类系统（Web 功能 / 接口 / App 端）？\n"
        "2. 需要覆盖哪些角色（管理员 / 普通用户 / 访客）？\n"
        "3. 有没有特定业务规则（如金额上限、审批流）？"
    )


async def _mock_stream_chunks(user_text: str):
    """mock 模式流式切片：think 段 + reply 段，逐段 yield。"""
    thinking = _mock_thinking_for(user_text)
    reply = _mock_reply_for(user_text)
    full = f" 思考\n{thinking}\n思考\n{reply}"
    chunk_size = 12
    i = 0
    while i < len(full):
        piece = full[i:i+chunk_size]
        yield ("delta", piece)
        await asyncio.sleep(0.04)
        i += chunk_size
    yield ("done", {"full": full, "clean": reply})

async def chat_stream(db: Session, user: User | None, user_text: str, history: list | None, attached_text: str = ""):
    """对话流式生成器（async）。

    设计：对话场景以 demo 体验优先。平台默认/环境变量兜底是免费或公共模型，
    可能很慢或不稳定，因此**聊天默认走 mock 流式**，只有用户明确自配真实模型时才走真实调用。
    """
    eff = resolve_effective(db, user)
    use_real = (eff.get("source") == "user" and eff.get("text") is not None)
    messages = _build_messages(user_text or "", history, attached_text)
    if not use_real:
        async for ev in _mock_stream_chunks(user_text or ""):
            yield ev
        return
    cfg = eff["text"]
    client = OpenAICompatClient(cfg["base_url"], cfg["api_key"], cfg["model"])
    try:
        # 真实模型是同步 generator（httpx 同步流式），在线程池里跑
        loop = asyncio.get_running_loop()
        sync_gen = client.chat_stream(messages, timeout=8.0)
        while True:
            ev = await loop.run_in_executor(None, lambda: next(sync_gen, None))
            if ev is None:
                break
            yield ev
    except LLMError as e:
        yield ("error", str(e))
        async for ev in _mock_stream_chunks(user_text or ""):
            yield ev


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
