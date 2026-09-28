"""M4 自愈循环（V5.0 4.3）：执行失败后 LLM 自动「取证 → 诊断 → 修复 → 重跑」。

职责边界：
- 由 auto_runner 在首轮执行完成后调用 heal()（失败用例存在且 auto_heal 开启时）
- 只修脚本（选择器/等待/定位），绝不放松断言——诊断 prompt 硬约束 + 代码级断言保护双防线
- 根因四分类：selector（选择器失效）/ timing（等待不足）/ env（环境问题，不可修）
  / product_bug（页面行为符合需求但断言失败，疑似真缺陷）
- 本版本不做视觉输入：截图仅记录路径供人工核对，prompt 预留升级位（签名保留 screenshot）
- 修复文件：LLM 输出完整文件内容 → ast.parse 校验 → 断言保护比对 → 旧文件备份 → 覆盖写回
"""
import ast
import json
import logging
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core import config
from app.core.db import SessionLocal
from app.core.utils import utcnow
from app.models.task import StepLog

logger = logging.getLogger("auto_healer")

# 报错/源码片段喂给 LLM 的截断上限（防极端 traceback 撑爆上下文）
_EVIDENCE_MAX = 2500
# 诊断 JSON 解析失败重试次数
_DIAG_RETRY = 1

# 诊断 prompt 硬约束（对齐文档 4.3：绝不放松断言）
_DIAG_SYSTEM = """你是资深 Web 自动化测试工程师，负责诊断并修复 pytest + playwright 测试脚本的失败。

【硬性铁律——违反即报废】
1. 绝对禁止修改、删除、弱化任何 assert 断言语句（包括断言的期望值与消息）
2. 绝对禁止通过「改预期让它通过」的方式修复失败——那会把真缺陷掩盖掉
3. 只允许修复：元素选择器（selector）、等待时间/显式等待（timing）、定位方式

【根因四分类】
- selector：选择器失效/页面结构变化（可修复）
- timing  ：等待不足导致元素未就绪（可修复）
- env     ：环境/测试数据问题，脚本本身没毛病（不可修，如实报告）
- product_bug：断言失败但页面行为符合需求描述，疑似被测系统真缺陷（不可修，如实报告）

【输出格式】只输出一个 JSON 对象，不要任何多余文字或 markdown 代码块：
{"root_cause": "selector|timing|env|product_bug",
 "analysis": "一句话根因分析（中文）",
 "fixed_file": "仅 root_cause 为 selector/timing 时给出需修复的脚本文件名（如 test_cases_0.py）",
 "fixed_content": "仅 root_cause 为 selector/timing 时给出修复后的完整文件内容，其余情况留空串"}
"""

# 诊断 JSON 的字段校验：root_cause 必须四选一
_VALID_ROOT_CAUSES = ("selector", "timing", "env", "product_bug")


@dataclass
class HealResult:
    """自愈结果（供 auto_runner 写入 report_json.heal 与 run 字段）。"""
    rounds: int = 0                                  # 实际进行的轮次
    log: list[dict] = field(default_factory=list)    # 每轮明细（契约见文档 4.2）
    suspected_bugs: list[dict] = field(default_factory=list)  # 疑似真缺陷/环境问题
    fixed: bool = False                              # 失败用例是否全部修复通过
    changed_files: list[str] = field(default_factory=list)    # 全程改过的文件
    last_rerun_path: str | None = None               # 最后一轮重跑报告路径（相对任务目录）


# ============ 取证 ============

def _func_source(auto_path: Path, node_id: str) -> str:
    """node_id（test_cases_0.py::test_tc_002）→ 对应测试函数的源码片段。"""
    module, _, func = node_id.partition("::")
    func = func.split("[")[0]  # 剥参数化后缀
    src_file = auto_path / module
    if not src_file.is_file():
        return ""
    try:
        src = src_file.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (OSError, SyntaxError, ValueError):
        return ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            return (ast.get_source_segment(src, node) or "")[:_EVIDENCE_MAX]
    return ""


