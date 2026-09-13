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

_TIMEOUT = 180           # 生成用例的常规超时（免费模型慢，放宽到 3 分钟）
_VISION_TIMEOUT = 180    # 视觉模型看图慢一些

# 录得不认 enable_thinking 参数的端点（base_url|model）。命中后不再注入，
# 避免每次都先吃一个 400 再重试。
_NO_THINKING_PARAM: set[str] = set()

# 思考内容的字段名：主流厂商各不同，按优先级取第一个非空字符串
_THINK_FIELDS = ("reasoning_content", "reasoning", "thinking")

# 思考标签（真实模型把思考混进正文时的形态）与 mock 的中文分隔符
_THINK_TAG_RE = re.compile(r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>")


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
        # 防御：部分厂商会把思考过程以 <think>/<thinking> 混入 content，去除避免污染生成结果
        content = _THINK_TAG_RE.sub("", content).strip()
        return content

    def generate(self, prompt: str) -> str:
        """与旧 BailianClient.generate 同签名：prompt 进、文本出。"""
        return self.chat([{"role": "user", "content": prompt}])

    def chat_stream(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
                    timeout: float = _TIMEOUT, enable_thinking: bool | None = None):
        """流式 chat_completions：yield (event, payload)。

        event in {"think", "delta", "done", "error"}：
        - think： payload=str，本次增量「思考内容」（来自 reasoning_content 等字段）
        - delta： payload=str，本次增量正文（老模型可能把 <think> 混在正文里，前端会切分）
        - done： payload={"full": str, "clean": str, "thinking": str}
        - error：payload=str，错误描述

        实现：httpx.stream() 逐行解析 SSE，取 choices[0].delta。思考与正文分开取：
        部分厂商把推理放在独立字段（reasoning_content / reasoning / thinking），
        只读 content 会把整段思考丢掉（思考面板恒空）。
        enable_thinking=None 表示不注入（走模型默认）；True/False 显式开关。
        个别端点不认该参数会返回 400，此时自动去掉参数重试一次并记入 _NO_THINKING_PARAM。
        """
        url = self.base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens, "stream": True,
        }
        ep_key = f"{self.base_url}|{self.model}"
        if enable_thinking is not None and ep_key not in _NO_THINKING_PARAM:
            payload["enable_thinking"] = bool(enable_thinking)

        full = ""
        think_full = ""
        for attempt in (0, 1):
            full, think_full = "", ""
            try:
                with httpx.Client(timeout=timeout) as hc:
                    with hc.stream("POST", url, headers=headers, json=payload) as r:
                        if r.status_code != 200:
                            body = r.read().decode("utf-8", errors="ignore")[:300]
                            # 端点不认 enable_thinking（400）→ 去掉参数重试一次，并记住该端点
                            if attempt == 0 and r.status_code == 400 and "enable_thinking" in payload:
                                _NO_THINKING_PARAM.add(ep_key)
                                payload.pop("enable_thinking", None)
                                continue
                            yield ("error", f"HTTP {r.status_code}：{body}")
                            return
                        for line in r.iter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                obj = json.loads(data)
                                d = obj["choices"][0].get("delta") or {}
                            except (KeyError, IndexError, ValueError):
                                continue
                            reason = ""
                            for f in _THINK_FIELDS:
                                v = d.get(f)
                                if isinstance(v, str) and v:
                                    reason = v
                                    break
                            if reason:
                                think_full += reason
                                yield ("think", reason)
                            delta = d.get("content") or ""
                            if delta:
                                full += delta
                                yield ("delta", delta)
            except httpx.HTTPError as e:
                yield ("error", f"网络错误：{e.__class__.__name__}")
                return
            except Exception as e:  # noqa: BLE001
                yield ("error", f"流式中断：{e.__class__.__name__}: {str(e)[:80]}")
                return
            break   # 正常跑完 → 不重试
        # 防御：正文里混入的 <think>/<thinking> 思考块清掉（前端也会切分，这里保完整存档干净）
        clean = _THINK_TAG_RE.sub("", full).strip()
        yield ("done", {"full": full, "clean": clean, "thinking": think_full})

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

