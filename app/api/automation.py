"""M1 被测系统（TestTarget）CRUD + M2 自动化执行端点。

- POST/GET /api/targets：创建 / 列出当前用户的被测系统
- GET/DELETE /api/targets/{id}：详情 / 删除（校验属主，admin 可越权查看）
- 登录凭据 Fernet 加密落库，任何接口不回传明文（只给 has_auth 布尔）

M2 执行引擎（contract-m2 3）：
- POST /api/tasks/{task_id}/run-auto：触发执行（无用例 400 / 进行中 409）
- GET  /api/tasks/{task_id}/executions：执行历史（新→旧）
- GET  /api/executions/{run_id}：执行详情（含解析后 report 对象）
- POST /api/executions/{run_id}/retry：复制配置重试（trigger=retry）
- GET  /api/executions/{run_id}/files/{path}：失败截图 / trace 附件（白名单前缀）
- GET  /api/tasks/{task_id}/pages：页面探索结果（crawler 落盘的 pages.json）
- GET  /api/tasks/{task_id}/pages/screenshot/{name}：页面探索截图（文件名白名单）

路由注册由统筹者在 main.py 合并（见 docs/handoff-M1-frontend-shared.md）。
"""
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Form, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.tasks import _own_task, _parse_cases
from app.core import exec_queue
from app.core.config import OUTPUT_DIR
from app.core.db import get_db
from app.models.automation import ExecutionRun, TestTarget, encrypt_credential
from app.models.user import User
from app.schemas.automation import (
    ExecutionDetail,
    ExecutionReport,
    ExecutionRunOut,
    TargetOut,
)

router = APIRouter()


def _validate_base_url(base_url: str) -> str:
    """校验并规范化被测系统 URL：必须 http(s) 且带域名，去掉尾斜杠。"""
    u = (base_url or "").strip()
    parsed = urlparse(u if "://" in u else f"https://{u}")
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise HTTPException(status_code=400, detail="请提供合法的 http(s) 站点地址")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def _to_out(t: TestTarget) -> TargetOut:
    return TargetOut(
        id=t.id, name=t.name, base_url=t.base_url or "",
        auth_type=t.auth_type, has_auth=bool(t.username_enc or t.password_enc),
        created_at=t.created_at,
    )


def _own_target(db: Session, target_id: str, user: User) -> TestTarget:
    """非本人且非 admin → 404（不暴露存在性），与任务接口的 _own_task 策略一致。"""
    t = db.get(TestTarget, target_id)
    if not t or (t.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="被测系统不存在")
    return t


