"""V2 质量运行范围与分段合并单测。

覆盖：
- POST /quality/run 的 scope 归一化（_normalize_plan）：默认全量 / e2e 子集 / 跳过 / 非法值
- 聚合分段合并（aggregate）：部分运行只更新对应段、总量不失真、
  覆盖率保留旧值、history 每次运行都追加（部分运行点带 partial + scopes 标记）
"""
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "scripts" / "quality"))

from aggregate_quality import aggregate  # noqa: E402
from app.api.quality import QualityRunReq, _normalize_plan  # noqa: E402


# ---------------- scope 归一化 ----------------

def test_scope_default_is_full_run():
    """请求体缺省 = 单测 + 接口 + e2e 全部套件。"""
    plan = _normalize_plan(None)
    assert plan["unit"] is True
    assert plan["api"] is True
    assert plan["e2e"] == ["m1", "m2", "m3", "m4", "m5"]


def test_scope_e2e_subset_keeps_milestone_order():
    """e2e 子集按里程碑顺序返回（乱序传入也归一）。"""
    plan = _normalize_plan(QualityRunReq(e2e=["m3", "m1"]))
    assert plan["unit"] is True and plan["api"] is True
    assert plan["e2e"] == ["m1", "m3"]


def test_scope_skip_e2e_with_false_or_empty():
    """e2e=False / [] 都表示跳过 e2e 段。"""
    assert _normalize_plan(QualityRunReq(e2e=False))["e2e"] is None
    assert _normalize_plan(QualityRunReq(e2e=[]))["e2e"] is None


def test_scope_unknown_suite_rejected():
    with pytest.raises(HTTPException) as ei:
        _normalize_plan(QualityRunReq(e2e=["m9"]))
    assert ei.value.status_code == 400


def test_scope_empty_selection_rejected():
    """三段都不选 → 400（范围不能为空）。"""
    with pytest.raises(HTTPException) as ei:
        _normalize_plan(QualityRunReq(unit=False, api=False, e2e=False))
    assert ei.value.status_code == 400


# ---------------- 聚合分段合并 ----------------

def _fake_kinds_factory(prefix: str):
    """伪造函数级类型判定：nodeid 前缀匹配 → api，否则 unit。"""
    def fake_kinds(_tests_dir):
        return {(f"{prefix}_a{i}.py", f"test_a{i}"): "api" for i in range(200)} | \
               {(f"{prefix}_u{i}.py", f"test_u{i}"): "unit" for i in range(300)}
    return fake_kinds


