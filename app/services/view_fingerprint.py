"""M1 视图指纹判重（探索 Agent 防绕圈，feature/plan-first-explore 分支）。

问题：SPA 站点 tab/菜单切换往往不改变 URL，ReAct 循环只看 URL 无法判断
「这个视图是否已经探索过」，Agent 容易在少数视图间反复漫游、绕圈烧预算。

方案：给每个「视图」生成指纹 = 规范化 URL + 激活态提示 + 可交互元素
(role, name) 有序集合。判定两视图是否同一**不用精确 hash**，而用元素集合的
Jaccard 相似度 ≥ SIM_THRESHOLD（0.9）判同——防懒加载/时间戳抖动导致误判。

对外接口：
- normalize_url(url)      ：去 fragment、去 utm_*/时间戳/随机 id 类噪声参数
- active_hint(snapshot)   ：识别快照中 aria-selected/active 的 tab 或菜单项名称
- fingerprint(url, snapshot) → ViewFingerprint（snapshot 支持快照文本或 ref 注册表）
- similarity(a, b)        ：两视图 (role, name) 集合的 Jaccard 相似度

元素签名过滤易变内容：纯数字 name、时间戳形态 name 归一化为占位符，
长数字串（订单号等）压缩为 #，避免「同一控件文案每秒刷新数字」被当成新视图。
快照格式约定（对齐 explorer_agent.BrowserSession.snapshot 的产出）：
    URL：https://...
    标题：...
    [e1] button 登录
    [e2] tab 订单（选中）      ← active 元素由快照侧追加「（选中）」标记
元素上限 60 个由快照侧控制，本模块不裁剪。
"""
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunsplit

# 同一视图判定阈值：元素集合 Jaccard 相似度 ≥ 0.9 视为同一视图
SIM_THRESHOLD = 0.9

# 快照文本中元素行的形态：[eN] role name
_ELEM_LINE_RE = re.compile(r"^\[(e\d+)\]\s+(\S+)\s*(.*)$")

# 快照文本中「激活态」标记（BrowserSession.snapshot 对 active 元素追加「（选中）」）
_ACTIVE_MARKERS = ("（选中）", "(选中)", "（当前）", "(current)",
                   "aria-selected=true", "aria-current=page", "[active]")

# ---- URL 噪声参数 ----
# key 命中即丢：追踪类（utm_* 单独前缀匹配）、时间戳/随机 id 类
_NOISE_KEYS = {
    "gclid", "fbclid", "spm", "scm", "scid", "sid", "sessionid",
    "_t", "t", "ts", "timestamp", "nonce", "_",
    "cb", "callback", "rand", "random", "requestid", "traceid", "seq",
}
# value 命中即丢（key 不在黑名单时）：epoch 时间戳 / 长 hex 随机 id / UUID
_VALUE_NOISE_RES = (
    re.compile(r"^\d{10,13}$"),
    re.compile(r"^[0-9a-fA-F]{16,}$"),
    re.compile(r"^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"),
)

# ---- 元素 name 归一化 ----
# 时间戳/时刻形态的 name → #time
_TS_RES = (
    re.compile(r"^\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$"),
    re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"),
    re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$"),
)
# 纯数字（含千分位/分隔符）name → #num
_PURE_NUM_RE = re.compile(r"^[\d\s.,，_/-]+$")
# 嵌在文案里的长数字串（订单号/ID）→ #
_LONG_DIGIT_RE = re.compile(r"\d{4,}")


@dataclass(frozen=True)
class ViewFingerprint:
    """一个视图的指纹：规范化 URL + 激活态 + 可交互元素签名有序集合。

    elements 元素形如 "role|归一化name"（去重、排序），供 Jaccard 比较。
    """
    url: str
    active: str
    elements: tuple[str, ...]


def _is_noise_param(key: str, value: str) -> bool:
    """query 参数是否为噪声（utm_/追踪/时间戳/随机 id）。"""
    k = (key or "").strip().lower()
    if k.startswith("utm_") or k in _NOISE_KEYS:
        return True
    v = (value or "").strip()
    return bool(v) and any(p.fullmatch(v) for p in _VALUE_NOISE_RES)