# 关闭「深度思考」时使用：不要求模型输出思考过程，直接给正式回复
_SYSTEM_PROMPT_NO_THINK = """你是 Buddy，资深软件测试工程师，专精测试用例设计。

【输出格式要求】直接给出正式回复，先复述需求理解，再列出覆盖维度，每条简短解释，最后给 1-3 个澄清问题。

要求：
1. 不要输出 <think>...</think>、<thinking>...</thinking> 或任何思考过程标记，也不要写「让我想想」这类元话语
2. 正式回复要可直接生成测试用例，澄清问题要具体（如"主要覆盖正面/反面/边界？"）
3. 简洁专业，避免客套；用 markdown 列表"""


def _build_messages(user_text: str, history: list | None, attached_text: str,
                    want_thinking: bool = True) -> list:
    """组装 messages：system + history + 当前用户消息（附加上下文拼在消息里）。

    attached_text 承载两类内容：迭代任务摘要、用户上传文档的正文。
    上限 6000 字与解析链路（_ai_parse_business）保持一致，避免长文档把上下文打爆。
    want_thinking=False 时换用不含思考要求的系统提示词（光靠参数关不掉标签输出）。
    """
    msgs = [{"role": "system", "content": _SYSTEM_PROMPT if want_thinking else _SYSTEM_PROMPT_NO_THINK}]
    if history:
        msgs.extend(history[-10:])  # 截断最多 10 轮避免超 token
    user_content = user_text or "（用户仅发送了附件，请结合下方的文档内容作答）"
    if attached_text:
        user_content += f"\n\n【附加上下文（任务摘要 / 用户上传文档）】\n{attached_text[:6000]}"
    msgs.append({"role": "user", "content": user_content})
    return msgs


def _mock_thinking_for(user_text: str, attach_name: str = "") -> str:
    """mock 模式：思考过程。attach_name 非空表示本轮带了附件。"""
    u = (user_text or "").strip()
    if not u:
        u = "（无文字描述，仅附件）"
    short = u[:60] + ("…" if len(u) > 60 else "")
    lines = []
    if attach_name:
        lines.append(f"- 已读取附件：《{attach_name}》")
    lines += [
        "- 需求理解：用户描述「" + short + "」",
        "- 覆盖维度：输入边界 / 错误处理 / 权限控制 / 数据一致性 / 异常兼容",
        "- 风险点：未明确业务类型（功能 / 接口 / App），未明确测试范围与通过标准",
    ]
    return "\n".join(lines) + "\n"


def _mock_reply_for(user_text: str, history: list | None = None, attach_name: str = "") -> str:
    """mock 模式：正式回复模板。

    attach_name 非空 = 本轮带了附件（服务端已读到文档内容），走「已读文档」模板，
    不再反问业务规则，避免出现「上传了文档还被追问要需求」的错位体验。
    """
    u = (user_text or "").strip() or "你描述的场景"
    if attach_name:
        head = f"已读取附件《{attach_name}》"
        if (user_text or "").strip():
            head += f"，结合你说的「{u[:30]}{'…' if len(u) > 30 else ''}」"
        return (
            head + "，我先理一下：\n\n"
            "**可能覆盖的测试维度：**\n"
            "1. **输入边界**：空值、最大长度、特殊字符、emoji、SQL 注入\n"
            "2. **错误处理**：异常返回、错误码覆盖、错误提示文案\n"
            "3. **权限控制**：未登录、不同角色、跨用户访问\n"
            "4. **数据一致性**：并发修改、删除后引用、外键约束\n"
            "5. **异常兼容**：网络中断、超时、重试机制\n\n"
            "要生成用例的话，点下面的「生成测试用例」我就按这份文档开工。"
        )
    # 检查是否有历史上下文
    has_history = history and len(history) > 0
    last_user_msg = ""
    if has_history:
        # 找最后一条用户消息作为上下文
        for m in reversed(history):
            if m.get('role') == 'user' and m.get('content'):
                last_user_msg = m['content'][:50]
                break
    if has_history and last_user_msg:
        return (
            f"基于之前的对话（关于「{last_user_msg}」），继续补充：\n\n"
            "**可能覆盖的测试维度：**\n"
            "1. **输入边界**：空值、最大长度、特殊字符、emoji、SQL 注入\n"
            "2. **错误处理**：异常返回、错误码覆盖、错误提示文案\n"
            "3. **权限控制**：未登录、不同角色、跨用户访问\n"
            "4. **数据一致性**：并发修改、删除后引用、外键约束\n"
            "5. **异常兼容**：网络中断、超时、重试机制\n"
        )
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


