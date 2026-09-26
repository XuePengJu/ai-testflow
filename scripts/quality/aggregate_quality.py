"""M0 质量聚合：解析 pytest-json-report + coverage + e2e 报告 → quality-summary.json + history.jsonl。

用法：
    .venv/bin/python scripts/quality/aggregate_quality.py [--reports-dir DIR] [--data-dir DIR]
    .venv/bin/python scripts/quality/aggregate_quality.py --update-sections e2e   # 部分运行

- --reports-dir：pytest-report.json / coverage.json / e2e-report.json 所在目录（默认质量数据目录）
- --data-dir：聚合产物目录（默认 <项目根>/quality_data，可用 AITF_ROOT_DIR 切换）
每次聚合向 history.jsonl 追加一行（带时间戳），供看板趋势图使用。
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# 允许直接脚本运行（不依赖 app 包导入顺序）：读取 config 拿默认目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from app.core.config import QUALITY_DATA_DIR  # noqa: E402


def _load_json(path: Path) -> dict | None:
    """读取 JSON 文件，不存在或解析失败返回 None（采集失败不阻断聚合）。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# 用例类型判定：接口测试 = 函数体直接触达 HTTP 客户端（TestClient fixture / httpx / /api/ 路径）
_API_CALL_RE = re.compile(
    r"(?:client|ac)\s*\.\s*(?:get|post|put|delete|patch|request)\b|httpx|['\"]/?api/", re.IGNORECASE
)
_TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)")


def _build_case_kinds(tests_dir: Path) -> dict[tuple[str, str], str]:
    """扫描 tests/ 源码，按用例函数判定类型 → {(文件名, 函数名): "api"|"unit"}。

    判定规则（函数级，而非文件级——同一文件常混两种）：
    - 函数体内出现 client.get/post…、await ac.…、httpx、"/api/…" → 接口测试（api）
    - 其余（纯函数/服务层/DB 层断言）→ 单元测试（unit）
    """
    kinds: dict[tuple[str, str], str] = {}
    for path in sorted(tests_dir.glob("test_*.py")):
        current: str | None = None
        body: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _TEST_DEF_RE.match(line)
            if m:
                if current is not None:
                    kinds[(path.name, current)] = (
                        "api" if _API_CALL_RE.search("\n".join(body)) else "unit"
                    )
                current, body = m.group(1), [line]
            elif current is not None:
                body.append(line)
        if current is not None:
            kinds[(path.name, current)] = (
                "api" if _API_CALL_RE.search("\n".join(body)) else "unit"
            )
    return kinds


def _agg_pytest(reports_dir: Path, tests_dir: Path | None = None) -> dict:
    """解析 pytest-json-report + coverage.json → pytest 维度汇总。"""
    report = _load_json(reports_dir / "pytest-report.json") or {}
    coverage = _load_json(reports_dir / "coverage.json") or {}

    tests = report.get("tests") or []
    kinds = _build_case_kinds(tests_dir) if tests_dir and tests_dir.exists() else {}
    by_file: dict[str, dict] = {}
    cases: list[dict] = []
    failures: list[dict] = []
    passed = failed = skipped = 0
    for t in tests:
        nodeid: str = t.get("nodeid", "")
        outcome = t.get("outcome", "unknown")
        fpath = nodeid.split("::", 1)[0] if nodeid else "(unknown)"
        bucket = by_file.setdefault(fpath, {"total": 0, "passed": 0})
        bucket["total"] += 1
        if outcome == "passed":
            passed += 1
            bucket["passed"] += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            failed += 1
            # 失败详情：call 阶段的 longrepr（截断，避免报告过大）
            call = t.get("call") or {}
            longrepr = str(call.get("longrepr", ""))[:300]
            failures.append({"file": fpath, "test": nodeid, "message": longrepr})
        # 用例明细：拆出函数名与参数化后缀（nodeid 形如 file::func[a-b-c]）
        tail = nodeid.rsplit("::", 1)[-1] if nodeid else ""
        name, _, param = tail.partition("[")
        fname = Path(fpath).name
        cases.append({
            "file": fpath,
            "name": name,
            "param": param.rstrip("]"),
            "outcome": outcome,
            "duration_ms": int(round((t.get("duration") or 0) * 1000)),
            "kind": kinds.get((fname, name), ""),
        })

    cov_totals = coverage.get("totals") or {}
    coverage_pct = round(float(cov_totals.get("percent_covered", 0.0)), 1)

    total = report.get("total", len(tests))
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "duration_ms": int(round((report.get("duration") or 0) * 1000)),
        "coverage_pct": coverage_pct,
        "api_cases": sum(1 for c in cases if c["kind"] == "api"),
        "unit_cases": sum(1 for c in cases if c["kind"] == "unit"),
        "by_file": by_file,
        "cases": cases,
        "failures": failures,
    }


