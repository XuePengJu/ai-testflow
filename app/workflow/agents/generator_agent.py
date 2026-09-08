"""GeneratorAgent：按策略调用模型生成测试用例（V2.4 支持注入真实模型）。

入参：units（ParserAgent 产出的测试单元列表）、client（可选，OpenAI 兼容客户端）
出参：(cases, output_summary, details_json)
"""
import json
from collections import Counter
from app.services.pipeline_lib import lib_generate
from src.models.testcase import RequirementUnit, TestCase


def run_generator(units: list[RequirementUnit], client=None, model_desc: str = ""):
    cases: list[TestCase] = lib_generate(units, client=client)
    model_note = model_desc or "mock 兜底"
    # 统计每个测试点生成多少条用例
    case_count_by_unit = Counter()
    for c in cases:
        case_count_by_unit[c.module] += 1
    unit_stats = [{"name": u.name, "cases_generated": case_count_by_unit.get(u.name, 0)} for u in units]
    summary = f"AI 生成 {len(cases)} 条测试用例（覆盖正向 / 异常 / 边界 · 模型：{model_note}）"
    details = {"total_cases": len(cases), "by_unit": unit_stats, "model": model_note}
    return cases, summary, json.dumps(details, ensure_ascii=False)