async def _mock_stream_chunks(user_text: str, attach_name: str = "", want_thinking: bool = True):
    """mock 模式流式切片：think 段 + reply 段，逐段 yield。

    want_thinking=False（用户关了「深度思考」）时只吐正式回复，不出思考段，
    与真实模型关闭思考后的表现保持一致。
    """
    thinking = _mock_thinking_for(user_text, attach_name)
    reply = _mock_reply_for(user_text, attach_name=attach_name)
    full = f" 思考\n{thinking}\n思考\n{reply}" if want_thinking else reply
    chunk_size = 12
    i = 0
    while i < len(full):
        piece = full[i:i+chunk_size]
        yield ("delta", piece)
        await asyncio.sleep(0.04)
        i += chunk_size
    yield ("done", {"full": full, "clean": reply})

async def chat_stream(
    db: Session,
    user: User | None,
    user_text: str,
    history: list | None,
    attached_text: str = "",
    attach_name: str = "",
    enable_thinking: bool = True,
):
    """对话流式生成器（async）。

    走向：用户自配(user) 或 平台默认(platform) 有可用文本配置时走真实模型，
    否则走 mock 流式。真实调用失败时降级：未产出内容则追加 mock 兜底，
    已产出半截内容则仅提示中断（避免真假内容混排）。

    attached_text  ：附加上下文（任务用例摘要 / 上传文档正文），拼进用户消息注入模型。
    attach_name    ：附件文件名，仅用于 mock 模式体现「已读到文档」。
    enable_thinking：前端「深度思考」开关。True→注入 enable_thinking 并让 think 事件透传；
                     False→换用无思考要求的系统提示词、不注入参数，且丢弃 think 事件
                     （个别模型不认参数仍会吐推理，丢掉才符合"关了就不显示面板"的预期）。
    """
    eff = resolve_effective(db, user)
    # 平台默认模型（source=platform，免费厂商由服务端 Key 兜底）同样算"已配好模型"。
    # 旧逻辑只认 user，导致平台默认配置被判为未配置、聊天恒走 mock 模板。
    use_real = eff.get("source") in ("user", "platform") and eff.get("text") is not None
    messages = _build_messages(user_text or "", history, attached_text, enable_thinking)
    if not use_real:
        async for ev in _mock_stream_chunks(user_text or "", attach_name, enable_thinking):
            yield ev
        return
    cfg = eff["text"]
    client = OpenAICompatClient(cfg["base_url"], cfg["api_key"], cfg["model"])
    produced = False  # 是否已吐出过真实内容（决定出错时能否安全降级到 mock）
    err = ""
    try:
        # 真实模型是同步 generator（httpx 同步流式），在线程池里跑
        loop = asyncio.get_running_loop()
        # 30s：真模型首字节常见 2-5s，原来 8s 会被误判成 ReadTimeout
        sync_gen = client.chat_stream(messages, timeout=30.0, enable_thinking=enable_thinking)
        while True:
            ev = await loop.run_in_executor(None, lambda: next(sync_gen, None))
            if ev is None:
                break
            # 客户端把网络/HTTP 异常也表达成 ("error", msg) 后 return（不抛异常），
            # 这里拦下来统一走降级，避免裸错误直接丢给用户、兜底内容被跳过
            if isinstance(ev, tuple) and len(ev) == 2 and ev[0] == "error":
                err = str(ev[1])
                break
            if isinstance(ev, tuple) and len(ev) == 2 and ev[0] == "think":
                if not enable_thinking:
                    continue   # 用户关了思考：丢弃推理内容，不展示思考面板
                yield ev
                continue
            if isinstance(ev, tuple) and len(ev) == 2 and ev[0] == "delta" and ev[1]:
                produced = True
            yield ev
    except Exception as e:  # noqa: BLE001  LLMError / httpx 异常一律降级
        err = f"{e.__class__.__name__}: {str(e)[:120]}"

    if not err:
        return
    if produced:
        # 已有半截真实内容：不追加模板（真假内容混排更难读），提示后正常收尾
        yield ("notice", f"生成中断：{err[:120]}")
        yield ("done", {"full": "", "degraded": True})
        return
    # 一个字都没产出：提示已降级，再继续输出演示内容，用户至少能看到兜底回复
    yield ("notice", f"真实模型调用失败，已切换演示模式：{err[:120]}")
    async for ev in _mock_stream_chunks(user_text or "", attach_name, enable_thinking):
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
