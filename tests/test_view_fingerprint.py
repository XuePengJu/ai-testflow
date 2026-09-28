"""M1 视图指纹判重单测：URL 规范化、指纹稳定性（同视图两次生成相似度≥0.9）、
tab 切换后指纹变化（相似度<0.9）、时间戳噪声归一化、探索 Agent 集成（prompt 注入 +
tab 触发器记账）。测试风格对齐 tests/test_explorer_agent.py（fake 浏览器 + fake LLM）。"""
from app.services import explorer_agent
from app.services.view_fingerprint import (
    active_hint,
    fingerprint,
    normalize_url,
    similarity,
)


def _snap(url: str, title: str, lines: list[str]) -> str:
    """拼一段与 BrowserSession.snapshot 同格式的快照文本。"""
    return "\n".join([f"URL：{url}", f"标题：{title}"] + lines)


# ---- 1. URL 规范化：去 fragment、去噪声参数、参数排序 ----

def test_normalize_url_strips_fragment_and_noise_params():
    noisy = ("https://a.test/list?a=1&utm_source=wx&utm_campaign=x"
             "&ts=1700000000000&cb=jp1&b=2#section-3")
    assert normalize_url(noisy) == "https://a.test/list?a=1&b=2"


def test_normalize_url_keeps_normal_params_and_sorts_them():
    assert normalize_url("https://a.test/list?page=2&kw=abc") == \
        "https://a.test/list?kw=abc&page=2"
    assert normalize_url("https://a.test/list?kw=abc&page=2") == \
        "https://a.test/list?kw=abc&page=2"


def test_normalize_url_drops_random_id_values():
    # 长 hex 随机 id / uuid 形态的参数值视为噪声
    assert normalize_url("https://a.test/x?rid=0123456789abcdef0123456789abcdef&n=1") == \
        "https://a.test/x?n=1"
    assert normalize_url(
        "https://a.test/x?trace=550e8400-e29b-41d4-a716-446655440000&n=1") == \
        "https://a.test/x?n=1"


# ---- 2. 指纹稳定性：同视图两次快照相似度 ≥ 0.9 ----

_VIEW_LINES_A = [
    "[e1] tab 订单（选中）",
    "[e2] tab 设置",
    "[e3] button 导出报表",
    "[e4] link 帮助文档",
    "[e5] button 全部标记已读",
    "[e6] textbox 搜索",
    "[e7] button 2024-05-01 12:00:00",   # 易变：时间戳形态按钮
    "[e8] link 返回首页",
    "[e9] button 刷新列表",
    "[e10] checkbox 只看未处理",
]


def test_fingerprint_stable_for_same_view():
    """同视图第二次快照：元素乱序、空白差异、时间戳刷新、URL 噪声参数变化、
    懒加载多 1 个元素 → Jaccard 仍 ≥ 0.9（10 公共 / 11 并集 ≈ 0.909）。"""
    snap_a = _snap("https://spa.test/admin?_=1700000000#tab", "管理台", _VIEW_LINES_A)
    snap_b = _snap("https://spa.test/admin?_=1700000123#other", "管理台", [
        "[e1] link 帮助文档",
        "[e2] button  2024-06-02 08:30:00",   # 时间戳已刷新 + 多余空白
        "[e3] tab 订单（选中）",
        "[e4] button 导出报表",
        "[e5] tab 设置",
        "[e6] checkbox 只看未处理",
        "[e7] textbox 搜索",
        "[e8] link 返回首页",
        "[e9] button 刷新列表",
        "[e10] button 全部标记已读",
        "[e11] button 重新加载",              # 懒加载新增元素
    ])
    fp_a = fingerprint("https://spa.test/admin?_=1700000000#tab", snap_a)
    fp_b = fingerprint("https://spa.test/admin?_=1700000123#other", snap_b)
    assert fp_a.url == fp_b.url == "https://spa.test/admin"
    assert fp_a.active == fp_b.active == "订单"
    assert similarity(fp_a, fp_b) >= 0.9


# ---- 3. tab 切换后指纹变化：相似度 < 0.9 ----

def test_tab_switch_yields_different_fingerprint():
    tab_orders = _snap("https://spa.test/admin", "管理台", [
        "[e1] tab 订单（选中）", "[e2] tab 设置",
        "[e3] button 导出订单", "[e4] link 订单详情", "[e5] button 批量发货",
    ])
    tab_settings = _snap("https://spa.test/admin", "管理台", [
        "[e1] tab 订单", "[e2] tab 设置（选中）",
        "[e3] button 保存设置", "[e4] link 修改密码", "[e5] button 恢复默认",
    ])
    fp_a = fingerprint("https://spa.test/admin", tab_orders)
    fp_b = fingerprint("https://spa.test/admin", tab_settings)
    assert fp_a.active == "订单" and fp_b.active == "设置"
    assert similarity(fp_a, fp_b) < 0.9


# ---- 4. 易变内容归一化：时间戳 / 纯数字 / 长数字串 ----

def test_timestamp_names_normalized():
    s1 = _snap("https://spa.test/x", "页", [
        "[e1] button 2024-05-01 12:00:00", "[e2] link 订单 100000001"])
    s2 = _snap("https://spa.test/x", "页", [
        "[e1] button 2025-12-31 23:59:59", "[e2] link 订单 200000002"])
    fp1 = fingerprint("https://spa.test/x", s1)
    fp2 = fingerprint("https://spa.test/x", s2)
    assert fp1.elements == fp2.elements
    assert similarity(fp1, fp2) == 1.0


