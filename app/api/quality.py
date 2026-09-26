"""M0 平台自身质量量化 API。

- GET  /api/quality/summary     最新聚合结果（无文件返回空态提示）——所有登录角色可见（展示用途）
- POST /api/quality/run         后台触发测试执行（subprocess，单 worker；运行中 409）——admin only
                                支持 scope 范围选择：{unit, api, e2e}，缺省=全量
- GET  /api/quality/run/status  当次运行状态（idle/running/completed/failed + 实时进度）——admin only
- GET  /api/quality/history     history.jsonl 趋势数组（时间升序，最多近 30 次）——所有登录角色可见

权限设计（方案 A，2026-09-23）：质量报告是平台对外展示面，读接口放开到任意登录角色
（含访客）；触发真实测试执行属于运维操作，保持 admin-only。

实现要点（2026-09-26 V2：范围选择 + 流式进度）：
- scope 范围：unit/api 分别勾选（部分 pytest 运行按函数级 node-id 清单执行），
  e2e 可选 m1~m5 任意子集；聚合按分段合并，各分段保留独立采集时间
- 进度：subprocess.run（阻塞）→ Popen 逐行读 stdout，实时解析 pytest 百分比
  与 e2e 套件/步骤行写入 _run_state，前端 2s 轮询展示进度条与套件状态
- 单 worker 运行态进程内共享（平台自身质量执行无需跨进程持久化）；
  原始报告落临时目录；量化对象是平台自身测试，与被测系统执行报告完全独立
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_current_user, require_admin
from app.core.config import BASE_DIR, QUALITY_DATA_DIR, QUALITY_EXEC_TIMEOUT
from app.models.user import User

router = APIRouter()

_AGGREGATE_SCRIPT = BASE_DIR / "scripts" / "quality" / "aggregate_quality.py"
_RUN_E2E_SCRIPT = BASE_DIR / "frontend" / "scripts" / "run-e2e.mjs"
_SCOPE_SCRIPT = BASE_DIR / "scripts" / "quality" / "test_scope.py"

# e2e 套件短名 → 脚本名（scope 参数用短名，报告/看板用脚本名）
_E2E_SLOTS: dict[str, str] = {
    "m1": "e2e-m1-browser.mjs",
    "m2": "e2e-m2-browser.mjs",
    "m3": "e2e-m3-browser.mjs",
    "m4": "e2e-m4-browser.mjs",
    "m5": "e2e-m5-smoke.mjs",
}

# 单 worker 运行态（进程内共享）
_run_state: dict = {
    "run_id": None,
    "status": "idle",         # idle / running / completed / failed
    "stage": "",              # 当前阶段：prepare / scope-collect / pytest / e2e / aggregate / done
    "message": "",            # 阶段进度文案
    "error": None,            # 失败原因摘要
    "started_at": None,
    "finished_at": None,
    "plan": None,             # 本次运行计划 {"unit":bool,"api":bool,"e2e":["m1",...]|None}
    "pytest_percent": None,   # pytest 阶段进度百分比（0~100，阶段外为 None）
    "pytest_done": 0,         # pytest 已执行用例数（按百分比 × 总数估算）
    "pytest_total_hint": None,  # 上一次聚合的 pytest 总数（进度估算基准）
    "suites": [],             # e2e 套件实时状态 [{name,status,duration_ms,steps_ok,steps_total,last_step}]
    "log_tail": [],           # 最近输出行（环形，最多 12 条）
}
_state_lock = threading.Lock()

_LOG_TAIL_MAX = 12
_PYTEST_PCT_RE = re.compile(r"\[\s*(\d+)%\]")
_E2E_SUITE_RE = re.compile(r"\[e2e\]\s+(\S+?):\s+(\w+)\s+\((\d+)ms\)")


def _set_state(**kwargs) -> None:
    with _state_lock:
        _run_state.update(kwargs)


def _append_log(line: str) -> None:
    with _state_lock:
        tail = _run_state["log_tail"]
        tail.append(line)
        if len(tail) > _LOG_TAIL_MAX:
            del tail[: len(tail) - _LOG_TAIL_MAX]


class QualityRunReq(BaseModel):
    """运行范围（全部缺省 = 全量）。unit/api 不选的段不跑；e2e 见 _normalize_plan。"""
    unit: bool | None = None
    api: bool | None = None
    e2e: bool | list[str] | None = None


def _normalize_plan(req: QualityRunReq | None) -> dict:
    """请求体 → 运行计划。plan["e2e"]=None 表示跳过 e2e，列表为所选套件短名。"""
    if req is None:
        return {"unit": True, "api": True, "e2e": list(_E2E_SLOTS)}
    unit = req.unit if req.unit is not None else True
    api = req.api if req.api is not None else True
    if req.e2e is None or req.e2e is True:
        e2e = list(_E2E_SLOTS)
    elif req.e2e is False or req.e2e == []:
        e2e = None
    else:
        bad = [s for s in req.e2e if s not in _E2E_SLOTS]
        if bad:
            raise HTTPException(400, detail=f"未知 e2e 套件：{bad}（可选 {list(_E2E_SLOTS)}）")
        e2e = [s for s in _E2E_SLOTS if s in req.e2e]  # 按里程碑顺序
    if not unit and not api and e2e is None:
        raise HTTPException(400, detail="运行范围为空：至少勾选一项")
    return {"unit": unit, "api": api, "e2e": e2e}


def _python_exe() -> str:
    """优先项目 venv 的 Python（保证 pytest-json-report/pytest-cov 可用），回退当前解释器。"""
    venv_python = BASE_DIR / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _pytest_total_hint() -> int | None:
    """上次聚合的 pytest 总数，作为本次进度估算基准（无历史数据则 None）。"""
    try:
        f = QUALITY_DATA_DIR / "quality-summary.json"
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            n = (data.get("pytest") or {}).get("total")
            return int(n) if n else None
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return None


def _stage_commands(tmp_dir: Path, plan: dict) -> list[dict]:
    """按运行计划构建流水线阶段列表。

    每项：{name, message, argv, cwd, ok_rc, on_line}——ok_rc 为允许的退出码集合
    （pytest 退出码 1 =有用例失败但 JSON 报告有效；e2e 同理，报告照常落盘）。
    """
    python = _python_exe()
    stages: list[dict] = []

    if plan["unit"] or plan["api"]:
        argv: list[str] = [python, "-m", "pytest"]
        full_pytest = plan["unit"] and plan["api"]
        if full_pytest:
            argv.append("tests/")
        else:
            kind = "api" if plan["api"] else "unit"
            kind_label = "接口" if kind == "api" else "单元"
            list_file = tmp_dir / f"scope-{kind}.txt"
            stages.append({
                "name": "scope-collect",
                "message": f"正在生成{kind_label}测试清单…",
                "argv": [python, str(_SCOPE_SCRIPT), "--kind", kind, "--out", str(list_file)],
                "cwd": str(BASE_DIR),
                "ok_rc": {0},
            })
            argv.append(f"@{list_file}")
        argv += [
            "--json-report", f"--json-report-file={tmp_dir / 'pytest-report.json'}",
            "--cov=app", f"--cov-report=json:{tmp_dir / 'coverage.json'}",
            # basetemp 隔离：pytest 临时夹具落本次运行目录，不污染系统 /tmp
            "--basetemp", str(tmp_dir / "pytest-tmp"),
            # 不加 -q：默认输出的 "[ N%]" 进度百分比是进度条数据源
        ]
        stages.append({
            "name": "pytest",
            "message": "正在运行 pytest 测试（含覆盖率采集）…",
            "argv": argv,
            "cwd": str(BASE_DIR),
            "ok_rc": {0, 1},
            # coverage 中间数据落临时目录，避免在项目根留下 .coverage.*
            "env": {"COVERAGE_FILE": str(tmp_dir / ".coverage")},
        })

    if plan["e2e"]:
        e2e_argv = ["node", str(_RUN_E2E_SCRIPT), str(tmp_dir)]
        if len(plan["e2e"]) < len(_E2E_SLOTS):
            e2e_argv += plan["e2e"]   # 子集：传套件短名过滤
        stages.append({
            "name": "e2e",
            "message": "正在运行浏览器 e2e 测试（Playwright 真实操作）…",
            "argv": e2e_argv,
            "cwd": str(BASE_DIR / "frontend"),
            "ok_rc": {0, 1},   # 有套件失败退出码非 0，报告仍完整，聚合如实呈现
        })

    agg_argv = [python, str(_AGGREGATE_SCRIPT), "--reports-dir", str(tmp_dir)]
    full = bool(plan["unit"] and plan["api"] and plan["e2e"]
                and len(plan["e2e"]) == len(_E2E_SLOTS))
    if not full:
        sections = ([] + (["pytest"] if (plan["unit"] or plan["api"]) else [])
                    + (["e2e"] if plan["e2e"] else []))
        agg_argv += ["--update-sections", ",".join(sections)]
        if not (plan["unit"] and plan["api"]):
            kinds = [k for k, sel in (("unit", plan["unit"]), ("api", plan["api"])) if sel]
            agg_argv += ["--pytest-kinds", ",".join(kinds)]
    stages.append({
        "name": "aggregate",
        "message": "正在聚合质量数据…",
        "argv": agg_argv,
        "cwd": str(BASE_DIR),
        "ok_rc": {0},
    })
    return stages


def _init_suite_state(plan: dict) -> None:
    """e2e 阶段开始前初始化套件实时状态（全部 pending）。"""
    suites = [{
        "name": _E2E_SLOTS[key], "status": "pending", "duration_ms": None,
        "steps_ok": 0, "steps_total": 0, "last_step": "",
    } for key in (plan["e2e"] or [])]
    _set_state(suites=suites)


def _update_suite(fn) -> None:
    """对当前（首个未完成）套件应用 fn(suite)；没有可更新项时忽略。"""
    with _state_lock:
        for s in _run_state["suites"]:
            if s["status"] in ("pending", "running"):
                s["status"] = "running"
                fn(s)
                break


def _on_stage_line(stage: str, line: str) -> None:
    """解析子进程输出行 → 更新运行态进度（pytest 百分比 / e2e 套件与步骤）。"""
    if not line:
        return
    _append_log(line)
    if stage == "pytest":
        m = _PYTEST_PCT_RE.search(line)
        if m:
            pct = min(100, int(m.group(1)))
            hint = _run_state.get("pytest_total_hint")
            _set_state(pytest_percent=pct,
                       pytest_done=int(round(pct / 100 * hint)) if hint else None)
    elif stage == "e2e":
        m = _E2E_SUITE_RE.search(line)
        if m:   # 套件结束行
            script, outcome, ms = m.group(1), m.group(2), int(m.group(3))
            with _state_lock:
                for s in _run_state["suites"]:
                    if s["name"] == script:
                        s["status"] = outcome if outcome in ("passed", "failed", "error") else "failed"
                        s["duration_ms"] = ms
                        break
        elif line.startswith(("✅", "❌")):   # 步骤行 → 归属当前运行中的套件
            def _apply(s: dict) -> None:
                s["steps_total"] += 1
                if line.startswith("✅"):
                    s["steps_ok"] += 1
                s["last_step"] = line.strip()[:80]
            _update_suite(_apply)


def _run_subprocess(stage: dict) -> None:
    """执行单个阶段：Popen 逐行读输出（实时进度），超时杀进程。"""
    proc = subprocess.Popen(
        stage["argv"],
        cwd=stage["cwd"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,   # 合并到 stdout 统一解析，避免 stderr 管道塞死
        text=True,
        errors="replace",
        env={**os.environ, **stage.get("env", {})},
    )
    timer = threading.Timer(QUALITY_EXEC_TIMEOUT, proc.kill)
    timer.start()
    try:
        assert proc.stdout is not None
        for raw in proc.stdout:
            _on_stage_line(stage["name"], raw.rstrip())
        proc.stdout.close()
        rc = proc.wait()
    finally:
        timer.cancel()
    if rc not in stage["ok_rc"]:
        tail = " | ".join(_run_state["log_tail"][-3:])
        raise RuntimeError(f"阶段 {stage['name']} 退出码 {rc}：{tail}")


def _run_pipeline(run_id: str, tmp_dir: Path, plan: dict) -> None:
    """后台线程：顺序执行各阶段 → 更新运行态。任一硬失败即终止并落 failed。"""
    try:
        for stage in _stage_commands(tmp_dir, plan):
            _set_state(stage=stage["name"], message=stage["message"])
            if stage["name"] == "e2e":
                _init_suite_state(plan)
            try:
                _run_subprocess(stage)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"阶段 {stage['name']} 超时（>{QUALITY_EXEC_TIMEOUT}s）")
            except FileNotFoundError as e:
                # 如 node 未安装：该阶段无法执行，按失败处理（如实暴露，不伪造数据）
                raise RuntimeError(f"阶段 {stage['name']} 启动失败：{e}")

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
def start_run(admin: User = Depends(require_admin), req: QualityRunReq | None = None):
    """后台触发一轮测试（范围可选，缺省全量）；已有运行在进行时 409。"""
    plan = _normalize_plan(req)
    with _state_lock:
        if _run_state["status"] == "running":
            raise HTTPException(409, detail="已有一轮测试在运行中，请稍候")
        run_id = uuid.uuid4().hex[:12]
        _run_state.update(run_id=run_id, status="running", stage="prepare",
                          message="正在准备测试环境…", error=None,
                          started_at=_now(), finished_at=None,
                          plan=plan, pytest_percent=None, pytest_done=0,
                          pytest_total_hint=_pytest_total_hint(),
                          suites=[], log_tail=[])
    tmp_dir = Path(tempfile.mkdtemp(prefix="aitf-quality-"))
    threading.Thread(target=_run_pipeline, args=(run_id, tmp_dir, plan),
                     name=f"quality-run-{run_id}", daemon=True).start()
    return {"run_id": run_id, "status": "running", "plan": plan}


@router.get("/quality/run/status")
def run_status(admin: User = Depends(require_admin)):
    """当次（或最近一次）运行状态与实时进度。"""
    with _state_lock:
        return json.loads(json.dumps(_run_state))   # 深拷贝快照，避免竞态


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
