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
        if v not in ("text", "vision"):
            raise ValueError("slot 只能是 text 或 vision")
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


class LLMEffectiveOut(BaseModel):
    source: str                      # user / platform / env / mock
    text: dict | None                # {provider, provider_label, model}
    vision: dict | None
