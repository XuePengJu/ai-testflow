"""ExporterAgent：导出 xlsx / json / xmind 多格式文件。

入参：cases（用例列表）、output_path（不含扩展名）、formats（格式列表）
出参：(files, output_summary)  files: {fmt: 绝对路径}
"""
from app.services.pipeline_lib import lib_export
from src.models.testcase import TestCase


def run_exporter(cases: list[TestCase], output_path: str, formats):
    files = lib_export(cases, output_path, formats)
    summary = "导出：" + (", ".join(files.keys()) if files else "无")
    return files, summary
