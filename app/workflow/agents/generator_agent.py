"""GeneratorAgent：按策略调用模型生成测试用例（无 Key 自动走 mock 兜底）。

入参：units（ParserAgent 产出的测试单元列表）
出参：(cases, output_summary)
"""
from app.services.pipeline_lib import lib_generate
from src.models.testcase import RequirementUnit, TestCase


def run_generator(units: list[RequirementUnit]):
    cases: list[TestCase] = lib_generate(units)
    summary = f"AI 生成 {len(cases)} 条测试用例（覆盖正向 / 异常 / 边界）"
    return cases, summary
