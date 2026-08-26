"""ReviewerAgent：质量校验与门禁。

入参：cases（GeneratorAgent 产出的用例列表）
出参：(report, output_summary)
"""
from collections import Counter

from src.models.testcase import TestCase


def _type_value(c):
    ct = c.case_type
    return ct.value if hasattr(ct, "value") else str(ct)


def run_reviewer(cases: list[TestCase]):
    total = len(cases)
    by_type = Counter(_type_value(c) for c in cases)
    by_priority = Counter(str(c.priority) for c in cases)
    abnormal = by_type.get("异常", 0) + by_type.get("边界", 0)
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
    }
    pct = report["abnormal_ratio"] * 100
    status_txt = "通过" if report["quality_pass"] else f"存在 {len(struct_issues)} 条结构问题"
    summary = f"质量报告：共 {total} 条，异常/边界占比 {pct:.0f}%，结构校验{status_txt}"
    return report, summary