@router.post("/targets", response_model=TargetOut, status_code=201)
def create_target(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    name: str = Form(""),
    base_url: str = Form(...),
    username: str = Form(""),
    password: str = Form(""),
    auth_type: str = Form("form"),
):
    """创建被测系统。username/password 有值时 Fernet 加密落库，明文即弃。"""
    url = _validate_base_url(base_url)
    t = TestTarget(
        id=uuid.uuid4().hex[:12],
        user_id=user.id,
        name=(name.strip() or urlparse(url).netloc)[:255],
        base_url=url,
        auth_type=auth_type if username or password else "none",
        username_enc=encrypt_credential(username.strip()) if username.strip() else None,
        password_enc=encrypt_credential(password) if password else None,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _to_out(t)


@router.get("/targets", response_model=list[TargetOut])
def list_targets(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """列出当前用户的被测系统（新→旧）。"""
    rows = db.execute(
        select(TestTarget).where(TestTarget.user_id == user.id)
        .order_by(TestTarget.created_at.desc())
    ).scalars().all()
    return [_to_out(t) for t in rows]


@router.get("/targets/{target_id}", response_model=TargetOut)
def get_target(target_id: str, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """被测系统详情（不含凭据明文）。"""
    return _to_out(_own_target(db, target_id, user))


@router.delete("/targets/{target_id}")
def delete_target(target_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """删除被测系统（引用它的历史任务不受影响，Task.target_id 仅作溯源）。"""
    t = _own_target(db, target_id, user)
    db.delete(t)
    db.commit()
    return {"ok": True, "deleted_target_id": target_id}


# ============ M2 执行引擎（contract-m2 3） ============

def _own_run(db: Session, run_id: str, user: User) -> ExecutionRun:
    """取执行记录并校验归属（经任务属主规则，非本人且非 admin → 404）。"""
    run = db.get(ExecutionRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    _own_task(db, run.task_id, user)  # 复用任务属主校验（guest 只能操作共享 guest 自己的任务）
    return run


def _run_out(run: ExecutionRun) -> ExecutionRunOut:
    return ExecutionRunOut(
        id=run.id, task_id=run.task_id, trigger=run.trigger, status=run.status,
        progress=run.progress, total=run.total, passed=run.passed,
        failed=run.failed, skipped=run.skipped, duration_ms=run.duration_ms,
        heal_round=run.heal_round,
        error=run.error, created_at=run.created_at,
        started_at=run.started_at, finished_at=run.finished_at,
    )


def _parse_run_report(run: ExecutionRun) -> ExecutionReport | None:
    """终态 run 的 report_json → 结构化报告对象；未终态 / 脏数据返回 None。"""
    if run.status not in ("completed", "failed") or not run.report_json:
        return None
    try:
        obj = json.loads(run.report_json)
        cases = obj.get("cases") or []
        return ExecutionReport(
            summary=obj.get("summary") or {},
            cases=cases,
            environment=obj.get("environment") or {},
            heal=obj.get("heal"),
        )
    except (ValueError, TypeError, AttributeError):
        return None


def _task_auto_dir(db: Session, task) -> str:
    """任务脚本目录相对路径：outputs/{user_data_dir}/{task_id}/auto（相对 AITF_ROOT_DIR）。"""
    return f"outputs/{task.user_data_dir(db)}/{task.id}/auto"


@router.post("/tasks/{task_id}/run-auto")
def run_auto(task_id: str, db: Session = Depends(get_db),
             user: User = Depends(get_current_user),
             auto_heal: bool = Form(True)):
    """触发一轮自动化执行：建 ExecutionRun(pending) → 入执行队列。

    - 任务无用例（cases_json 空）→ 400
    - 已有 pending/running 的执行 → 409（同一任务同时只允许一轮）
    - auto_heal：失败后是否进入 LLM 自愈循环（M4，默认开）
    """
    task = _own_task(db, task_id, user)

    cases = _parse_cases(task.cases_json)
    if not cases:
        raise HTTPException(status_code=400, detail="任务暂无用例，请先生成测试用例")

    active = db.execute(
        select(ExecutionRun).where(
            ExecutionRun.task_id == task_id,
            ExecutionRun.status.in_(("pending", "running")),
        )
    ).scalars().first()
    if active:
        raise HTTPException(status_code=409, detail="该任务已有执行正在进行中，请等待完成")

    run = ExecutionRun(
        id=uuid.uuid4().hex[:12],
        task_id=task.id,
        user_id=task.user_id,
        trigger="manual",
        status="pending",
        total=len(cases),
        auto_dir=_task_auto_dir(db, task),
    )
    db.add(run)
    db.commit()

    # M4 自愈开关登记（默认开；显式关闭的 run 执行器跳过自愈）
    from app.services.auto_runner import set_auto_heal
    set_auto_heal(run.id, auto_heal)

    exec_queue.start_workers()  # 懒启动（幂等）：首次触发时建 worker 池 + 启动恢复
    exec_queue.enqueue(run.id)
    return {"run_id": run.id, "status": "pending"}


@router.get("/tasks/{task_id}/executions", response_model=list[ExecutionRunOut])
def list_executions(task_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """任务执行历史（新→旧，不含 report 大对象）。"""
    _own_task(db, task_id, user)
    rows = db.execute(
        select(ExecutionRun).where(ExecutionRun.task_id == task_id)
        .order_by(ExecutionRun.created_at.desc(), ExecutionRun.id.desc())
    ).scalars().all()
    return [_run_out(r) for r in rows]


@router.get("/executions/{run_id}", response_model=ExecutionDetail)
def get_execution(run_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    """执行详情：终态时带解析后的 report 对象；pending/running 只带进度字段供轮询。"""
    run = _own_run(db, run_id, user)
    detail = ExecutionDetail(
        **_run_out(run).model_dump(),
        auto_dir=run.auto_dir or "",
        report=_parse_run_report(run),
    )
    return detail


@router.post("/executions/{run_id}/retry")
def retry_execution(run_id: str, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """重试执行：复制配置建新 run（trigger=retry），原 run 保持不变。"""
    old = _own_run(db, run_id, user)
    if old.status in ("pending", "running"):
        raise HTTPException(status_code=409, detail="该执行正在进行中，无法重试")

    run = ExecutionRun(
        id=uuid.uuid4().hex[:12],
        task_id=old.task_id,
        user_id=old.user_id,
        trigger="retry",
        status="pending",
        total=old.total,
        auto_dir=old.auto_dir,
    )
    db.add(run)
    db.commit()

    exec_queue.start_workers()
    exec_queue.enqueue(run.id)
    return {"run_id": run.id, "status": "pending"}


# 附件路径白名单前缀：失败截图 / playwright trace（contract-m2 3）
_FILES_ALLOWED_PREFIXES: tuple[str, ...] = ("shots/", "trace/")


@router.get("/executions/{run_id}/files/{path:path}")
def execution_file(run_id: str, path: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """执行附属文件（失败截图 / trace）。路径白名单前缀，404 兜底。"""
    run = _own_run(db, run_id, user)
    # 安全校验：拒绝对路径 / 目录穿越
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="非法路径")
    if not path.startswith(_FILES_ALLOWED_PREFIXES):
        raise HTTPException(status_code=404, detail="文件不存在")

    full = (OUTPUT_DIR.parent / run.auto_dir / path) if run.auto_dir else None
    if full is None or not full.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(full)


# ============ 页面探索可视化（e2e 全链路 crawler 产物） ============

# 截图文件名白名单：crawler 页面截图（page-NNN.png）+ M5 探索步骤截图（step-NNN.png），
# 仅允许序号命名，杜绝任意路径/目录穿越；子目录按前缀路由（page→pages/，step→explore/）
_PAGE_SHOT_RE = re.compile(r"^(page|step)-\d+\.png$")

# 探索录屏固定相对路径（crawler 归档产物；端点不接受用户输入路径，天然杜绝穿越）
_EXPLORE_VIDEO_PARTS = ("videos", "explore.webm")


def _task_out_dir(db: Session, task) -> Path:
    """任务输出目录：OUTPUT_DIR/{user_data_dir}/{task_id}（与引擎 out_dir 同口径）。"""
    return OUTPUT_DIR / task.user_data_dir(db) / task.id


@router.get("/tasks/{task_id}/pages")
def get_task_pages(task_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """任务页面探索结果：读任务输出目录 pages.json（crawler 步骤落盘的 PageDesc 列表）。

    - 归属校验与任务接口一致（非本人且非 admin → 404，不暴露存在性）
    - pages.json 不存在（未跑过 crawler / 旧任务 / 抓取失败）→ 200 空列表，前端据此隐藏区块
    - 脏数据（非法 JSON / 非 list）兜底为空列表，不让前端崩
    """
    task = _own_task(db, task_id, user)
    out_dir = _task_out_dir(db, task)
    pages_file = out_dir / "pages.json"
    pages: list = []
    if pages_file.is_file():
        try:
            loaded = json.loads(pages_file.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                pages = loaded
        except (ValueError, OSError):
            pages = []
    # 探索录屏可播标记：videos/explore.webm 存在时前端才显示播放入口
    video_available = (out_dir / "videos" / "explore.webm").is_file()
    return {"task_id": task.id, "pages": pages, "video_available": video_available}


@router.get("/tasks/{task_id}/pages/screenshot/{name}")
def get_task_page_screenshot(task_id: str, name: str, db: Session = Depends(get_db),
                             user: User = Depends(get_current_user)):
    """页面/探索步骤截图：FileResponse 返回任务目录下 page-NNN.png（pages/）或 step-NNN.png（explore/）。

    - 文件名正则白名单 ^(page|step)-\d+\.png$：任何其他字符（含 / 与 ..）一律 400，防路径穿越
    - 截图缺失（静态抓取 / 截图失败）→ 404
    """
    task = _own_task(db, task_id, user)
    if not _PAGE_SHOT_RE.fullmatch(name):
        raise HTTPException(status_code=400, detail="非法截图文件名")
    # 按前缀路由子目录：page-* 为 crawler 页面截图，step-* 为 M5 探索步骤截图
    sub = "pages" if name.startswith("page-") else "explore"
    shot = _task_out_dir(db, task) / sub / name
    if not shot.is_file():
        raise HTTPException(status_code=404, detail="截图不存在")
    return FileResponse(shot)


@router.get("/tasks/{task_id}/video")
def get_task_video(task_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """探索过程录屏（crawler Playwright 录制的 explore.webm）。

    - 归属校验与 pages 一致（非本人且非 admin → 404，不暴露存在性）
    - 路径固定 videos/explore.webm（无用户输入路径，杜绝穿越）
    - 无录屏（静态抓取 / Playwright 降级 / 旧任务）→ 404，前端隐藏播放入口
    """
    task = _own_task(db, task_id, user)
    video = _task_out_dir(db, task).joinpath(*_EXPLORE_VIDEO_PARTS)
    if not video.is_file():
        raise HTTPException(status_code=404, detail="录屏不存在")
    return FileResponse(video, media_type="video/webm")
