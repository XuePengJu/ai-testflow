"""M2 自动化执行 Runner：subprocess 跑 pytest → 解析报告 → 组装 report_json → 更新终态。

职责边界（contract-m2 4）：
- 由 app/core/exec_queue 的 worker 调 run_execution(run_id)，本模块不碰队列
- subprocess：`.venv 解释器 -m pytest auto/ --json-report ...`，cwd=任务目录，
  no shell=True、timeout=AUTO_EXEC_TIMEOUT、env 白名单（凭据仅经环境变量注入，不落盘不进日志）
- 终态判定：有用例失败=completed；仅进程级崩溃 / 超时 / 报告缺失=failed
"""
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from app.core import config
from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.automation import ExecutionRun, TestTarget, decrypt_credential
from app.models.task import Task

logger = logging.getLogger("auto_runner")

# pytest 进程退出码含义：0=全过 1=有用例失败（含收集错误报告）；其余=进程级异常
_PY_OK_RETURNCODES = (0, 1)
# error 字段最长保留字符数（stderr / longrepr 截断，防止极端 traceback 撑爆 Text 列）
_ERROR_MAX = 4000

# M4 自愈：API 侧登记 auto_heal=False 的 run（显式关闭自愈场景）。
# 进程重启后集合丢失 → 回退默认开启，仅影响显式关闭的那一次执行。
_HEAL_DISABLED: set[str] = set()


def set_auto_heal(run_id: str, enabled: bool) -> None:
    """登记/取消 run 的自愈开关（run-auto 端点在入队前调用）。"""
    if enabled:
        _HEAL_DISABLED.discard(run_id)
    else:
        _HEAL_DISABLED.add(run_id)


def _resolve_heal_client():
    """解析自愈用的 LLM 客户端（用户/平台默认/环境变量）；不可用返回 None（跳过自愈）。"""
    from app.services import llm_service

    db = SessionLocal()
    try:
        eff = llm_service.resolve_effective(db, None)
        cfg = eff.get("text")
        if not cfg or not cfg.get("api_key"):
            return None
        return llm_service.OpenAICompatClient(cfg["base_url"], cfg["api_key"], cfg["model"])
    finally:
        db.close()


def _merge_rerun(report: dict, rerun_report: dict) -> None:
    """自愈重跑报告并入主报告：按 node_id 更新用例结果，重算 summary。"""
    by_node = {c["node_id"]: c for c in rerun_report["cases"] if c.get("node_id")}
    for c in report["cases"]:
        rc = by_node.get(c.get("node_id", ""))
        if not rc:
            continue
        c["outcome"] = rc["outcome"]
        c["error"] = rc.get("error")
        c["screenshot"] = rc.get("screenshot")
        c["duration_ms"] = c.get("duration_ms", 0) + rc.get("duration_ms", 0)
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for c in report["cases"]:
        if c["outcome"] in counts:
            counts[c["outcome"]] += 1
    report["summary"]["passed"] = counts["passed"]
    report["summary"]["failed"] = counts["failed"]
    report["summary"]["skipped"] = counts["skipped"]


def _run_heal_flow(db, run: ExecutionRun, task, target, report: dict,
                   auto_path: Path, mapping: dict, task_cases: list[dict]) -> dict | None:
    """自愈循环入口：失败用例存在且 LLM 可用时执行，返回 report_json.heal 结构（无则 None）。"""
    from app.services.auto_healer import heal

    client = _resolve_heal_client()
    if client is None:
        logger.info("自愈跳过：无可用文本模型（run=%s）", run.id)
        return None

    failed_items = [dict(c) for c in report["cases"] if c.get("outcome") == "failed"]
    if not failed_items:
        return None
    logger.info("启动自愈: run=%s 失败用例 %d 条（上限 %d 轮）",
                run.id, len(failed_items), config.AUTO_HEAL_ROUNDS)
    result = heal(
        run, failed_items, auto_path, client,
        context={"mapping": mapping, "task_cases": task_cases,
                 "base_url": (target.base_url or "") if target else "", "target": target},
    )
    # 重跑报告并入主报告（修复成功的用例 outcome 更新为 passed）
    if result.last_rerun_path:
        rerun_abs = auto_path.parent / result.last_rerun_path
        if rerun_abs.is_file():
            try:
                rerun_report = _parse_report(
                    rerun_abs, mapping, task_cases,
                    (target.base_url or "") if target else "")
                _merge_rerun(report, rerun_report)
            except (ValueError, OSError) as e:
                logger.warning("自愈重跑报告并入失败: %s", e)
    report.pop("_recount", None)
    heal_meta = {
        "rounds": result.rounds,
        "log": result.log,
        "suspected_bugs": result.suspected_bugs,
    }
    report["heal"] = heal_meta
    # run 字段兜底同步（heal 内部已按轮落库，这里保证最终态一致）
    run.heal_round = result.rounds
    run.heal_log = json.dumps(result.log, ensure_ascii=False)
    logger.info("自愈结束: run=%s 轮次=%d fixed=%s suspected=%d",
                run.id, result.rounds, result.fixed, len(result.suspected_bugs))
    return heal_meta