def test_pure_digit_name_normalized():
    s1 = _snap("https://spa.test/x", "页", ["[e1] button 123456"])
    s2 = _snap("https://spa.test/x", "页", ["[e1] button 654321"])
    assert fingerprint("https://spa.test/x", s1).elements == ("button|#num",)
    assert similarity(fingerprint("https://spa.test/x", s1),
                      fingerprint("https://spa.test/x", s2)) == 1.0


# ---- 5. 激活态识别与注册表输入 ----

def test_active_hint_parses_selected_tab():
    snap = _snap("https://spa.test/admin", "管理台",
                 ["[e1] tab 订单", "[e2] tab 设置（选中）", "[e3] button 保存"])
    assert active_hint(snap) == "设置"
    assert active_hint(_snap("https://spa.test/admin", "管理台",
                             ["[e1] tab 订单"])) == ""


def test_fingerprint_accepts_registry_dict():
    """指纹函数同时接受 ref→元素注册表（BrowserSession.snapshot 的第二返回值）。"""
    registry = {
        "e1": {"role": "tab", "name": "订单", "css": "#a", "active": True},
        "e2": {"role": "tab", "name": "设置", "css": "#b"},
    }
    fp = fingerprint("https://spa.test/admin", registry)
    assert fp.active == "订单"
    assert "tab|订单" in fp.elements and "tab|设置" in fp.elements


def test_similarity_edges():
    fp = fingerprint("https://a.test/x",
                     _snap("https://a.test/x", "页", ["[e1] button 保存"]))
    other = fingerprint("https://b.test/y",
                        _snap("https://b.test/y", "页", ["[e1] link 首页"]))
    assert similarity(fp, fp) == 1.0
    assert similarity(fp, other) == 0.0


# ---- 6. 探索 Agent 集成：prompt 注入 + tab 触发器记账（fake 浏览器/LLM） ----

class FakePage:
    def __init__(self):
        self.url = "https://spa.test/admin"
        self.title = "管理台"

    def snapshot_lines(self) -> list[str]:
        return ["[e1] tab 订单", "[e2] tab 设置（选中）", "[e3] button 导出"]


class FakeBrowser:
    """与 BrowserSession 同方法协议的最小假浏览器（单页 SPA，tab 不改 URL）。"""

    def __init__(self, page: FakePage):
        self.page = page
        self.registry: dict[str, dict] = {}

    @property
    def url(self) -> str:
        return self.page.url

    @property
    def title(self) -> str:
        return self.page.title

    def goto(self, url: str) -> tuple[bool, str]:
        if url == self.page.url:
            return True, "ok"
        return False, f"打开失败：未知页面 {url}"

    def back(self) -> tuple[bool, str]:
        return True, "ok"

    def snapshot(self) -> tuple[str, dict[str, dict]]:
        self.registry = {
            "e1": {"role": "tab", "name": "订单", "css": "#a"},
            "e2": {"role": "tab", "name": "设置", "css": "#b", "active": True},
            "e3": {"role": "button", "name": "导出", "css": "#c"},
        }
        lines = [f"URL：{self.page.url}", f"标题：{self.page.title}"]
        lines += self.page.snapshot_lines()
        return "\n".join(lines), dict(self.registry)

    def click(self, ref: str) -> tuple[bool, str]:
        if ref not in self.registry:
            return False, f"ref {ref} 无效"
        return True, "ok"

    def fill(self, fields: list[dict]) -> tuple[bool, str]:
        return True, "ok"

    def screenshot(self, path: str) -> tuple[bool, str]:
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG-fake")
        return True, "ok"

    def close(self) -> None:
        pass


class FakeLLM:
    def __init__(self, decisions: list[dict]):
        self.decisions = list(decisions)
        self.calls: list[list] = []

    def chat(self, messages: list, **kwargs) -> str:
        import json
        self.calls.append(messages)
        return json.dumps(self.decisions.pop(0), ensure_ascii=False)


def test_run_explore_injects_view_context_and_records_tab_click(tmp_path):
    """指纹开关默认开：决策 prompt 注入已见视图清单；点击 tab 后触发器记为已触发。"""
    llm = FakeLLM([
        {"reason": "先看页面", "tool": "browser_snapshot", "args": {}},
        {"reason": "切到订单 tab", "tool": "browser_click", "args": {"ref": "e1"}},
        {"reason": "结束", "tool": "submit_cases",
         "args": {"cases": [], "done": True}},
    ])
    browser = FakeBrowser(FakePage())
    outcome = explorer_agent.run_explore(
        "https://spa.test/admin", out_dir=str(tmp_path), llm_client=llm,
        goal="探索管理台", browser_factory=lambda: browser)

    assert outcome.stop_reason == "done"
    assert outcome.details["views_seen"] == 1
    # 第 2 步决策（点击前）已注入已见视图清单与未触发入口「订单」
    second_call = "\n".join(str(m["content"]) for m in llm.calls[1])
    assert "已见视图清单" in second_call
    assert "tab/菜单入口：订单" in second_call
    # 第 3 步决策（点击 tab 订单之后）：订单已触发，剩余入口只剩「设置」
    third_call = "\n".join(str(m["content"]) for m in llm.calls[2])
    assert "tab/菜单入口：设置" in third_call
    assert "tab/菜单入口：订单" not in third_call