def _agg_e2e(reports_dir: Path) -> dict:
    """解析 run-e2e.mjs 产出的 e2e-report.json → e2e 维度汇总（缺文件 = 未运行）。"""
    report = _load_json(reports_dir / "e2e-report.json") or {}
    suites = report.get("suites") or []
    passed = sum(1 for s in suites if s.get("outcome") == "passed")
    return {
        "suites": suites,
        "total": report.get("total", len(suites)),
        "passed": passed,
        "pass_rate": round(passed / len(suites) * 100, 1) if suites else 0.0,
        "duration_ms": sum(int(s.get("duration_ms") or 0) for s in suites),
    }


def _pytest_stats(cases: list[dict], coverage_pct: float, duration_ms: int,
                  failures: list[dict]) -> dict:
    """从用例明细重算 pytest 段汇总（分段合并后总量仍如实）。"""
    total = len(cases)
    passed = sum(1 for c in cases if c["outcome"] == "passed")
    skipped = sum(1 for c in cases if c["outcome"] == "skipped")
    failed = sum(1 for c in cases if c["outcome"] not in ("passed", "skipped"))
    by_file: dict[str, dict] = {}
    for c in cases:
        bucket = by_file.setdefault(c["file"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        if c["outcome"] == "passed":
            bucket["passed"] += 1
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "pass_rate": round(passed / total * 100, 1) if total else 0.0,
        "duration_ms": duration_ms,
        "coverage_pct": coverage_pct,
        "api_cases": sum(1 for c in cases if c.get("kind") == "api"),
        "unit_cases": sum(1 for c in cases if c.get("kind") == "unit"),
        "by_file": by_file,
        "cases": cases,
        "failures": failures,
    }


def aggregate(reports_dir: Path, data_dir: Path, update_sections: set[str] | None = None,
              pytest_kinds: set[str] | None = None, full_run: bool = False) -> dict:
    """聚合原始报告 → 写 quality-summary.json，返回摘要。

    update_sections：本次真实产出报告的分段（pytest/e2e 子集）；None = 两段全量。
    pytest_kinds：pytest 更新涉及的类型段（api/unit 子集）；部分 pytest 运行时
      仅替换对应 kind 的用例明细，另一 kind 保留旧数据——总量不失真，
      各 kind 采集时间记录在 segments 字段。coverage 部分运行不可比，保留旧值。
    e2e 按套件合并：本次跑过的套件替换旧记录，未跑的保留，套件带 collected_at。
    history 每次运行都追加趋势点：全量点无标记，部分运行点带 partial=True + scopes
    （如 ["pytest:api", "e2e"]），前端以空心点/虚线段区分展示。
    """
    now = datetime.now(timezone.utc).isoformat()
    fresh_p = _agg_pytest(reports_dir, tests_dir=_PROJECT_ROOT / "tests")
    fresh_e = _agg_e2e(reports_dir)
    prev = _load_json(data_dir / "quality-summary.json") or {}
    updating_pytest = update_sections is None or "pytest" in update_sections
    updating_e2e = update_sections is None or "e2e" in update_sections

    # ---- pytest 分段（kind 粒度合并）----
    kinds_all = {"api", "unit"}
    kinds_now = kinds_all if pytest_kinds is None else (pytest_kinds & kinds_all) or kinds_all
    prev_p = prev.get("pytest")
    if updating_pytest and not full_run and prev_p and prev_p.get("cases") is not None:
        kinds = _build_case_kinds(_PROJECT_ROOT / "tests")
        kept_cases = [c for c in prev_p["cases"] if c.get("kind") not in kinds_now]
        kept_failures = []
        for f in prev_p.get("failures") or []:
            fname = Path((f.get("test") or "").split("::", 1)[0]).name
            func = (f.get("test") or "").rsplit("::", 1)[-1].partition("[")[0]
            if kinds.get((fname, func)) not in kinds_now:
                kept_failures.append(f)
        cases = kept_cases + fresh_p["cases"]
        failures = kept_failures + fresh_p["failures"]
        # 覆盖率部分运行不可比（只执行了子集），保留旧值
        pytest_seg = _pytest_stats(cases, prev_p.get("coverage_pct", fresh_p["coverage_pct"]),
                                   fresh_p["duration_ms"], failures)
        segments = {**(prev_p.get("segments") or {}), **{k: now for k in kinds_now}}
    elif updating_pytest:
        pytest_seg = fresh_p
        segments = {k: now for k in kinds_all}
    else:
        pytest_seg = prev_p or fresh_p
        segments = (prev_p or {}).get("segments") or {}
    pytest_seg["segments"] = segments

    # ---- e2e 分段（套件粒度合并）----
    prev_suites = {s.get("name"): s for s in (prev.get("e2e") or {}).get("suites") or []}
    if updating_e2e:
        fresh_names = {s.get("name") for s in fresh_e["suites"]}
        merged = [dict(s, collected_at=now) for s in fresh_e["suites"]]
        merged += [s for name, s in prev_suites.items() if name not in fresh_names]
        order = {n: i for i, n in enumerate(
            ["e2e-m1-browser.mjs", "e2e-m2-browser.mjs", "e2e-m3-browser.mjs",
             "e2e-m4-browser.mjs", "e2e-m5-smoke.mjs"])}
        merged.sort(key=lambda s: order.get(s.get("name", ""), 99))
        passed = sum(1 for s in merged if s.get("outcome") == "passed")
        e2e_seg = {
            "suites": merged,
            "total": len(merged),
            "passed": passed,
            "pass_rate": round(passed / len(merged) * 100, 1) if merged else 0.0,
            "duration_ms": sum(int(s.get("duration_ms") or 0) for s in merged),
        }
    else:
        e2e_seg = prev.get("e2e") or fresh_e

    summary = {
        "pytest": pytest_seg,
        "e2e": e2e_seg,
        "generated_at": now,
        "full_run": bool(full_run),
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "quality-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 趋势行：全量与部分运行都追加（方案 B）。字段取合并后 summary——分段合并保证
    # 总量不失真，曲线连续；部分运行点带 partial 标记 + scopes 范围描述，
    # 前端画空心点/虚线段并提示"部分运行"，与全量点区分。
    scopes: list[str] = []
    if update_sections is None or {"pytest", "e2e"} <= update_sections:
        full = True
    else:
        full = False
        if "pytest" in update_sections:
            kinds_sorted = sorted(kinds_now) if (pytest_kinds is None or len(kinds_now) == 2) \
                else sorted(pytest_kinds & kinds_all)
            scopes.append("pytest:" + ",".join(kinds_sorted))
        if "e2e" in update_sections:
            scopes.append("e2e")
    point = {
        "ts": summary["generated_at"],
        "pytest_total": summary["pytest"]["total"],
        "pass_rate": summary["pytest"]["pass_rate"],
        "coverage_pct": summary["pytest"]["coverage_pct"],
        "e2e_total": summary["e2e"]["total"],
        "e2e_passed": summary["e2e"]["passed"],
    }
    if not full:
        point["partial"] = True
        point["scopes"] = scopes
    with (data_dir / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(point, ensure_ascii=False) + "\n")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="聚合平台自身测试结果为质量看板数据")
    parser.add_argument("--reports-dir", type=Path, default=None,
                        help="pytest/coverage/e2e 原始报告目录（默认 quality_data）")
    parser.add_argument("--data-dir", type=Path, default=QUALITY_DATA_DIR,
                        help="聚合产物目录（默认 quality_data）")
    parser.add_argument("--update-sections", type=str, default=None,
                        help="部分运行：逗号分隔（pytest/e2e 子集），仅重算指定分段并保留其余；缺省=全量")
    parser.add_argument("--pytest-kinds", type=str, default=None,
                        help="pytest 更新涉及的类型段（api/unit 子集，逗号分隔）；缺省=双段全更新")
    parser.add_argument("--full-run", action="store_true",
                        help="全量运行（pytest 双类型 + e2e 全部套件），追加 history 趋势点")
    args = parser.parse_args()
    reports_dir = args.reports_dir or args.data_dir

    update_sections = None
    if args.update_sections:
        valid = {"pytest", "e2e"}
        update_sections = {s.strip() for s in args.update_sections.split(",") if s.strip() in valid}
        if not update_sections:
            update_sections = None
    pytest_kinds = None
    if args.pytest_kinds:
        pytest_kinds = {s.strip() for s in args.pytest_kinds.split(",") if s.strip() in {"api", "unit"}} or None
    full_run = args.full_run or update_sections is None

    summary = aggregate(reports_dir, args.data_dir, update_sections=update_sections,
                        pytest_kinds=pytest_kinds, full_run=full_run)
    p, e = summary["pytest"], summary["e2e"]
    mode = "全量" if summary.get("full_run", True) else f"部分（{sorted(update_sections or set())}）"
    print(f"聚合完成（{mode}）→ {args.data_dir / 'quality-summary.json'}")
    print(f"  pytest: {p['passed']}/{p['total']} 通过（{p['pass_rate']}%），覆盖率 {p['coverage_pct']}%，失败 {p['failed']}")
    print(f"  e2e:    {e['passed']}/{e['total']} 套件通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
