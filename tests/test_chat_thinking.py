"""思考（reasoning）链路回归测试。

覆盖四件事：
1. 客户端解析 —— 思考在独立字段（reasoning_content）时必须单独产出 think 事件，
   只读 content 会让思考整段丢失、思考面板恒空。
2. 开关注入 —— enable_thinking 按「深度思考」开关注入；端点不认（400）时
   自动去掉参数重试一次，并记住该端点不再注入。
3. 开关生效 —— 关闭思考时换用无 <think> 要求的系统提示词，并丢弃 think 事件。
4. 落库与切分 —— 思考单独存库（否则历史消息思考面板为空）；<think>/<thinking>
   两种标签前后端都能切。
"""
import asyncio
import json

import pytest

from app.api import chat as chat_api
from app.schemas.llm_config import ChatIn
from app.services import llm_service


def _collect(gen):
    """把 async generator 收成事件列表。"""

    async def _run():
        return [ev async for ev in gen]

    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def _clean_endpoint_cache():
    """_NO_THINKING_PARAM 是模块级缓存，测试间必须清空，否则相互污染。"""
    llm_service._NO_THINKING_PARAM.clear()
    yield
    llm_service._NO_THINKING_PARAM.clear()


@pytest.fixture
def platform_cfg(monkeypatch):
    """把生效配置固定为"平台默认模型"，保证走真实分支。"""
    monkeypatch.setattr(
        llm_service,
        "resolve_effective",
        lambda db, user: {
            "source": "platform",
            "text": {"base_url": "http://fake.local/v1", "api_key": "k", "model": "m"},
            "vision": None,
        },
    )


# ============ 1. 客户端：思考独立字段 → think 事件 ============

class _FakeResp:
    def __init__(self, status_code, lines=None, body=b""):
        self.status_code = status_code
        self._lines = lines or []
        self._body = body

    def read(self):
        return self._body

    def iter_lines(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHttpClient:
    """httpx.Client 替身：按 scripts 顺序发响应，记录每次请求体。"""

    scripts: list = []
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url, headers=None, json=None):
        # 必须存副本：重试路径会 pop payload 里的 enable_thinking，
        # 存引用的话第一次请求的记录会被一起改掉，断言会误判
        type(self).calls.append(dict(json) if json else json)
        return type(self).scripts.pop(0)


def _lines(*deltas):
    """把 delta dict 列表编成 SSE 行。"""
    out = [f"data: {json.dumps(d)}" for d in deltas]
    out.append("data: [DONE]")
    return out


@pytest.fixture
def fake_http(monkeypatch):
    _FakeHttpClient.scripts = []
    _FakeHttpClient.calls = []
    monkeypatch.setattr(llm_service.httpx, "Client", _FakeHttpClient)
    return _FakeHttpClient


def test_reasoning_content_becomes_think_event(fake_http):
    """reasoning_content 必须产出 think 事件，且不与正文混在一起。"""
    fake_http.scripts = [_FakeResp(200, _lines(
        {"choices": [{"delta": {"reasoning_content": "先想"}}]},
        {"choices": [{"delta": {"reasoning_content": "一下"}}]},
        {"choices": [{"delta": {"content": "正式"}}]},
        {"choices": [{"delta": {"content": "回复"}}]},
    ))]
    client = llm_service.OpenAICompatClient("http://x/v1", "k", "m")

    events = list(client.chat_stream([{"role": "user", "content": "hi"}]))

    assert [k for k, _ in events[:-1]] == ["think", "think", "delta", "delta"]
    assert "".join(p for k, p in events if k == "think") == "先想一下"
    assert "".join(p for k, p in events if k == "delta") == "正式回复"
    done = events[-1][1]
    assert done["thinking"] == "先想一下"      # 思考随 done 一起回传（落库用）
    assert "先想" not in done["full"]          # 正文里绝不能混入思考


def test_reasoning_field_fallback_order(fake_http):
    """字段名各厂商不同：reasoning / thinking 都要能兜住。"""
    fake_http.scripts = [_FakeResp(200, _lines(
        {"choices": [{"delta": {"reasoning": "A"}}]},
        {"choices": [{"delta": {"thinking": "B"}}]},
    ))]
    client = llm_service.OpenAICompatClient("http://x/v1", "k", "m")

    events = list(client.chat_stream([{"role": "user", "content": "hi"}]))

    assert "".join(p for k, p in events if k == "think") == "AB"


# ============ 2. 开关注入与 400 降级 ============

def test_enable_thinking_injected_by_flag(fake_http):
    """开关 True/False 分别注入对应值；None 表示不注入（走模型默认）。"""
    for flag, expected in ((True, True), (False, False)):
        fake_http.scripts = [_FakeResp(200, _lines())]
        fake_http.calls = []
        client = llm_service.OpenAICompatClient("http://x/v1", "k", "m")
        list(client.chat_stream([{"role": "user", "content": "hi"}], enable_thinking=flag))
        assert fake_http.calls[0]["enable_thinking"] is expected

    fake_http.scripts = [_FakeResp(200, _lines())]
    fake_http.calls = []
    client = llm_service.OpenAICompatClient("http://x/v1", "k", "m")
    list(client.chat_stream([{"role": "user", "content": "hi"}], enable_thinking=None))
    assert "enable_thinking" not in fake_http.calls[0]


