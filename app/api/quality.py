"""M0 平台自身质量量化 API。

- GET  /api/quality/summary     最新聚合结果（无文件返回空态提示）——所有登录角色可见（展示用途）
- POST /api/quality/run         后台触发"pytest + e2e + 聚合"（subprocess，单 worker；运行中 409）——admin only
- GET  /api/quality/run/status  当次运行状态（idle/running/completed/failed + 阶段进度文案）——admin only
- GET  /api/quality/history     history.jsonl 趋势数组（时间升序，最多近 30 次）——所有登录角色可见

权限设计（方案 A，2026-09-23）：质量报告是平台对外展示面，读接口放开到任意登录角色
（含访客）；触发真实测试执行属于运维操作，保持 admin-only。

实现要点：subprocess 不 shell=True、timeout=QUALITY_EXEC_TIMEOUT、原始报告落临时目录；
量化对象是平台自身测试，与 M1-M3 的被测系统执行报告完全独立。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user, require_admin
from app.core.config import BASE_DIR, QUALITY_DATA_DIR, QUALITY_EXEC_TIMEOUT
from app.models.user import User

router = APIRouter()

_AGGREGATE_SCRIPT = BASE_DIR / "scripts" / "quality" / "aggregate_quality.py"
_RUN_E2E_SCRIPT = BASE_DIR / "frontend" / "scripts" / "run-e2e.mjs"

# 单 worker 运行态（进程内共享；平台自身质量执行无需跨进程持久化）
_run_state: dict = {
    "run_id": None,
    "status": "idle",        # idle / running / completed / failed
    "stage": "",             # 当前阶段：pytest / e2e / aggregate
    "message": "",           # 阶段进度文案
    "error": None,           # 失败原因摘要
    "started_at": None,
    "finished_at": None,
}
_state_lock = threading.Lock()


def _set_state(**kwargs) -> None:
    with _state_lock:
        _run_state.update(kwargs)


def _python_exe() -> str:
    """优先项目 venv 的 Python（保证 pytest-json-report/pytest-cov 可用），回退当前解释器。"""
    venv_python = BASE_DIR / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _stage_commands(tmp_dir: Path) -> list[dict]:
    """构建流水线阶段命令列表（真实执行路径；测试可通过 monkeypatch 覆盖以加速验证）。

    每项：{name, argv, cwd, ok_nonzero, env}——ok_nonzero=True 表示非零退出码不算失败
    （pytest 有用例失败时退出码非 0，但 JSON 报告仍有效，聚合继续）；
    env 为附加环境变量（缺省继承父进程）。
    """
    python = _python_exe()
    return [
        {
            "name": "pytest",
            "message": "正在运行 pytest 单元测试（含覆盖率采集）…",
            "argv": [
                python, "-m", "pytest", "tests/", "-q",
                "--json-report", f"--json-report-file={tmp_dir / 'pytest-report.json'}",
                "--cov=app", f"--cov-report=json:{tmp_dir / 'coverage.json'}",
                # basetemp 隔离：pytest 临时夹具落本次运行目录，不污染系统 /tmp
                "--basetemp", str(tmp_dir / "pytest-tmp"),
            ],
            "cwd": str(BASE_DIR),
            "ok_nonzero": True,
            # coverage 中间数据落临时目录，避免在项目根留下 .coverage.*
            "env": {"COVERAGE_FILE": str(tmp_dir / ".coverage")},
        },
        {
            "name": "e2e",
            "message": "正在运行 Node e2e 套件…",
            "argv": ["node", str(_RUN_E2E_SCRIPT), str(tmp_dir)],
            "cwd": str(BASE_DIR / "frontend"),
            "ok_nonzero": True,   # e2e 失败套件照常写报告，聚合如实呈现
        },
        {
            "name": "aggregate",
            "message": "正在聚合质量数据…",
            "argv": [python, str(_AGGREGATE_SCRIPT), "--reports-dir", str(tmp_dir)],
            "cwd": str(BASE_DIR),
            "ok_nonzero": False,
        },
    ]


def _run_pipeline(run_id: str, tmp_dir: Path) -> None:
    """后台线程：顺序执行各阶段 → 更新运行态。任一硬失败即终止并落 failed。"""
    try:
        for stage in _stage_commands(tmp_dir):
            _set_state(stage=stage["name"], message=stage["message"])
            try:
                proc = subprocess.run(
                    stage["argv"],
                    cwd=stage["cwd"],
                    capture_output=True,
                    text=True,
                    timeout=QUALITY_EXEC_TIMEOUT,
                    env={**os.environ, **stage.get("env", {})},
                )
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"阶段 {stage['name']} 超时（>{QUALITY_EXEC_TIMEOUT}s）")
            except FileNotFoundError as e:
                # 如 node 未安装：该阶段无法执行，按失败处理（如实暴露，不伪造数据）
                raise RuntimeError(f"阶段 {stage['name']} 启动失败：{e}")

            if proc.returncode != 0 and not stage["ok_nonzero"]:
                tail = (proc.stderr or proc.stdout or "").strip()[-500:]
                raise RuntimeError(f"阶段 {stage['name']} 退出码 {proc.returncode}：{tail}")

        _set_state(status="completed", stage="done", message="质量数据已更新",
                   finished_at=_now(), error=None)
    except Exception as e:  # noqa: BLE001  后台线程兜底，失败原因如实回传前端
        _set_state(status="failed", message="执行失败", error=str(e)[:500],
                   finished_at=_now())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/quality/summary")
def get_summary(user: User = Depends(get_current_user)):
    """最新聚合结果；尚未运行过时返回空态提示（前端据此渲染引导）。任意登录角色可见。"""
    f = QUALITY_DATA_DIR / "quality-summary.json"
    if not f.exists():
        return {"exists": False, "summary": None,
                "message": "尚未运行平台测试，点击「运行测试」生成质量数据"}
    try:
        return {"exists": True, "summary": json.loads(f.read_text(encoding="utf-8"))}
    except (json.JSONDecodeError, OSError):
        raise HTTPException(500, detail="quality-summary.json 损坏，请重新运行测试")


@router.post("/quality/run")
def start_run(admin: User = Depends(require_admin)):
    """后台触发一轮完整测试（pytest + e2e + 聚合）；已有运行在进行时 409。"""
    with _state_lock:
        if _run_state["status"] == "running":
            raise HTTPException(409, detail="已有一轮测试在运行中，请稍候")
        run_id = uuid.uuid4().hex[:12]
        _run_state.update(run_id=run_id, status="running", stage="prepare",
                          message="正在准备测试环境…", error=None,
                          started_at=_now(), finished_at=None)
    tmp_dir = Path(tempfile.mkdtemp(prefix="aitf-quality-"))
    threading.Thread(target=_run_pipeline, args=(run_id, tmp_dir),
                     name=f"quality-run-{run_id}", daemon=True).start()
    return {"run_id": run_id, "status": "running"}


@router.get("/quality/run/status")
def run_status(admin: User = Depends(require_admin)):
    """当次（或最近一次）运行状态与阶段进度文案。"""
    with _state_lock:
        return dict(_run_state)


@router.get("/quality/history")
def get_history(user: User = Depends(get_current_user)):
    """趋势数组：history.jsonl 按时间升序，最多近 30 次。任意登录角色可见。"""
    f = QUALITY_DATA_DIR / "history.jsonl"
    if not f.exists():
        return []
    points: list[dict] = []
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                points.append(json.loads(line))
    except (json.JSONDecodeError, OSError):
        raise HTTPException(500, detail="history.jsonl 损坏，请重新运行测试")
    return points[-30:]
