"""ParserAgent：解析规格文件 → 测试单元（接口 / 业务需求）。

入参：input_path（规格文件绝对路径）、kind（api / business）、client（可选，真实模型客户端）
出参：(units, output_summary)

business 模式：优先用真实模型理解整段需求、拆成测试点（不依赖 Markdown 标题，
一段话 / 一堆文字都能拆）；AI 不可用或解析失败时回退正则切标题，正则也没切出
则整段当一个测试点（保底不出现 0 条用例）。
"""
import json
import re
from pathlib import Path

from app.services.pipeline_lib import lib_parse
from src.models.testcase import RequirementUnit


_AI_PARSE_PROMPT = """你是资深软件测试工程师。请把下面这段业务需求拆解成若干「可独立测试的测试点」。

要求：
1. 通读整段需求，识别其中所有可测试的功能点 / 场景 / 业务规则，归纳合并成核心测试点
2. 不要过度拆分，控制在 3~8 个测试点；相关的小功能点合并到同一个测试点里
3. name：一句话概括该测试点（10~30 字，简洁明确）
4. description：补充该测试点的关键规则、字段、边界条件或前置条件（可空）
5. 只输出 JSON 数组，不要输出任何其他文字、解释或 markdown 代码块
6. 输出格式严格为：[{"name":"...","description":"..."}]

需求文本：
{text}"""


def _ai_parse_business(text: str, client) -> list[RequirementUnit]:
    """用真实模型把整段需求拆成测试点。失败返回 []（由调用方回退正则）。"""
    if client is None:
        return []
    prompt = _AI_PARSE_PROMPT.replace("{text}", text.strip()[:6000])
    try:
        raw = client.generate(prompt)
    except Exception:
        return []
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    units: list[RequirementUnit] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        desc = str(item.get("description", "")).strip()
        units.append(RequirementUnit(
            name=name[:80],
            kind="module",
            description=desc[:500],
            params=[name[:80]],
        ))
    return units


def run_parser(input_path: str, kind: str, client=None):
    """返回 (units, summary, details_json)。
    details_json 包含解析出的测试点列表，供前端展开查看详情。"""
    units: list[RequirementUnit] = []
    mode = "解析"

    if kind == "business":
        try:
            text = Path(input_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if text.strip():
            units = _ai_parse_business(text, client)
            if units:
                mode = "AI 理解拆解"

    if not units:
        units = lib_parse(kind, input_path)
        mode = "正则解析" if kind == "business" else "解析"

    summary = f"{mode}得到 {len(units)} 个测试点"
    # 序列化测试点列表，供前端展开查看详情
    details = {"units": [{"name": u.name, "description": u.description} for u in units]}
    import json as _json
    return units, summary, _json.dumps(details, ensure_ascii=False)