def _load_pages_summary(task_dir: Path) -> str:
    """任务目录 pages.json（PageDesc 列表）→ 纯文本页面结构摘要（PageDesc 是
    crawler 落盘的当时页面快照，供 LLM 对照判断选择器是否因页面改版失效）。"""
    pages_file = task_dir / "pages.json"
    if not pages_file.is_file():
        return "（无页面结构快照）"
    try:
        pages = json.loads(pages_file.read_text(encoding="utf-8"))
        if not isinstance(pages, list):
            return "（页面结构快照格式异常）"
    except (ValueError, OSError):
        return "（页面结构快照不可读）"
    lines = []
    for p in pages[:config.CRAWL_MAX_PAGES]:
        if not isinstance(p, dict):
            continue
        head = f"页面：{p.get('title') or '(无标题)'}  URL：{p.get('url', '')}"
        forms = p.get("forms") or []
        fields_txt = "; ".join(
            f.name for f_desc in forms for f in (f_desc.get("fields") or [])
            if isinstance(f_desc, dict) and isinstance(f, dict) and f.get("name")
        ) if forms else ""
        parts = [head]
        if p.get("buttons"):
            parts.append(f"按钮：{'、'.join(map(str, p['buttons'][:10]))}")
        if fields_txt:
            parts.append(f"表单字段：{fields_txt[:300]}")
        lines.append("\n".join(parts))
    return ("\n\n".join(lines))[:4000] or "（页面结构快照为空）"


def _gather_suspects(auto_path: Path, failed_items: list[dict]) -> list[dict]:
    """失败用例取证：case_id + pytest 报错全文 + 失败截图路径 + 脚本源码片段。"""
    suspects = []
    for item in failed_items:
        suspects.append({
            "case_id": item.get("case_id") or item.get("node_id", ""),
            "node_id": item.get("node_id", ""),
            "error": (item.get("error") or "（无报错文本）")[:_EVIDENCE_MAX],
            "screenshot": item.get("screenshot") or "",   # 本版本不做视觉输入，路径供人工核对
            "source": _func_source(auto_path, item.get("node_id", "")),
        })
    return suspects


def _build_prompt(suspects: list[dict], pages_summary: str) -> str:
    """诊断 prompt：取证材料 + 页面结构摘要（视觉输入升级位：screenshot 字段在此注入）。"""
    evidence = json.dumps(suspects, ensure_ascii=False, indent=1)
    return f"""【失败用例取证材料】
{evidence}

【当时的页面结构快照（crawler 抓取）】
{pages_summary}

请按系统提示的规则诊断根因。selector/timing 类必须给出修复后的完整文件内容；注意修复时保留文件里所有 assert 断言原样不动。"""


# ============ 诊断（LLM，JSON 解析 + 校验 + 重试 1 次） ============