def test_400_on_thinking_param_retries_without_it(fake_http):
    """端点不认 enable_thinking（400）→ 去掉参数重试一次，用户无感。"""
    fake_http.scripts = [
        _FakeResp(400, body=b'{"error":"unknown field enable_thinking"}'),
        _FakeResp(200, _lines({"choices": [{"delta": {"content": "ok"}}]})),
    ]
    client = llm_service.OpenAICompatClient("http://x/v1", "k", "m")

    events = list(client.chat_stream([{"role": "user", "content": "hi"}], enable_thinking=True))

    assert "error" not in [k for k, _ in events]
    assert "".join(p for k, p in events if k == "delta") == "ok"
    assert len(fake_http.calls) == 2
    assert "enable_thinking" in fake_http.calls[0]
    assert "enable_thinking" not in fake_http.calls[1]
    # 该端点被记住 → 下次不再注入，省掉一次 400
    assert "http://x/v1|m" in llm_service._NO_THINKING_PARAM


def test_known_bad_endpoint_skips_param(fake_http):
    """已记住不认参数的端点，后续请求直接不带该参数。"""
    llm_service._NO_THINKING_PARAM.add("http://x/v1|m")
    fake_http.scripts = [_FakeResp(200, _lines())]
    client = llm_service.OpenAICompatClient("http://x/v1", "k", "m")

    list(client.chat_stream([{"role": "user", "content": "hi"}], enable_thinking=False))

    assert "enable_thinking" not in fake_http.calls[0]


# ============ 3. 开关对服务层/提示词的影响 ============

class _FakeClient:
    """OpenAICompatClient 替身：按 script 顺序吐事件。"""

    script: list = []
    last_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def chat_stream(self, messages, **kwargs):
        type(self).last_kwargs = kwargs
        for ev in self.script:
            yield ev


def test_think_events_dropped_when_thinking_off(platform_cfg, monkeypatch):
    """关闭思考：think 事件不透传，但正文照常送达。"""
    _FakeClient.script = [
        ("think", "内部推理"),
        ("delta", "正式回复"),
        ("done", {"full": "正式回复", "thinking": "内部推理"}),
    ]
    monkeypatch.setattr(llm_service, "OpenAICompatClient", _FakeClient)

    events = _collect(llm_service.chat_stream(None, None, "你好", None, "", "", False))

    assert "think" not in [k for k, _ in events]
    assert "".join(p for k, p in events if k == "delta") == "正式回复"
    # 开关必须透传到客户端
    assert _FakeClient.last_kwargs.get("enable_thinking") is False


def test_think_events_pass_through_when_thinking_on(platform_cfg, monkeypatch):
    """开启思考：think 事件原样透传给 SSE 层。"""
    _FakeClient.script = [("think", "内部推理"), ("delta", "正式回复")]
    monkeypatch.setattr(llm_service, "OpenAICompatClient", _FakeClient)

    events = _collect(llm_service.chat_stream(None, None, "你好", None, "", "", True))

    assert "".join(p for k, p in events if k == "think") == "内部推理"


def test_system_prompt_switches_with_flag():
    """关思考必须同时换系统提示词（光靠参数关不掉模型主动输出 <think>）。"""
    on = llm_service._build_messages("需求", None, "", True)[0]["content"]
    off = llm_service._build_messages("需求", None, "", False)[0]["content"]

    # 开：明确要求模型用 <think> 包裹思考；关：明确禁止输出思考标记
    assert "思考过程用 <think>...</think> 包裹" in on
    assert "思考过程用 <think>...</think> 包裹" not in off
    assert "不要输出" in off


def test_mock_omits_thinking_when_disabled(monkeypatch):
    """mock 模式同样尊重开关：关掉后不出思考段，避免面板空有标题。"""
    monkeypatch.setattr(
        llm_service, "resolve_effective",
        lambda db, user: {"source": "mock", "text": None, "vision": None},
    )

    on = _collect(llm_service.chat_stream(None, None, "你好", None, "", "", True))
    off = _collect(llm_service.chat_stream(None, None, "你好", None, "", "", False))

    assert "思考" in "".join(p for k, p in on if k == "delta")
    assert "思考" not in "".join(p for k, p in off if k == "delta")


# ============ 4. 落库与标签切分 ============

class _FakeConv:
    id = "c1"
    user_id = 7
    updated_at = None


class _FakeUser:
    id = 7


class _FakeDB:
    def __init__(self):
        self.added = []

    def get(self, model, pk):
        return _FakeConv()

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass


def test_persist_chat_stores_thinking_separately():
    """思考必须单独落到 Message.thinking，否则历史消息的思考面板是空的。"""
    db = _FakeDB()
    body = ChatIn(message="看看这份需求", conversation_id="c1")

    chat_api._persist_chat(db, _FakeUser(), body, "正式回复", "先想一下")

    assistant = db.added[-1]
    assert assistant.content == "正式回复"
    assert assistant.thinking == "先想一下"


@pytest.mark.parametrize("tag", ["think", "thinking"])
def test_split_think_accepts_both_tags(tag):
    """前后端标签集合必须一致：<think> 与 <thinking> 都要能切出来。"""
    thinking, reply = chat_api._split_think(f"<{tag}>推理内容</{tag}>正式回复")

    assert thinking == "推理内容"
    assert reply == "正式回复"


def test_split_think_keeps_mock_marker():
    """mock 的中文分隔符格式不能被这次改动破坏。"""
    thinking, reply = chat_api._split_think(" 思考\n想到了\n思考\n回复内容")

    assert "想到了" in thinking
    assert reply == "回复内容"
