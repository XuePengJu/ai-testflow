"""ReviewerAgent：质量校验与门禁（增强版）。

入参：cases（GeneratorAgent 产出的用例列表）
出参：(report, output_summary)

增强：检查覆盖度，缺失类型时调用 AI 补充用例。
"""
import json
import re
from collections import Counter

from app.services import llm_service
from src.models.testcase import TestCase, CaseType, Priority, align_step_expectations


_FILL_PROMPT = """你是资深测试工程师。请为以下「测试点」补充测试用例，使其覆盖缺失的维度。

缺失维度：{missing}
已有用例数量：{total} 条
测试点描述：{description}

请补充 2~4 条用例，补齐缺失维度。输出 JSON 数组，每个元素结构：
[
  {{
    "title": "用例标题",
    "module": "模块",
    "case_type": "正向|异常|边界值|场景组合",
    "priority": "P0|P1|P2|P3",
    "pre_condition": "前置条件",
    "steps": ["步骤1", "步骤2"],
    "step_expectations": ["步骤1的预期结果", "步骤2的预期结果"],
    "expected": "整体预期结果（各步骤预期的总结）",
    "test_data": "测试数据"
  }}
]

要求：step_expectations 必须与 steps 一一对应、数量严格一致。
只输出 JSON，不要解释。"""

_MISSING_THRESHOLD = {
    "正向": 1,
    "异常": 1,
    "边界值": 1,
    "场景组合": 0,  # 可选
}


def _normalize_type(ct):
    """兼容不同大小写/中文化。"""
    if ct is None:
        return ""
    ct = str(ct).strip()
    mapping = {
        "正向": "正向", "positive": "正向", "正常": "正向",
        "异常": "异常", "negative": "异常", "出错": "异常",
        "边界": "边界值", "边界值": "边界值", "boundary": "边界值",
        "场景": "场景组合", "场景组合": "场景组合", "scenario": "场景组合",
    }
    return mapping.get(ct, ct)


def _build_cases_from_json(arr: list) -> list[TestCase]:
    """从 JSON 数组构建 TestCase（复用 _normalize 逻辑）。"""
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        ct = item.get("case_type") or item.get("caseType") or "正向"
        pr = item.get("priority") or item.get("优先级") or "P1"
        steps = item.get("steps", []) or []
        expected = item.get("expected", "")
        se = item.get("step_expectations") or item.get("stepExpectations") or []
        try:
            out.append(TestCase(
                title=item.get("title", "未命名用例"),
                module=item.get("module", ""),
                case_type=CaseType.POSITIVE if _normalize_type(ct) == "正向" else
                          CaseType.NEGATIVE if _normalize_type(ct) == "异常" else
                          CaseType.BOUNDARY if _normalize_type(ct) == "边界值" else
                          CaseType.SCENARIO,
                priority=Priority(pr),
                pre_condition=item.get("pre_condition", "") or item.get("preCondition", ""),
                steps=steps,
                step_expectations=align_step_expectations(steps, se, expected),
                expected=expected,
                test_data=item.get("test_data") or item.get("testData"),
            ))
        except Exception:
            continue
    return out


def run_reviewer(cases: list[TestCase], client=None) -> tuple[dict, str]:
    """质量校验 + AI 补充。client 为可选的真实模型客户端。"""
    total = len(cases)
    by_type = Counter(_normalize_type(c.case_type) for c in cases)
    by_priority = Counter(str(c.priority) for c in cases)
    abnormal = by_type.get("异常", 0) + by_type.get("边界值", 0)
    modules = Counter(c.module for c in cases if c.module)

    # 结构校验：步骤与预期缺失
    struct_issues = [c.title for c in cases if not c.steps or not c.expected]

    report = {
        "total": total,
        "by_type": dict(by_type),
        "by_priority": dict(by_priority),
        "abnormal_ratio": round(abnormal / total, 2) if total else 0,
        "modules": dict(modules),
        "struct_issues": struct_issues,
        "quality_pass": (len(struct_issues) == 0 and total > 0),
        "supplemented": 0,
    }

    # 覆盖度检查 + AI 补充
    supplemented = 0
    if client is not None and total > 0:
        missing = [k for k, v in _MISSING_THRESHOLD.items() if by_type.get(k, 0) < v]
        if missing:
            try:
                # 取一个典型测试点描述（这里用第一条用例的模块作为上下文）
                desc = cases[0].module or cases[0].title if cases else "未知"
                fill_prompt = _FILL_PROMPT.format(
                    missing="、".join(missing),
                    total=total,
                    description=desc,
                )
                raw = client.chat([{"role": "user", "content": fill_prompt}], temperature=0.5, max_tokens=2048)
                m = re.search(r"\[.*\]", raw or "", re.S)
                if m:
                    try:
                        arr = json.loads(m.group(0))
                        new_cases = _build_cases_from_json(arr)
                        if new_cases:
                            cases.extend(new_cases)
                            # 更新 by_type
                            for nc in new_cases:
                                by_type[_normalize_type(nc.case_type)] += 1
                            supplemented = len(new_cases)
                    except Exception:  # noqa: BLE001
                        pass
            except llm_service.LLMError:
                pass  # 补充失败不阻断，保留原有报告

    # 更新 report
    report["by_type"] = dict(by_type)
    report["supplemented"] = supplemented
    report["quality_pass"] = (len(struct_issues) == 0 and total > 0)

    pct = report["abnormal_ratio"] * 100
    if supplemented > 0:
        extra = f"；补充了 {supplemented} 条"
    else:
        extra = ""
    status_txt = "通过" if report["quality_pass"] else f"存在 {len(struct_issues)} 条结构问题"
    summary = f"质量报告：共 {len(cases)} 条，异常/边界占比 {pct:.0f}%，结构校验{status_txt}{extra}"
    return report, summary, json.dumps(report, ensure_ascii=False, default=str)
