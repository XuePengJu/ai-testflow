"""内置用例生成核心库（generator_core/）。

已整合原 ai-testcase-generator 的解析/生成/导出逻辑，通过 sys.path 注入
generator_core 目录（其下有 config/ 与 src/ 两个包），使项目自包含、clone 即跑。
mock 开关跟随平台配置。平台侧只负责任务编排与持久化。
"""
import sys
import json

from app.core.config import GENERATOR_CORE_DIR, DASHSCOPE_API_KEY

# 注入 generator_core 目录到 sys.path（其下有 config/ 与 src/ 两个包）
if str(GENERATOR_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(GENERATOR_CORE_DIR))

import config.settings as legacy_settings
# 让生成库的 mock 开关跟随平台配置（is_mock() 动态读取该模块变量）
legacy_settings.DASHSCOPE_API_KEY = DASHSCOPE_API_KEY

from src.parser.swagger_parser import parse_swagger                # noqa: E402
from src.parser.markdown_parser import parse_markdown              # noqa: E402
from src.generator.case_generator import CaseGenerator             # noqa: E402
import src.exporter.excel_exporter as excel_exporter                # noqa: E402
import src.exporter.json_exporter as json_exporter                  # noqa: E402
import src.exporter.xmind_exporter as xmind_exporter                # noqa: E402
from src.models.testcase import TestCase, RequirementUnit          # noqa: E402


def lib_parse(kind: str, input_path: str) -> list[RequirementUnit]:
    """解析输入文件为测试单元。kind=api 走 swagger，business 走 markdown。"""
    if kind == "api":
        return parse_swagger(input_path)
    return parse_markdown(input_path)


def lib_generate(units: list[RequirementUnit], client=None) -> list[TestCase]:
    """按策略生成测试用例。client=平台注入的真实模型（V2.4）；None → mock/dashscope 兜底。"""
    return CaseGenerator(client=client).generate(units)


def lib_export(cases: list[TestCase], output_path: str, formats: list[str]) -> dict:
    """导出指定格式，返回 {fmt: 绝对路径}。"""
    result: dict = {}
    for fmt in formats:
        fmt = fmt.strip().lower()
        if fmt == "xlsx":
            ext = excel_exporter.export_excel(cases, output_path + ".xlsx")
            result["xlsx"] = output_path + "." + ext
        elif fmt == "json":
            json_exporter.export_json(cases, output_path + ".json")
            result["json"] = output_path + ".json"
        elif fmt == "xmind":
            xmind_exporter.export_xmind(cases, output_path + ".xmind")
            result["xmind"] = output_path + ".xmind"
    return result


def cases_to_json(cases: list[TestCase]) -> str:
    """用例列表序列化为 JSON 字符串，便于存库。"""
    return json.dumps(
        [c.model_dump(mode="json") for c in cases],
        ensure_ascii=False, indent=2,
    )