@pytest.fixture()
def prev_full_summary(tmp_path: Path) -> Path:
    """预置一份"全量"summary：unit 2 条全过 + api 2 条全过，e2e 2 套件。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cases = [
        {"file": "tests/test_x_u0.py", "name": "test_u0", "param": "", "outcome": "passed",
         "duration_ms": 1, "kind": "unit"},
        {"file": "tests/test_x_u1.py", "name": "test_u1", "param": "", "outcome": "passed",
         "duration_ms": 1, "kind": "unit"},
        {"file": "tests/test_x_a0.py", "name": "test_a0", "param": "", "outcome": "passed",
         "duration_ms": 1, "kind": "api"},
        {"file": "tests/test_x_a1.py", "name": "test_a1", "param": "", "outcome": "passed",
         "duration_ms": 1, "kind": "api"},
    ]
    summary = {
        "pytest": {
            "total": 4, "passed": 4, "failed": 0, "skipped": 0, "pass_rate": 100.0,
            "duration_ms": 9000, "coverage_pct": 71.2, "api_cases": 2, "unit_cases": 2,
            "by_file": {}, "cases": cases, "failures": [],
            "segments": {"unit": "2026-01-01T00:00:00+00:00", "api": "2026-01-01T00:00:00+00:00"},
        },
        "e2e": {
            "total": 2, "passed": 2, "pass_rate": 100.0, "duration_ms": 4000,
            "suites": [
                {"name": "e2e-m1-browser.mjs", "outcome": "passed", "duration_ms": 2000,
                 "error": None, "steps": [], "collected_at": "2026-01-01T00:00:00+00:00"},
                {"name": "e2e-m2-browser.mjs", "outcome": "passed", "duration_ms": 2000,
                 "error": None, "steps": [], "collected_at": "2026-01-01T00:00:00+00:00"},
            ],
        },
        "generated_at": "2026-01-01T00:00:00+00:00",
        "full_run": True,
    }
    (data_dir / "quality-summary.json").write_text(json.dumps(summary, ensure_ascii=False))
    return data_dir


def _write_reports(tmp_path: Path, tests: list[dict], suites: list[dict] | None) -> Path:
    rep = tmp_path / "reports"
    rep.mkdir(exist_ok=True)
    (rep / "pytest-report.json").write_text(json.dumps({"tests": tests, "total": len(tests), "duration": 1.0}))
    (rep / "coverage.json").write_text(json.dumps({"totals": {"percent_covered": 40.0}}))
    if suites is not None:
        (rep / "e2e-report.json").write_text(json.dumps({"suites": suites, "total": len(suites), "passed": 0}))
    return rep


def test_partial_api_merge_keeps_unit_and_totals(tmp_path, prev_full_summary, monkeypatch):
    """仅重跑 api：unit 旧数据保留、总量 = unit 旧 + api 新、覆盖率保留旧值。"""
    import aggregate_quality as agg
    monkeypatch.setattr(agg, "_build_case_kinds", _fake_kinds_factory("test_x"))
    tests = [
        {"nodeid": "tests/test_x_a0.py::test_a0", "outcome": "failed", "duration": 0.01},
        {"nodeid": "tests/test_x_a1.py::test_a1", "outcome": "passed", "duration": 0.01},
    ]
    rep = _write_reports(tmp_path, tests, suites=None)
    s = aggregate(rep, prev_full_summary, update_sections={"pytest"}, pytest_kinds={"api"}, full_run=False)
    p = s["pytest"]
    assert p["total"] == 4                    # unit 2 旧 + api 2 新，总量不失真
    assert p["unit_cases"] == 2
    assert p["api_cases"] == 2
    assert p["failed"] == 1                   # 新 api 的失败如实计入
    assert p["coverage_pct"] == 71.2          # 部分运行覆盖率不可比 → 保留旧值
    assert [c["outcome"] for c in p["cases"] if c["kind"] == "unit"] == ["passed", "passed"]
    assert p["segments"]["api"] != "2026-01-01T00:00:00+00:00"
    assert p["segments"]["unit"] == "2026-01-01T00:00:00+00:00"
    # 方案 B：部分运行也写趋势点，但带 partial 标记与范围描述
    lines = (prev_full_summary / "history.jsonl").read_text().splitlines()
    assert len(lines) == 1
    pt = json.loads(lines[0])
    assert pt["partial"] is True
    assert pt["scopes"] == ["pytest:api"]
    assert pt["pytest_total"] == 4            # 取合并后不失真的总量


def test_partial_e2e_merge_keeps_other_suites(tmp_path, prev_full_summary, monkeypatch):
    """仅重跑 m1：m2 旧记录保留、汇总按合并后套件列表重算。"""
    import aggregate_quality as agg
    monkeypatch.setattr(agg, "_build_case_kinds", _fake_kinds_factory("test_x"))
    tests = []
    rep = _write_reports(tmp_path, tests, suites=[
        {"name": "e2e-m1-browser.mjs", "outcome": "failed", "duration_ms": 1234, "error": "boom", "steps": []},
    ])
    s = aggregate(rep, prev_full_summary, update_sections={"e2e"}, full_run=False)
    e = s["e2e"]
    assert e["total"] == 2
    by_name = {x["name"]: x for x in e["suites"]}
    assert by_name["e2e-m1-browser.mjs"]["outcome"] == "failed"
    assert by_name["e2e-m2-browser.mjs"]["outcome"] == "passed"   # 旧记录保留
    assert by_name["e2e-m2-browser.mjs"]["collected_at"] == "2026-01-01T00:00:00+00:00"
    assert by_name["e2e-m1-browser.mjs"]["collected_at"] != "2026-01-01T00:00:00+00:00"


def test_full_run_appends_history(tmp_path, prev_full_summary, monkeypatch):
    """全量运行：history 追加一行趋势点（无 partial 标记）。"""
    import aggregate_quality as agg
    monkeypatch.setattr(agg, "_build_case_kinds", _fake_kinds_factory("test_x"))
    tests = [{"nodeid": "tests/test_x_a0.py::test_a0", "outcome": "passed", "duration": 0.01}]
    suites = [
        {"name": "e2e-m1-browser.mjs", "outcome": "passed", "duration_ms": 1, "error": None, "steps": []},
        {"name": "e2e-m2-browser.mjs", "outcome": "passed", "duration_ms": 1, "error": None, "steps": []},
    ]
    rep = _write_reports(tmp_path, tests, suites=suites)
    aggregate(rep, prev_full_summary, update_sections={"pytest", "e2e"}, full_run=True)
    lines = (prev_full_summary / "history.jsonl").read_text().splitlines()
    assert len(lines) == 1
    pt = json.loads(lines[0])
    assert pt["pytest_total"] == 1
    assert "partial" not in pt                # 全量点不带部分运行标记


def test_partial_e2e_history_scopes(tmp_path, prev_full_summary, monkeypatch):
    """仅跑 e2e：趋势点 partial=True 且 scopes=["e2e"]。"""
    import aggregate_quality as agg
    monkeypatch.setattr(agg, "_build_case_kinds", _fake_kinds_factory("test_x"))
    rep = _write_reports(tmp_path, [], suites=[
        {"name": "e2e-m1-browser.mjs", "outcome": "passed", "duration_ms": 1, "error": None, "steps": []},
    ])
    aggregate(rep, prev_full_summary, update_sections={"e2e"}, full_run=False)
    pt = json.loads((prev_full_summary / "history.jsonl").read_text().splitlines()[-1])
    assert pt["partial"] is True
    assert pt["scopes"] == ["e2e"]
