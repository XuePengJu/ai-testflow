"""AI 测试工作流平台 · 入口。

启动：uvicorn main:app --reload --port 8000
访问：http://127.0.0.1:8000  （前端 dashboard）
      http://127.0.0.1:8000/docs （Swagger 接口文档）

V2：认证 + 多用户（guest/user/admin）。反代部署时才开 --proxy-headers。
"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, categories, guest, llm_config, tasks, users
from app.core.config import STATIC_DIR, jwt_secret_is_placeholder, ENV
from app.core.db import init_db

logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # JWT_SECRET 启动检测：默认占位值 → 演示 WARNING / 生产拒启
    if jwt_secret_is_placeholder():
        if ENV == "production":
            raise RuntimeError("生产环境必须设置 JWT_SECRET 环境变量")
        logger.warning("JWT_SECRET 为默认占位值，仅限本地演示使用")

    # 访客清理调度器（每小时 + 启动兜底）
    from app.jobs.guest_cleaner import start_scheduler
    start_scheduler()

    yield


app = FastAPI(title="AI 测试工作流平台", version="0.2.0", lifespan=lifespan)

# CORS：生产用 CORS_ORIGINS 环境变量限定前端域名（逗号分隔）；缺省 "*"（本地演示）
_cors_raw = os.getenv("CORS_ORIGINS", "*").strip()
_cors_origins = ["*"] if _cors_raw == "*" else [o.strip() for o in _cors_raw.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 分级加密（admin 明文 / user+guest AES-256-GCM）——最后注册 = 最外层
from app.core.middleware import ApiCryptoMiddleware  # noqa: E402
app.add_middleware(ApiCryptoMiddleware)

app.include_router(tasks.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(guest.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(categories.router, prefix="/api")
app.include_router(llm_config.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/config.js")
def config_js():
    """前端 API 基址配置（本地同源伺服，避免 404 导致 API_BASE 缺失）"""
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(
        (STATIC_DIR / "config.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
    )


@app.get("/favicon.svg")
@app.get("/favicon.ico")
def favicon():
    """站点图标（与左上角 logo 同款：渐变方块 + 白色机器人脸）"""
    from fastapi.responses import FileResponse

    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


# 前端第三方库（mind-elixir 等）本地化 vendor 目录，同源伺服
# 让本地单服务（8000）即可完整加载思维导图，无需另起静态服务器
app.mount("/vendor", StaticFiles(directory=str(STATIC_DIR / "vendor")), name="vendor")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
