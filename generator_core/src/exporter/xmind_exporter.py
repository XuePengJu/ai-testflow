"""Xmind 导出（纯标准库：.xmind 即 zip 包 + content.json，无需第三方库）。"""
import json
import zipfile
import uuid
from src.models.testcase import TestCase


def _node(title: str, children: list | None = None) -> dict:
    n = {"id": uuid.uuid4().hex, "title": title}
    if children:
        n["children"] = {"attached": children}
    return n


def export_xmind(cases: list[TestCase], out_path: str) -> None:
    groups: dict[str, list[TestCase]] = {}
    for c in cases:
        groups.setdefault(c.module or "未分类", []).append(c)

    attached = []
    for mod, cs in groups.items():
        children = [_node(f"[{c.case_type.value}] {c.title}") for c in cs]
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