def _extract_json(text: str) -> dict:
    """LLM 输出 → JSON 对象（容忍 markdown 代码块/前后杂文字）。解析失败抛 ValueError。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出中没有 JSON 对象")
    obj = json.loads(text[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("输出不是 JSON 对象")
    return obj


def _validate_diagnosis(diag: dict, script_files: set[str]) -> str | None:
    """诊断 JSON 结构校验。返回 None=合法，否则返回错误说明。"""
    rc = diag.get("root_cause")
    if rc not in _VALID_ROOT_CAUSES:
        return f"root_cause 非法: {rc!r}"
    if not str(diag.get("analysis") or "").strip():
        return "缺少 analysis"
    if rc in ("selector", "timing"):
        f = diag.get("fixed_file") or ""
        # 路径安全：只允许 auto 目录下已存在的裸文件名，杜绝 LLM 输出路径穿越
        if not f or Path(f).name != f or f not in script_files:
            return f"fixed_file 非法: {f!r}（必须是脚本目录下已存在的文件名）"
        if not str(diag.get("fixed_content") or "").strip():
            return "selector/timing 类必须给出 fixed_content"
    return None


def _diagnose(llm_client, prompt: str, script_files: set[str]) -> dict:
    """调用 LLM 诊断，JSON 解析/校验失败重试 1 次；两次都失败抛 LLMError。"""
    from app.services.langchain_client import LLMError

    last_err = ""
    for attempt in range(_DIAG_RETRY + 1):
        try:
            raw = llm_client.chat(
                [{"role": "system", "content": _DIAG_SYSTEM},
                 {"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=8192,
            )
            diag = _extract_json(raw)
            err = _validate_diagnosis(diag, script_files)
            if err:
                raise ValueError(err)
            return diag
        except (ValueError, LLMError) as e:
            last_err = str(e)[:200]
            logger.warning("诊断输出解析/校验失败（第 %d 次）: %s", attempt + 1, last_err)
    raise LLMError(f"诊断 JSON 两次解析/校验失败: {last_err}")


# ============ 断言保护（代码级防线，不信任 LLM 自觉） ============

def _assert_signatures(src: str) -> list[str]:
    """提取文件中所有 assert 语句的规范化源码文本（空白折叠）。"""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    sigs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            seg = ast.get_source_segment(src, node)
            if seg:
                sigs.append(" ".join(seg.split()))
    return sigs


def _assertions_preserved(old_src: str, new_src: str) -> bool:
    """断言保护：旧文件里每一条 assert 必须在新文件中原样存在（改期望值/删除都会被拒）。
    新增 assert 允许（加强校验不算放松）。旧文件解析失败时保守放行（交由 ast 校验兜底）。"""
    old_sigs = _assert_signatures(old_src)
    if not old_sigs:
        return True
    new_sigs = set(_assert_signatures(new_src))
    return all(sig in new_sigs for sig in old_sigs)


# ============ 重跑（只跑失败用例的 node id） ============

def _rerun_failed(task_dir: Path, env: dict[str, str], node_ids: list[str],
                  report_rel: str) -> Path | None:
    """pytest 只跑指定 node id（复用 auto_runner 的 subprocess 模式），返回报告路径。"""
    cmd = [
        sys.executable, "-m", "pytest", *node_ids,
        "--json-report", f"--json-report-file={report_rel}",
        "--browser=chromium",
    ]
    try:
        proc = subprocess.run(
            cmd, cwd=str(task_dir), env=env, shell=False,
            capture_output=True, text=True, timeout=config.AUTO_EXEC_TIMEOUT,
        )
        logger.info("自愈重跑: node=%s 退出码=%d", node_ids, proc.returncode)
    except subprocess.TimeoutExpired:
        logger.error("自愈重跑超时（>%ds）", config.AUTO_EXEC_TIMEOUT)
        return None
    report = task_dir / report_rel
    return report if report.is_file() else None


# ============ 单轮过程 ============

def _backup_file(auto_path: Path, round_no: int, filename: str) -> None:
    """修复前旧文件备份到 auto/heal_backup/round{n}/。"""
    src = auto_path / filename
    dst_dir = auto_path / "heal_backup" / f"round{round_no}"
    dst_dir.mkdir(parents=True, exist_ok=True)
    (dst_dir / filename).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _persist_round(run, db, entry: dict) -> None:
    """每轮结束即时落库：run.heal_round / heal_log + StepLog「自愈第 N 轮」可观测。"""
    run.heal_round = entry["round"]
    run.heal_log = json.dumps(_log_of(run) + [entry], ensure_ascii=False)
    step = StepLog(
        task_id=run.task_id, name="auto_heal", title=f"自愈第 {entry['round']} 轮",
        status="completed" if entry.get("rerun_outcome") == "passed" else "running",
        progress=json.dumps({
            "diagnosis": entry.get("diagnosis", {}).get("analysis", ""),
            "root_cause": entry.get("diagnosis", {}).get("root_cause", ""),
            "changed_files": entry.get("changed_files", []),
            "rerun_outcome": entry.get("rerun_outcome", ""),
        }, ensure_ascii=False),
        started_at=utcnow(), finished_at=utcnow(),
    )
    db.add(step)
    db.commit()


def _log_of(run) -> list:
    try:
        obj = json.loads(run.heal_log or "[]")
        return obj if isinstance(obj, list) else []
    except ValueError:
        return []


# ============ 主入口 ============

def heal(run, failed_items: list[dict], auto_dir: Path, llm_client,
         context: dict | None = None) -> HealResult:
    """自愈循环（≤ config.AUTO_HEAL_ROUNDS 轮）。

    参数：
        run          : ExecutionRun（须已绑定 db 会话，供每轮即时落库）
        failed_items : 首轮失败用例列表（report_json cases 的 failed 子集）
        auto_dir     : 脚本目录绝对路径（…/{task_id}/auto）
        llm_client   : OpenAI 兼容客户端（须实现 chat(messages) -> str）
        context      : {"mapping": dict, "task_cases": list, "base_url": str,
                        "target": TestTarget|None}（供重跑环境变量与报告组装）

    收敛：全部通过 → fixed=True；product_bug/env/轮次耗尽 → suspected_bugs 记录后停止。
    """
    ctx = context or {}
    auto_path = Path(auto_dir)
    task_dir = auto_path.parent
    result = HealResult()

    db = SessionLocal()
    try:
        # run 绑定的会话（auto_runner 传入时已 attached）；未 attached 则退回本地会话
        from sqlalchemy.orm import object_session
        run_db = object_session(run) or db

        script_files = {p.name for p in auto_path.glob("*.py")}
        env_builder = None
        target = ctx.get("target")
        if target is not None:
            from app.services.auto_runner import _build_env
            env_builder = _build_env(task_dir, auto_path, target)

        pending = [dict(x) for x in failed_items]  # 深拷贝，轮间维护"仍未通过"集合

        for round_no in range(1, max(1, config.AUTO_HEAL_ROUNDS) + 1):
            if not pending:
                break
            # 1. 取证
            suspects = _gather_suspects(auto_path, pending)
            prompt = _build_prompt(suspects, _load_pages_summary(task_dir))
            # 2. 诊断
            try:
                diag = _diagnose(llm_client, prompt, script_files)
            except Exception as e:  # noqa: BLE001  LLMError 等：诊断失败按 env 收敛
                logger.error("第 %d 轮诊断失败: %s", round_no, e)
                entry = {"round": round_no, "suspects": [
                    {k: s[k] for k in ("case_id", "error", "screenshot")} for s in suspects],
                    "diagnosis": {"root_cause": "env", "analysis": f"诊断调用失败: {e}"},
                    "changed_files": [], "rerun_outcome": "diagnosis_failed"}
                result.log.append(entry)
                _persist_round(run, run_db, entry)
                for s in suspects:
                    result.suspected_bugs.append({
                        "case_id": s["case_id"], "kind": "env",
                        "reason": f"自愈诊断调用失败: {str(e)[:200]}"})
                pending = []   # 已收敛，避免末尾轮次耗尽兜底重复记录
                break

            analysis = str(diag.get("analysis") or "").strip()
            entry = {"round": round_no, "suspects": [
                {k: s[k] for k in ("case_id", "error", "screenshot")} for s in suspects],
                "diagnosis": {"root_cause": diag.get("root_cause"), "analysis": analysis},
                "changed_files": [], "rerun_outcome": ""}

            # 3. 收敛判定：product_bug / env 不可修 → 记录疑似缺陷并停止
            if diag["root_cause"] in ("product_bug", "env"):
                entry["rerun_outcome"] = "skipped"
                result.log.append(entry)
                _persist_round(run, run_db, entry)
                kind = diag["root_cause"]
                for s in suspects:
                    result.suspected_bugs.append({
                        "case_id": s["case_id"], "kind": kind, "reason": analysis})
                pending = []   # 已收敛，避免末尾轮次耗尽兜底重复记录
                logger.info("第 %d 轮诊断为 %s，停止自愈: %s", round_no, kind, analysis)
                break

            # 4. 修复：ast 校验 + 断言保护 → 备份 → 覆盖写回
            filename = diag["fixed_file"]
            new_content = str(diag.get("fixed_content") or "")
            target_file = auto_path / filename
            old_src = target_file.read_text(encoding="utf-8")
            try:
                ast.parse(new_content)
            except SyntaxError as e:
                entry["rerun_outcome"] = f"rejected_syntax_error: {e}"
                result.log.append(entry)
                _persist_round(run, run_db, entry)
                continue  # 本轮作废，进入下一轮
            if not _assertions_preserved(old_src, new_content):
                # 断言保护防线：LLM 试图修改/删除断言 → 拒绝写回
                entry["rerun_outcome"] = "rejected_assert_change"
                result.log.append(entry)
                _persist_round(run, run_db, entry)
                logger.warning("第 %d 轮修复被拒：LLM 试图修改/删除断言 (%s)", round_no, filename)
                continue

            _backup_file(auto_path, round_no, filename)
            target_file.write_text(new_content, encoding="utf-8")
            entry["changed_files"] = [filename]
            result.changed_files.append(filename)

            # 5. 重跑：只跑仍未通过的用例
            node_ids = [f"auto/{it['node_id']}" for it in pending if it.get("node_id")]
            report_rel = f"auto/heal_report_r{round_no}.json"
            rerun_path = _rerun_failed(task_dir, env_builder or {}, node_ids, report_rel)
            result.last_rerun_path = report_rel

            if rerun_path is None:
                entry["rerun_outcome"] = "error"
                result.log.append(entry)
                _persist_round(run, run_db, entry)
                continue

            # 重跑结果并入 pending（passed 的移出失败集合）
            from app.services.auto_runner import _parse_report
            rerun_report = _parse_report(
                rerun_path, ctx.get("mapping") or {}, ctx.get("task_cases") or [],
                ctx.get("base_url") or "")
            still_failed = []
            by_node = {c["node_id"]: c for c in rerun_report["cases"]}
            for it in pending:
                rc = by_node.get(it.get("node_id", ""))
                if rc and rc["outcome"] != "failed":
                    it["outcome"] = rc["outcome"]
                else:
                    still_failed.append(it)
                    if rc:
                        it["error"] = rc.get("error") or it.get("error")
            pending = still_failed
            entry["rerun_outcome"] = "passed" if not pending else "failed"
            result.log.append(entry)
            _persist_round(run, run_db, entry)

            if not pending:
                result.fixed = True
                break

            time.sleep(0)  # 轮间无额外等待（预留：必要时可加退避）

        # 轮次耗尽仍有失败 → 疑似缺陷兜底记录（文档 4.3 收敛判定）
        for it in pending:
            result.suspected_bugs.append({
                "case_id": it.get("case_id") or it.get("node_id", ""), "kind": "exhausted",
                "reason": "自愈轮次耗尽仍未修复"})
        result.rounds = len(result.log)
        return result
    finally:
        db.close()
