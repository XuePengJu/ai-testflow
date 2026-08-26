"""平台配置：读取 .env，定义路径与模型开关。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # ai-testflow/

# 加载 .env（仅当环境变量未设置时填充，避免覆盖系统环境）
_ENV_PATH = BASE_DIR / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen-plus")

UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
DB_PATH = BASE_DIR / "app.db"

# 内置的用例生成核心库（已整合，使项目自包含、clone 即跑）
GENERATOR_CORE_DIR = BASE_DIR / "generator_core"

# 前端静态目录
STATIC_DIR = BASE_DIR / "app" / "static"


def is_mock() -> bool:
    """无百炼 Key 时走 mock 兜底，保证开箱即跑。"""
    return not bool(DASHSCOPE_API_KEY)