def normalize_url(url: str) -> str:
    """规范化 URL：去 fragment、去噪声 query 参数、参数按名排序（判定稳定）。

    相对路径/纯文本 URL 仅去 fragment；解析失败原样返回（去 fragment）。
    """
    if not url:
        return ""
    text = url.strip()
    try:
        parts = urlparse(text)
    except ValueError:
        return text.split("#", 1)[0]
    if not parts.scheme and not parts.netloc:
        return text.split("#", 1)[0]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not _is_noise_param(k, v)]
    query.sort()
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "",
                       urlencode(query), ""))


def _clean_active_marker(name: str) -> str:
    """剥掉激活态标记，返回干净元素名。"""
    text = (name or "").strip()
    for marker in _ACTIVE_MARKERS:
        text = text.replace(marker, "").strip()
    return text


def _normalize_name(name: str) -> str:
    """元素 name 归一化：过滤易变内容，返回稳定签名。

    规则：空白压缩 → 剥激活标记 → 时间戳形态 → #time；纯数字 → #num；
    文案内长数字串（订单号/ID）→ #。无数字的文案原样保留。
    """
    text = re.sub(r"\s+", " ", (name or "")).strip()
    for marker in _ACTIVE_MARKERS:
        text = text.replace(marker, "").strip()
    if not text:
        return ""
    if any(p.fullmatch(text) for p in _TS_RES):
        return "#time"
    if any(ch.isdigit() for ch in text) and _PURE_NUM_RE.fullmatch(text):
        return "#num"
    return _LONG_DIGIT_RE.sub("#", text)


def _parse_elements(snapshot: "str | dict") -> list[tuple[str, str]]:
    """快照 → [(role, name)]。支持快照文本（[eN] role name 行）或 ref 注册表 dict。"""
    pairs: list[tuple[str, str]] = []
    if isinstance(snapshot, dict):
        for info in snapshot.values():
            if isinstance(info, dict):
                pairs.append((str(info.get("role") or ""),
                              str(info.get("name") or "")))
        return pairs
    for line in str(snapshot or "").splitlines():
        m = _ELEM_LINE_RE.match(line.strip())
        if m:
            pairs.append((m.group(2), m.group(3)))
    return pairs


def active_hint(snapshot: "str | dict") -> str:
    """从快照识别激活态 tab/菜单项名称（如 aria-selected / active class 的元素）。

    快照文本约定：active 元素由快照侧在 name 后追加「（选中）」标记；
    ref 注册表约定：元素 info 带 active=True。无激活元素返回空串。
    """
    if isinstance(snapshot, dict):
        for info in snapshot.values():
            if isinstance(info, dict) and info.get("active"):
                name = _clean_active_marker(str(info.get("name") or ""))
                if name:
                    return name
        return ""
    for line in str(snapshot or "").splitlines():
        m = _ELEM_LINE_RE.match(line.strip())
        if not m:
            continue
        name_part = m.group(3)
        if any(marker in name_part for marker in _ACTIVE_MARKERS):
            name = _clean_active_marker(name_part)
            if name:
                return name
    return ""


def fingerprint(url: str, snapshot: "str | dict") -> ViewFingerprint:
    """生成视图指纹 = 规范化 URL + 激活态提示 + 可交互元素 (role, name) 有序集合。"""
    pairs = _parse_elements(snapshot)
    sigs: set[str] = set()
    for role, name in pairs:
        norm = _normalize_name(name)
        role = (role or "").strip()
        if not role and not norm:
            continue
        sigs.add(f"{role}|{norm}")
    return ViewFingerprint(url=normalize_url(url),
                           active=active_hint(snapshot),
                           elements=tuple(sorted(sigs)))


def similarity(a: ViewFingerprint, b: ViewFingerprint) -> float:
    """两视图 (role, name) 元素集合的 Jaccard 相似度；≥ SIM_THRESHOLD 判同一视图。

    两视图元素集合均为空时退化为 URL 判等（相同 1.0，不同 0.0）。
    """
    ea, eb = set(a.elements), set(b.elements)
    if not ea and not eb:
        return 1.0 if a.url == b.url else 0.0
    union = ea | eb
    if not union:
        return 0.0
    return len(ea & eb) / len(union)
