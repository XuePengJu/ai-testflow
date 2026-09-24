"""M0 质量聚合：解析 pytest-json-report + coverage + e2e 报告 → quality-summary.json + history.jsonl。

用法：
    .venv/bin/python scripts/quality/aggregate_quality.py [--reports-dir DIR] [--data-dir DIR]

- --reports-dir：pytest-report.json / coverage.json / e2e-report.json 所在目录（默认质量数据目录）
- --data-dir：聚合产物目录（默认 <项目根>/quality_data，可用 AITF_ROOT_DIR 切换）
每次聚合向 history.jsonl 追加一行（带时间戳），供看板趋势图使用。
"""
import argparse
import json
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


def _agg_pytest(reports_dir: Path) -> dict:
    """解析 pytest-json-report + coverage.json → pytest 维度汇总。"""
    report = _load_json(reports_dir / "pytest-report.json") or {}
    coverage = _load_json(reports_dir / "coverage.json") or {}

    tests = report.get("tests") or []
    by_file: dict[str, dict] = {}
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
        "by_file": by_file,
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


def aggregate(reports_dir: Path, data_dir: Path) -> dict:
    """聚合三份原始报告 → 写 quality-summary.json + 追加 history.jsonl，返回摘要。"""
    summary = {
        "pytest": _agg_pytest(reports_dir),
        "e2e": _agg_e2e(reports_dir),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "quality-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 趋势行：只留看板需要的字段（history.jsonl 逐行 JSON，按时间升序追加）
    point = {
        "ts": summary["generated_at"],
        "pytest_total": summary["pytest"]["total"],
        "pass_rate": summary["pytest"]["pass_rate"],
        "coverage_pct": summary["pytest"]["coverage_pct"],
        "e2e_total": summary["e2e"]["total"],
        "e2e_passed": summary["e2e"]["passed"],
    }
    with (data_dir / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(point, ensure_ascii=False) + "\n")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="聚合平台自身测试结果为质量看板数据")
    parser.add_argument("--reports-dir", type=Path, default=None,
                        help="pytest/coverage/e2e 原始报告目录（默认 quality_data）")
    parser.add_argument("--data-dir", type=Path, default=QUALITY_DATA_DIR,
                        help="聚合产物目录（默认 quality_data）")
    args = parser.parse_args()
    reports_dir = args.reports_dir or args.data_dir

    summary = aggregate(reports_dir, args.data_dir)
    p, e = summary["pytest"], summary["e2e"]
    print(f"聚合完成 → {args.data_dir / 'quality-summary.json'}")
    print(f"  pytest: {p['passed']}/{p['total']} 通过（{p['pass_rate']}%），覆盖率 {p['coverage_pct']}%，失败 {p['failed']}")
    print(f"  e2e:    {e['passed']}/{e['total']} 套件通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
