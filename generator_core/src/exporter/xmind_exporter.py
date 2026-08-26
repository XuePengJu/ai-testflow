"""Xmind 导出（纯标准库：.xmind 即 zip 包 + content.json，无需第三方库）。"""
import json
import zipfile
import uuid
from src.models.testcase import TestCase


def _node(title: str, children: list | None = None, notes: str | None = None) -> dict:
    n = {"id": uuid.uuid4().hex, "title": title}
    if notes:
        n["notes"] = {"plain": {"content": notes}}
    if children:
        n["children"] = {"attached": children}
    return n


def _case_notes(c: TestCase) -> str:
    """把一个用例的关键信息拼成 Xmind 备注文本（双击节点可见）。"""
    lines = [f"优先级: {c.priority.value}", f"类型: {c.case_type.value}"]
    if c.pre_condition:
        lines.append(f"前置条件: {c.pre_condition}")
    if c.steps:
        lines.append("步骤:")
        lines += [f"  {i}. {s}" for i, s in enumerate(c.steps, 1)]
    if c.expected:
        lines.append(f"预期结果: {c.expected}")
    if c.test_data:
        lines.append(f"测试数据: {c.test_data}")
    return "\n".join(lines)


def export_xmind(cases: list[TestCase], out_path: str) -> None:
    groups: dict[str, list[TestCase]] = {}
    for c in cases:
        groups.setdefault(c.module or "未分类", []).append(c)

    attached = []
    for mod, cs in groups.items():
        children = [
            _node(f"[{c.case_type.value}] {c.title}", notes=_case_notes(c))
            for c in cs
        ]
        attached.append(_node(mod, children))

    sheet = {
        "id": uuid.uuid4().hex,
        "class": "sheet",
        "title": "DBERP 测试用例",
        "children": {"attached": attached},
    }
    content = [sheet]
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.json", json.dumps(content, ensure_ascii=False))
        z.writestr("metadata.json", json.dumps({"creator": "ai-testcase-generator"}, ensure_ascii=False))