def resolve_auto_dir(run: ExecutionRun) -> Path:
    """run.auto_dir（相对 AITF_ROOT_DIR 的路径，如 outputs/u_1/{task_id}/auto）→ 绝对路径。"""
    return config.OUTPUT_DIR.parent / run.auto_dir


def _node_relative(nodeid: str) -> str:
    """pytest nodeid（相对任务目录，如 auto/test_cases_0.py::test_tc_001）
    → 契约形态 test_cases_0.py::test_tc_001（剥掉 auto/ 前缀）。"""
    nodeid = (nodeid or "").strip()
    prefix = "auto/"
    if nodeid.startswith(prefix):
        nodeid = nodeid[len(prefix):]
    return nodeid


def _safe_shot_name(node_id: str) -> str:
    """node_id 安全化 → 截图文件名（test_tc_001 → test_tc_001.png 基础上带模块前缀）。

    ⚠️ 与 conftest 模板（m2-scripter 生成）的失败截图命名规则必须一致：
    非安全字符（非字母数字 . _ -）一律替换为下划线。
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", node_id)


def _node_func(node_id: str) -> str:
    """node_id → 纯函数名：剥掉模块路径与 [chromium] 参数化后缀。"""
    return (node_id or "").split("::")[-1].split("[")[0]


def _find_screenshot(shots_dir: Path, node_id: str) -> str | None:
    """定位失败截图（相对 auto_dir 的路径，如 shots/xxx.png），找不到返回 None。

    优先精确匹配安全化文件名（node_id / auto/+node_id 两种前缀都试）；
    兜底按测试函数名匹配（兼容 conftest 命名差异：pytest-playwright 参数化后缀、
    完整 nodeid 含 auto/ 前缀都会被归一化后命中，见 contract-m2 联调裁决 2）。
    """
    if not shots_dir.is_dir():
        return None
    for cand in (node_id, f"auto/{node_id}"):
        exact = shots_dir / f"{_safe_shot_name(cand)}.png"
        if exact.is_file():
            return f"shots/{exact.name}"
    func = _node_func(node_id)
    if not func:
        return None
    for p in sorted(shots_dir.glob("*.png")):
        stem = p.stem
        # 归一化：剥 conftest 实际用的 auto/ 前缀（安全化成 auto_）；再按函数名匹配
        if stem.startswith("auto_"):
            stem = stem[len("auto_"):]
        # 负向后行断言：函数名后不能紧跟字母/数字（防止 test_tc_001 误配 test_tc_0010）
        if re.search(re.escape(func) + r"(?![A-Za-z0-9])", stem):
            return f"shots/{p.name}"
    return None


def _case_title(cases: list[dict], case_id: str) -> str:
    """按 case_id 从任务用例表里取复合标题；缺失时回退 case_id 本身。"""
    for c in cases:
        if (c.get("case_id") or "") == case_id:
            return (c.get("title") or "").strip() or case_id
    return case_id


def _stage_longrepr(test: dict) -> str | None:
    """从 pytest-json-report 的单用例对象提取失败/跳过原因（setup→call→teardown 顺序）。"""
    for stage in ("setup", "call", "teardown"):
        lr = (test.get(stage) or {}).get("longrepr")
        if lr:
            text = lr if isinstance(lr, str) else json.dumps(lr, ensure_ascii=False)
            return text[:_ERROR_MAX]
    return None


def _stage_ms(test: dict) -> int:
    """单用例耗时 = setup + call + teardown 三个阶段之和（秒 → 毫秒）。"""
    total = sum(float((test.get(s) or {}).get("duration") or 0) for s in ("setup", "call", "teardown"))
    return int(round(total * 1000))


def _build_env(task_dir: Path, auto_path: Path, target: TestTarget | None) -> dict[str, str]:
    """构建子进程环境变量（白名单，contract-m2 4）。

    凭据解密后仅经 AITF_AUTH_USER/AITF_AUTH_PASS 传给子进程，不落盘、不进任何日志。
    """
    keep = {}
    for key in ("PATH", "HOME", "PYTHONPATH"):
        if os.environ.get(key):
            keep[key] = os.environ[key]
    # 项目根加入 PYTHONPATH：conftest / 测试脚本可 import 项目共享模块
    root = str(config.BASE_DIR)
    keep["PYTHONPATH"] = f"{root}{os.pathsep}{keep['PYTHONPATH']}" if keep.get("PYTHONPATH") else root

    base_url = (target.base_url or "") if target else ""
    keep["AITF_BASE_URL"] = base_url
    keep["AITF_SHOTS_DIR"] = str(auto_path / "shots")
    storage_state = auto_path / "storage_state.json"
    if not storage_state.is_file():
        # explore 任务：探索 Agent 登录后归档在 explore/storage_state.json，执行时复用登录态
        explore_state = task_dir / "explore" / "storage_state.json"
        if explore_state.is_file():
            storage_state = explore_state
    if storage_state.is_file():
        keep["AITF_STORAGE_STATE"] = str(storage_state)
    if target and target.auth_type != "none":
        keep["AITF_AUTH_USER"] = decrypt_credential(target.username_enc)
        keep["AITF_AUTH_PASS"] = decrypt_credential(target.password_enc)
    return keep


def _parse_report(report_path: Path, mapping: dict[str, str],
                  task_cases: list[dict], base_url: str) -> dict:
    """解析 pytest-json-report 输出 + mapping.json → 组装契约 2 的 report_json 结构。"""
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    summary_raw = raw.get("summary") or {}
    shots_dir = report_path.parent / "shots"

    cases: list[dict] = []
    counts = {"passed": 0, "failed": 0, "skipped": 0}
    for test in raw.get("tests") or []:
        node_id = _node_relative(test.get("nodeid", ""))
        outcome = test.get("outcome") or "skipped"
        if outcome in counts:
            counts[outcome] += 1
        func_name = _node_func(node_id)  # 剥 [chromium] 参数化后缀再查映射
        case_id = mapping.get(func_name, "")
        cases.append({
            "case_id": case_id or func_name,
            "node_id": node_id,
            "title": _case_title(task_cases, case_id) if case_id else func_name,
            "outcome": outcome,
            "duration_ms": _stage_ms(test),
            "error": _stage_longrepr(test) if outcome == "failed" else None,
            "screenshot": _find_screenshot(shots_dir, node_id) if outcome == "failed" else None,
        })

    # skipped 补齐（contract-m2 联调裁决 5）：脚本批次缺失时，cases_json 中
    # 有 case_id 但报告无对应 node 的用例按 skipped 落账，保证 total 一致
    reported_ids = {c["case_id"] for c in cases}
    for tc in task_cases:
        tc_id = (tc.get("case_id") or "").strip()
        if tc_id and tc_id not in reported_ids:
            cases.append({
                "case_id": tc_id,
                "node_id": "",
                "title": _case_title(task_cases, tc_id),
                "outcome": "skipped",
                "duration_ms": 0,
                "error": "脚本生成失败",
                "screenshot": None,
            })

    return {
        "summary": {
            "total": len(cases),
            "passed": counts["passed"],
            "failed": counts["failed"],
            "skipped": sum(1 for c in cases if c["outcome"] == "skipped"),
            "duration_ms": int(round(float(summary_raw.get("duration") or 0) * 1000)),
        },
        "cases": cases,
        "environment": {"browser": "chromium", "base_url": base_url},
    }


def _finish(db, run: ExecutionRun, *, status: str, error: str | None = None,
            report: dict | None = None) -> None:
    """统一落终态（幂等：已终态不再改）。"""
    run.status = status
    run.error = (error or None)
    run.finished_at = utcnow()
    if report is not None:
        s = report["summary"]
        run.progress = s["total"]
        run.total = s["total"]
        run.passed = s["passed"]
        run.failed = s["failed"]
        run.skipped = s["skipped"]
        run.duration_ms = s["duration_ms"]
        run.report_json = json.dumps(report, ensure_ascii=False)
    db.commit()


def run_execution(run_id: str) -> None:
    """执行一轮自动化测试（exec_queue worker 入口）。

    全程兜底：任何异常都把 run 置为 failed，绝不让 worker 循环崩掉。
    """
    db = SessionLocal()
    task_dir: Path | None = None
    try:
        run = db.get(ExecutionRun, run_id)
        if not run:
            logger.warning("执行记录不存在，跳过: %s", run_id)
            return
        task = db.get(Task, run.task_id)
        target = db.get(TestTarget, task.target_id) if task and task.target_id else None

        auto_path = resolve_auto_dir(run)
        task_dir = auto_path.parent
        if not auto_path.is_dir():
            _finish(db, run, status="failed", error=f"脚本目录不存在: {run.auto_dir}（请先生成自动化脚本）")
            return

        # 用例总数：mapping.json（脚本侧真值）优先，缺失回退任务用例数
        mapping: dict[str, str] = {}
        mapping_path = auto_path / "mapping.json"
        if mapping_path.is_file():
            try:
                mapping = json.loads(mapping_path.read_text(encoding="utf-8")) or {}
            except ValueError:
                logger.warning("mapping.json 解析失败，按无映射处理: %s", mapping_path)
        task_cases: list[dict] = []
        if task and task.cases_json:
            try:
                obj = json.loads(task.cases_json)
                task_cases = obj if isinstance(obj, list) else []
            except ValueError:
                task_cases = []
        total = len(mapping) or len(task_cases)

        run.status = "running"
        run.started_at = utcnow()
        run.total = total
        run.progress = 0
        db.commit()

        (auto_path / "shots").mkdir(parents=True, exist_ok=True)
        env = _build_env(task_dir, auto_path, target)
        cmd = [
            sys.executable, "-m", "pytest", "auto/",
            "--json-report", "--json-report-file=auto/report.json",
            "--browser=chromium",
        ]
        t0 = time.time()
        try:
            proc = subprocess.run(
                cmd, cwd=str(task_dir), env=env, shell=False,
                capture_output=True, text=True, timeout=config.AUTO_EXEC_TIMEOUT,
            )
            returncode, stderr = proc.returncode, (proc.stderr or "")
        except subprocess.TimeoutExpired:
            _finish(db, run, status="failed",
                    error=f"执行超时（>{config.AUTO_EXEC_TIMEOUT}s），进程已被终止")
            return

        report_path = auto_path / "report.json"
        if returncode not in _PY_OK_RETURNCODES or not report_path.is_file():
            # 进程级崩溃：使用错误 / 内部错误 / 未收集到任何用例 / 报告缺失
            detail = stderr.strip()[-_ERROR_MAX:] or f"pytest 退出码 {returncode}"
            hint = "（未收集到任何测试用例，请检查脚本）" if returncode == 5 else ""
            _finish(db, run, status="failed", error=f"{detail}{hint}")
            return

        try:
            report = _parse_report(report_path, mapping, task_cases,
                                   (target.base_url or "") if target else "")
        except (ValueError, OSError) as e:
            _finish(db, run, status="failed", error=f"执行报告解析失败: {e}")
            return

        # pytest 报告缺 summary.duration 时（部分插件版本不输出），用 run 起止时间兜底
        if not report["summary"]["duration_ms"] and run.started_at:
            elapsed = max(0.0, (utcnow() - run.started_at).total_seconds())
            report["summary"]["duration_ms"] = max(1, int(round(elapsed * 1000)))

        logger.info("执行完成: run=%s task=%s 用例 %d 条 (passed=%d failed=%d skipped=%d) 耗时 %.1fs",
                    run_id, run.task_id, report["summary"]["total"],
                    report["summary"]["passed"], report["summary"]["failed"],
                    report["summary"]["skipped"], time.time() - t0)

        # M4 自愈循环（V5.0 4.3）：有用例失败且启用自愈 → LLM 诊断-修复-重跑 ≤AUTO_HEAL_ROUNDS 轮。
        # run 保持 running（前端轮询可见 heal_round 递增），结束后统一 _finish 落终态。
        if (report["summary"]["failed"] > 0 and run_id not in _HEAL_DISABLED
                and config.AUTO_HEAL_ROUNDS > 0):
            try:
                _run_heal_flow(db, run, task, target, report, auto_path, mapping, task_cases)
            except Exception as e:  # noqa: BLE001  自愈失败不影响执行结果如实落库
                logger.exception("自愈流程异常（不影响结果落库）: %s", run_id)
                report.pop("heal", None)

        # 有用例失败也按契约归为 completed，报告如实呈现
        _finish(db, run, status="completed", report=report)
    except Exception as e:  # noqa: BLE001  worker 兜底：任何异常都不能打断执行循环
        logger.exception("执行异常: %s", run_id)
        try:
            run = db.get(ExecutionRun, run_id)
            if run and run.status in ("pending", "running"):
                _finish(db, run, status="failed", error=f"执行器内部错误: {e}")
        except Exception:  # noqa: BLE001
            logger.exception("终态兜底写入失败: %s", run_id)
    finally:
        db.close()
