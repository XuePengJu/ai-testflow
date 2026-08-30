"""模型厂商预设（V2.4 FR-I）。

统一走 OpenAI 兼容协议（POST {base_url}/chat/completions），选中预设后
只需填 API Key——Base URL 自动带出（可改）。模型列表仅是建议值，
前端允许自由填写（如火山方舟需填接入点 ID ep-xxx）。
"""

# vision=True 表示该模型支持图像输入（可作为「图像识别模型」槽位）
PROVIDERS: dict[str, dict] = {
    "bailian": {
        "label": "阿里百炼 · 通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "note": "Token 包 / 按量计费 Key 同端点通用",
        "models": [
            {"id": "qwen-plus", "label": "qwen-plus · 均衡（推荐）", "vision": False},
            {"id": "qwen-max", "label": "qwen-max · 最强", "vision": False},
            {"id": "qwen-turbo", "label": "qwen-turbo · 快速便宜", "vision": False},
            {"id": "qwen-vl-plus", "label": "qwen-vl-plus · 图文理解", "vision": True},
            {"id": "qwen-vl-max", "label": "qwen-vl-max · 图文最强", "vision": True},
        ],
    },
    "zhipu": {
        "label": "智谱 GLM · API 按量",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "note": "普通 API Key（按量计费）；glm-4.7-flash / glm-4.6v-flash 长期免费",
        "models": [
            {"id": "glm-4.7-flash", "label": "glm-4.7-flash · 免费主力(200K)", "vision": False},
            {"id": "glm-4.5-flash", "label": "glm-4.5-flash · 免费", "vision": False},
            {"id": "glm-4.5-air", "label": "glm-4.5-air · 轻量", "vision": False},
            {"id": "glm-4.6v-flash", "label": "glm-4.6v-flash · 免费视觉(图/视频)", "vision": True},
        ],
    },
    "zhipu_coding": {
        "label": "智谱 GLM · Coding Plan（订阅）",
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "note": "订阅 Coding Plan 后用套餐 Key，走专用端点",
        "models": [
            {"id": "glm-4.6", "label": "glm-4.6", "vision": False},
            {"id": "glm-4.5", "label": "glm-4.5", "vision": False},
        ],
    },
    "hunyuan": {
        "label": "腾讯混元",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "note": "",
        "models": [
            {"id": "hunyuan-turbos-latest", "label": "hunyuan-turbos-latest", "vision": False},
            {"id": "hunyuan-vision", "label": "hunyuan-vision · 图文理解", "vision": True},
        ],
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "note": "注意：deepseek-chat 不支持图像输入",
        "models": [
            {"id": "deepseek-chat", "label": "deepseek-chat · V3", "vision": False},
            {"id": "deepseek-reasoner", "label": "deepseek-reasoner · R1 推理", "vision": False},
        ],
    },
    "kimi": {
        "label": "月之暗面 · Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "note": "",
        "models": [
            {"id": "kimi-latest", "label": "kimi-latest", "vision": False},
            {"id": "kimi-k2-turbo-preview", "label": "kimi-k2-turbo-preview", "vision": False},
        ],
    },
    "doubao": {
        "label": "火山方舟 · 豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "note": "模型处填「推理接入点 ID（ep-xxx）」或模型 ID",
        "models": [
            {"id": "doubao-seed-1-6", "label": "doubao-seed-1-6", "vision": False},
            {"id": "doubao-1-5-vision-pro", "label": "doubao-1-5-vision-pro · 图文", "vision": True},
        ],
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "note": "填写任意 OpenAI 兼容端点",
        "models": [],
    },
}


def provider_label(provider: str) -> str:
    p = PROVIDERS.get(provider)
    return p["label"] if p else (provider or "自定义")


def is_provider(provider: str) -> bool:
    return provider in PROVIDERS
