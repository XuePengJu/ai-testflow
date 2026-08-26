"""解析业务需求 Markdown → 测试单元列表（按二级及以上标题切分章节）。"""
import re
from pathlib import Path
from src.models.testcase import RequirementUnit


def parse_markdown(path: str | Path) -> list[RequirementUnit]:
    text = Path(path).read_text(encoding="utf-8")
    units = []
    # 按 Markdown 二级及以上标题切分（覆盖 ## 与 ### 章节）
    parts = re.split(r"\n#{2,}\s+", text)
    for part in parts[1:]:
        lines = part.strip().split("\n")
        title = lines[0].strip().split("（")[0].strip()
        body = "\n".join(lines[1:]).strip()
        if not title:
            continue
        units.append(RequirementUnit(
            name=title,
            kind="module",
            description=body[:500],
            params=[title],
        ))
    return units
