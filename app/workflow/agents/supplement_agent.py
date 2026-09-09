"""SupplementAgent：增量生成测试用例（迭代补充）。

入参：existing_cases（已有用例列表）、instruction（补充要求）、client（可选 LLM 客户端）
出参：(new_cases, summary, details_json)

设计要点：
- 上下文只传压缩摘要（模块+标题+类型统计），不传 steps/expected 全文，防 token 超限
- 明确要求 LLM 不重复已有用例，只补充指令涉及范围
- mock 兜底：按补充指令关键词生成模板化用例
"""
import json
import re
from collections import Counter

from src.models.testcase import TestCase, CaseType, Priority


_SUPPLEMENT_PROMPT = """你是资深测试工程师。请根据补充要求，为已有测试用例集**增量补充**用例。

【已有用例摘要】
{existing_summary}

【补充要求】
{instruction}

【输出要求】
1. 只生成补充要求涉及的用例，**绝对不要重复已有用例的标题和场景**
2. 如果补充要求涉及新模块，module 字段填新模块名
3. 输出 JSON 数组，每个元素结构：
[
  {{
    "title": "用例标题",
    "module": "模块",
    "case_type": "正向|异常|边界值|场景组合",
    "priority": "P0|P1|P2|P3",
    "pre_condition": "前置条件",
    "steps": ["步骤1", "步骤2"],
    "expected": "预期结果",
    "test_data": "测试数据"
  }}
]
4. 只输出 JSON，不要解释、不要 markdown 代码块包裹"""


def build_existing_summary(cases: list[TestCase], max_per_module: int = 15) -> str:
    """构建已有用例的压缩摘要：模块分组 + 标题 + 类型，不传全文。"""
    if not cases:
        return "（无已有用例）"
    by_module: dict[str, list[TestCase]] = {}
    for c in cases:
        by_module.setdefault(c.module or "未分类", []).append(c)
    lines = []
    total = len(cases)
    type_dist = Counter(str(c.case_type.value) for c in cases)
    lines.append(f"共 {total} 条，类型分布：{dict(type_dist)}")
    for mod, cs in by_module.items():
        lines.append(f"\n模块「{mod}」（{len(cs)} 条）：")
        for c in cs[:max_per_module]:
            title = c.title[:40] + ("…" if len(c.title) > 40 else "")
            lines.append(f"  - {title}（{c.case_type.value}）")
        if len(cs) > max_per_module:
            lines.append(f"  …（另有 {len(cs) - max_per_module} 条未列出）")
    return "\n".join(lines)


def _normalize(raw: dict) -> dict:
    """兼容 LLM 返回的字段名大小写/中英差异（复用 CaseGenerator 逻辑）。"""
    ct = raw.get("case_type") or raw.get("caseType") or "正向"
    pr = raw.get("priority") or raw.get("优先级") or "P1"
    return {
        "title": raw.get("title", "未命名用例"),
        "module": raw.get("module", ""),
        "case_type": ct,
        "priority": pr,
        "pre_condition": raw.get("pre_condition", "") or raw.get("preCondition", ""),
        "steps": raw.get("steps", []) or [],
        "expected": raw.get("expected", ""),
        "test_data": raw.get("test_data") or raw.get("testData"),
    }


def _parse_llm(text: str) -> list[TestCase]:
    """从 LLM 回复中解析 JSON 用例数组。"""
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    out = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        try:
            n = _normalize(item)
            out.append(TestCase(
                title=n["title"],
                module=n["module"],
                case_type=CaseType(n["case_type"]) if n["case_type"] in ("正向", "异常", "边界值", "场景组合") else CaseType.POSITIVE,
                priority=Priority(n["priority"]) if n["priority"] in ("P0", "P1", "P2", "P3") else Priority.P1,
                pre_condition=n["pre_condition"],
                steps=n["steps"],
                expected=n["expected"],
                test_data=n["test_data"],
            ))
        except Exception:  # noqa: BLE001
            continue
    return out


def _mock_supplement(instruction: str, existing_cases: list[TestCase]) -> list[TestCase]:
    """mock 兜底：根据补充指令关键词生成模板化用例。"""
    existing_modules = {c.module for c in existing_cases if c.module}
    target_module = ""
    for mod in existing_modules:
        if mod and mod in instruction:
            target_module = mod
            break
    if not target_module:
        target_module = instruction[:10] or "补充模块"
    templates = [
        ("补充-异常输入校验", "异常", "P1", "输入非法数据", ["输入非法格式数据", "提交"], "系统提示错误并拒绝提交"),
        ("补充-边界值验证", "边界值", "P2", "输入边界值", ["输入最小边界值", "输入最大边界值", "提交"], "系统正确处理边界值"),
        ("补充-空值处理", "异常", "P1", "必填项为空", ["清空必填字段", "提交"], "系统提示必填项不能为空"),
    ]
    cases = []
    for title, ct, pr, pre, steps, exp in templates:
        cases.append(TestCase(
            title=title,
            module=target_module,
            case_type=CaseType(ct),
            priority=Priority(pr),
            pre_condition=pre,
            steps=steps,
            expected=exp,
        ))
    return cases


def run_supplement(
    existing_cases: list[TestCase],
    instruction: str,
    client=None,
    model_desc: str = "",
) -> tuple[list[TestCase], str, str]:
    """增量生成用例。

    Args:
        existing_cases: 已有用例列表（用于去重上下文）
        instruction: 用户补充要求
        client: 可选 LLM 客户端（None → mock 兜底）
        model_desc: 模型描述（用于 summary）

    Returns:
        (new_cases, summary, details_json)
    """
    if client is not None:
        summary_text = build_existing_summary(existing_cases)
        prompt = _SUPPLEMENT_PROMPT.format(
            existing_summary=summary_text,
            instruction=instruction or "补充更多测试用例",
        )
        try:
            raw = client.generate(prompt)
            new_cases = _parse_llm(raw)
        except Exception:  # noqa: BLE001
            new_cases = _mock_supplement(instruction, existing_cases)
            model_note = "LLM 调用失败，mock 兜底"
        else:
            model_note = model_desc or "真实模型"
    else:
        new_cases = _mock_supplement(instruction, existing_cases)
        model_note = "mock 兜底"

    # 过滤掉与已有用例标题完全重复的
    existing_titles = {(c.title, c.module, c.case_type.value) for c in existing_cases}
    deduped = [c for c in new_cases if (c.title, c.module, c.case_type.value) not in existing_titles]

    by_module = Counter(c.module or "未分类" for c in deduped)
    summary = f"增量生成 {len(deduped)} 条用例（模型：{model_note}）"
    details = {
        "generated": len(new_cases),
        "after_dedup": len(deduped),
        "by_module": dict(by_module),
        "instruction": instruction,
        "model": model_note,
    }
    return deduped, summary, json.dumps(details, ensure_ascii=False)
