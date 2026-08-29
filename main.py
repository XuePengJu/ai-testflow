"""AI 测试工作流平台 · 入口。

启动：uvicorn main:app --reload --port 8000
访问：http://127.0.0.1:8000  （前端 dashboard）
      http://127.0.0.1:8000/docs （Swagger 接口文档）

V2：认证 + 多用户（guest/user/admin）。反代部署时才开 --proxy-headers。
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.api import auth, categories, guest, tasks, users
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
