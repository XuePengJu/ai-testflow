"""Xmind 导出（纯标准库，无第三方依赖）。

生成 XMind 8（legacy）XML 格式：content.xml + meta.xml + styles.xml + META-INF/manifest.xml。

结构说明：
- 每个模块作为一个 topic
- 每个用例作为一个 topic，标题为 `[类型] 用例标题`
- 用例的 优先级 / 类型 / 前置条件 / 步骤 / 预期结果 / 测试数据 全部展开为子节点
- 这样 XMind 打开后可直接看到完整用例信息，无需双击查看备注
"""
import time
import uuid
import zipfile
from xml.sax.saxutils import escape

from src.models.testcase import TestCase


def _tid() -> str:
    """XMind 风格 topic id（26 位小写字母数字）。"""
    return uuid.uuid4().hex[:26]


def _ts() -> str:
    return str(int(time.time() * 1000))


def _topic(title: str, children: list[str] | None = None, root: bool = False) -> str:
    """生成单个 topic XML。

    Args:
        title: 节点标题
        children: 子 topic XML 字符串列表
        root: 是否为根节点
    """
    attrs = f'id="{_tid()}" timestamp="{_ts()}"'
    if root:
        attrs += ' structure-class="org.xmind.ui.logic.right"'
    parts = [f"<topic {attrs}>", f"<title>{escape(title)}</title>"]
    if children:
        inner = "".join(children)
        parts.append(f'<children><topics type="attached">{inner}</topics></children>')
    parts.append("</topic>")
    return "".join(parts)


def _case_topic(c: TestCase) -> str:
    """把一个用例展开成 topic + 子节点。"""
    children: list[str] = []

    # 基础信息
    children.append(_topic(f"优先级: {c.priority.value}"))
    children.append(_topic(f"类型: {c.case_type.value}"))

    # 前置条件
    if c.pre_condition:
        children.append(_topic(f"前置条件: {c.pre_condition}"))

    # 步骤（带编号展开为子节点）
    if c.steps:
        step_children = [
            _topic(f"{i}. {s}") for i, s in enumerate(c.steps, 1) if s
        ]
        children.append(_topic("步骤", children=step_children))

    # 预期结果
    if c.expected:
        children.append(_topic(f"预期结果: {c.expected}"))

    # 测试数据
    if c.test_data:
        children.append(_topic(f"测试数据: {c.test_data}"))

    return _topic(f"[{c.case_type.value}] {c.title}", children=children)


_CONTENT_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0" '
    'xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:svg="http://www.w3.org/2000/svg" '
    'xmlns:xhtml="http://www.w3.org/1999/xhtml" xmlns:xlink="http://www.w3.org/1999/xlink" '
    'modified-by="ai-testflow" timestamp="{ts}" version="2.0">'
)

_META = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<meta xmlns="urn:xmind:xmap:xmlns:meta:2.0" version="2.0">'
    "<Author><Name>ai-testflow</Name><Email/><Org/></Author>"
    "<Create><Time>{time_str}</Time></Create>"
    "<Creator><Name>ai-testflow</Name><Version>1.0</Version></Creator>"
    "</meta>"
)

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<xmap-styles xmlns="urn:xmind:xmap:xmlns:style:2.0" '
    'xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:svg="http://www.w3.org/2000/svg" '
    'version="2.0"><styles/></xmap-styles>'
)

_MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    '<manifest xmlns="urn:xmind:xmap:xmlns:manifest:1.0" password-hint="">'
    '<file-entry full-path="content.xml" media-type="text/xml"/>'
    '<file-entry full-path="META-INF/" media-type=""/>'
    '<file-entry full-path="META-INF/manifest.xml" media-type="text/xml"/>'
    '<file-entry full-path="meta.xml" media-type="text/xml"/>'
    '<file-entry full-path="styles.xml" media-type="text/xml"/>'
    "</manifest>"
)


def export_xmind(cases: list[TestCase], out_path: str) -> None:
    groups: dict[str, list[TestCase]] = {}
    for c in cases:
        groups.setdefault(c.module or "未分类", []).append(c)

    modules_xml: list[str] = []
    for mod, cs in groups.items():
        case_topics = [_case_topic(c) for c in cs]
        modules_xml.append(_topic(mod, children=case_topics))

    root_xml = _topic("DBERP 测试用例", children=modules_xml, root=True)

    ts = _ts()
    content_xml = (
        _CONTENT_HEAD.format(ts=ts)
        + f'<sheet id="{_tid()}" modified-by="ai-testflow" timestamp="{ts}">'
        + root_xml
        + "<title>DBERP 测试用例</title>"
        + "</sheet></xmap-content>"
    )

    time_str = time.strftime("%Y-%m-%d %H:%M:%S")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content.xml", content_xml)
        z.writestr("meta.xml", _META.format(time_str=time_str))
        z.writestr("styles.xml", _STYLES)
        z.writestr("META-INF/manifest.xml", _MANIFEST)
