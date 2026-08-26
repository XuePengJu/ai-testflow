"""ParserAgent：解析规格文件 → 测试单元（接口 / 业务需求）。

入参：input_path（规格文件绝对路径）、kind（api / business）
出参：(units, output_summary)
"""
from app.services.pipeline_lib import lib_parse
from src.models.testcase import RequirementUnit


def run_parser(input_path: str, kind: str):
    units: list[RequirementUnit] = lib_parse(kind, input_path)
    summary = f"解析得到 {len(units)} 个测试单元"
    return units, summary
