"""LLM 配置请求/响应模型（V2.4 FR-I）。"""
from pydantic import BaseModel, field_validator


class LLMConfigIn(BaseModel):
    """PUT 配置：api_key 缺省 = 保留原 Key；显式传空串 = 清除 Key。"""
    slot: str
    provider: str = "custom"
    base_url: str = ""
    model: str = ""
    api_key: str | None = None

    @field_validator("slot")
    @classmethod
    def _slot(cls, v: str) -> str:
        if v not in ("text", "vision", "embedding"):
            raise ValueError("slot 只能是 text / vision / embedding")
        return v

    @field_validator("model", "base_url")
    @classmethod
    def _not_blank_key_fields(cls, v: str, info) -> str:
        # model / base_url 必填（自定义端点也必须有值）
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} 不能为空")
        return v.strip()


class LLMConfigOut(BaseModel):
    slot: str
    provider: str
    base_url: str
    model: str
    api_key_masked: str = ""    # 如 sk-****abcd；空 = 未配置 Key


class LLMTestIn(BaseModel):
    """测试连通：api_key 缺省 = 用已保存的 Key（仅测已保存配置时）。"""
    provider: str = "custom"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    kind: str = "chat"              # chat | embedding（V4.0：向量模型走 /embeddings）


class LLMEffectiveOut(BaseModel):
    source: str                      # user / platform / env / mock
    text: dict | None                # {provider, provider_label, model}
    vision: dict | None


class ChatIn(BaseModel):
    """首页对话流：用户消息 + 可选历史（多轮）。"""
    message: str
    history: list[dict] = []         # [{role:"user"/"assistant", content:"..."}]
    conversation_id: str | None = None   # 归属会话（落库对话记录用；None 则不落库）
    task_id: str | None = None       # 迭代补充模式：关联的任务 id，AI 回复时附该任务用例摘要
    file_id: str | None = None       # 对话附件 id（POST /api/files 返回），AI 读取文档内容后作答
    thinking: bool = True            # 「深度思考」开关：关掉则不请求思考、也不显示思考面板
    roles: list[str] | None = None   # 参与角色（pm/qa/dev），决定 AI 回复身份；空则默认测试工程师
    kb_id: str | None = None         # V4.0 RAG：限定知识库检索；空则检索当前用户全部可见库
    mode: str | None = None          # V4.1 会话模式：workflow(默认)/kb_qa，标记会话归属
