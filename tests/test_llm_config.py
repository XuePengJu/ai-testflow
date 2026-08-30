"""V2.4 FR-I：模型接入配置（厂商预设 / 用户级 Key 加密 / 生效优先级 / 连通测试 / 管线注入）。

外部 LLM 调用统一 mock `llm_service._post_chat`，离线可跑。
"""
import pytest

from app.core import config
from app.core.db import SessionLocal
from app.models.llm_config import LLMConfig
from app.models.user import User
from app.services import llm_service

_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _alice(db):
    return db.query(User).filter(User.username == "alice").first()


# ---------- 厂商预设 ----------

def test_providers_require_auth(client):
    assert client.get("/api/llm/providers").status_code == 401


def test_providers_listed(client, accounts):
    r = client.get("/api/llm/providers", headers=_h(accounts["user"]["token"]))
    assert r.status_code == 200
    data = r.json()
    # 三家重点厂商 + 智谱 Coding Plan 专用端点 + 自定义兜底
    for k in ("bailian", "zhipu", "zhipu_coding", "hunyuan", "deepseek", "kimi", "doubao", "custom"):
        assert k in data, f"缺厂商预设 {k}"
    assert data["zhipu_coding"]["base_url"].endswith("/api/coding/paas/v4")
    # 百炼有视觉模型标注
    assert any(m["vision"] for m in data["bailian"]["models"])
    # deepseek 全系不支持图像
    assert not any(m["vision"] for m in data["deepseek"]["models"])


# ---------- 访客限制 ----------

def test_guest_cannot_config(client, fresh_guest):
    token, _ = fresh_guest
    body = {"slot": "text", "provider": "bailian", "base_url": _URL,
            "model": "qwen-plus", "api_key": "sk-x"}
    assert client.get("/api/llm/config", headers=_h(token)).status_code == 403
    assert client.put("/api/llm/config", headers=_h(token), json=body).status_code == 403


# ---------- 个人配置：加密落库 + 脱敏回显 ----------

def test_user_config_roundtrip(client, accounts, db_session):
    tok = accounts["user"]["token"]
    r = client.put("/api/llm/config", headers=_h(tok), json={
        "slot": "text", "provider": "bailian", "base_url": _URL,
        "model": "qwen-plus", "api_key": "sk-test-1234"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["api_key_masked"] == "****1234"
    assert "api_key" not in out          # 永不回显明文

    # 落库为密文
    u = _alice(db_session)
    row = (db_session.query(LLMConfig)
           .filter(LLMConfig.user_id == u.id, LLMConfig.slot == "text").first())
    assert row and row.api_key_enc
    assert "sk-test-1234" not in row.api_key_enc
    assert llm_service.decrypt_key(row.api_key_enc, u.id) == "sk-test-1234"


def test_put_without_key_keeps_old(client, accounts, db_session):
    tok = accounts["user"]["token"]
    r = client.put("/api/llm/config", headers=_h(tok), json={
        "slot": "text", "provider": "bailian", "base_url": _URL, "model": "qwen-max"})
    assert r.status_code == 200 and r.json()["model"] == "qwen-max"
    u = _alice(db_session)
    row = (db_session.query(LLMConfig)
           .filter(LLMConfig.user_id == u.id, LLMConfig.slot == "text").first())
    # 未提供 api_key → 旧 Key 保留
    assert llm_service.decrypt_key(row.api_key_enc, u.id) == "sk-test-1234"


def test_text_slot_requires_key(client, accounts, db_session):
    tok = accounts["user"]["token"]
    u = _alice(db_session)
    # 先清掉已有配置，制造"无 Key"状态
    db_session.query(LLMConfig).filter(LLMConfig.user_id == u.id).delete()
    db_session.commit()
    r = client.put("/api/llm/config", headers=_h(tok), json={
        "slot": "text", "provider": "bailian", "base_url": _URL, "model": "qwen-plus"})
    assert r.status_code == 400 and "API Key" in r.json()["detail"]


def test_invalid_slot_rejected(client, accounts):
    r = client.put("/api/llm/config", headers=_h(accounts["user"]["token"]), json={
        "slot": "foo", "provider": "bailian", "base_url": _URL, "model": "m"})
    assert r.status_code == 422


# ---------- 生效优先级：user > platform > mock ----------

def test_effective_priority(client, accounts, monkeypatch):
    # 隔离服务器 env 兜底 key，确保下面只验证 user / platform 两层
    monkeypatch.setattr(config, "MODELSCOPE_API_KEY", "")
    monkeypatch.setattr(config, "DASHSCOPE_API_KEY", "")
    tok = accounts["user"]["token"]
    # alice 已配 text（roundtrip 用例）
    client.put("/api/llm/config", headers=_h(tok), json={
        "slot": "text", "provider": "bailian", "base_url": _URL,
        "model": "qwen-plus", "api_key": "sk-test-1234"})
    r = client.get("/api/llm/effective", headers=_h(tok))
    eff = r.json()
    assert eff["source"] == "user"
    assert eff["text"]["model"] == "qwen-plus"
    assert "api_key" not in eff["text"]      # 对外视图无 Key
    assert eff["vision"] is None

    # admin 配平台默认（text + vision）
    atok = accounts["admin"]["token"]
    client.put("/api/llm/platform-config", headers=_h(atok), json={
        "slot": "text", "provider": "zhipu", "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.5", "api_key": "sk-platform-key"})
    client.put("/api/llm/platform-config", headers=_h(atok), json={
        "slot": "vision", "provider": "bailian", "base_url": _URL,
        "model": "qwen-vl-plus", "api_key": "sk-platform-vl"})

    # alice 仍优先自己的配置（user > platform），vision 无个人配置 → 用平台默认
    eff2 = client.get("/api/llm/effective", headers=_h(tok)).json()
    assert eff2["source"] == "user"
    assert eff2["text"]["model"] == "qwen-plus"
    assert eff2["vision"] and eff2["vision"]["model"] == "qwen-vl-plus"

    # 新用户 carol：无个人配置 → 走平台默认
    client.post("/api/auth/register", json={
        "username": "carol-llm", "email": "carol-llm@test.com", "password": "Carol12345"})
    r = client.post("/api/auth/login", data={"username": "carol-llm", "password": "Carol12345"})
    btok = r.json()["access_token"]
    eff3 = client.get("/api/llm/effective", headers=_h(btok)).json()
    assert eff3["source"] == "platform"
    assert eff3["text"]["model"] == "glm-4.5"
    assert eff3["vision"]["model"] == "qwen-vl-plus"

    # 方案3：魔搭 env 兜底应高于平台默认 GLM（GLM 仍保留，仅作魔搭缺失时的兜底）
    monkeypatch.setattr(config, "MODELSCOPE_API_KEY", "ms-test-dummy")
    eff4 = client.get("/api/llm/effective", headers=_h(btok)).json()
    assert eff4["source"] == "env"
    assert eff4["text"]["provider"] == "modelscope"
    assert eff4["text"]["model"] == config.MODELSCOPE_MODEL


def test_platform_config_admin_only(client, accounts):
    # 普通用户 403
    assert client.get("/api/llm/platform-config",
                      headers=_h(accounts["user"]["token"])).status_code == 403
    # admin 可读
    r = client.get("/api/llm/platform-config", headers=_h(accounts["admin"]["token"]))
    assert r.status_code == 200 and isinstance(r.json(), list)


def test_delete_slot(client, accounts, db_session):
    tok = accounts["user"]["token"]
    client.put("/api/llm/config", headers=_h(tok), json={
        "slot": "vision", "provider": "bailian", "base_url": _URL,
        "model": "qwen-vl-plus", "api_key": "sk-vl-5678"})
    r = client.delete("/api/llm/config/vision", headers=_h(tok))
    assert r.status_code == 204
    u = _alice(db_session)
    assert (db_session.query(LLMConfig)
            .filter(LLMConfig.user_id == u.id, LLMConfig.slot == "vision").count()) == 0


# ---------- 连通测试 ----------

def test_connectivity_ok_and_fail(client, accounts, monkeypatch):
    tok = accounts["user"]["token"]

    def fake_post(base_url, api_key, payload, timeout=60):
        assert api_key == "sk-test-1234"
        assert payload["model"] == "qwen-plus"
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm_service, "_post_chat", fake_post)
    r = client.post("/api/llm/test", headers=_h(tok), json={
        "base_url": _URL, "model": "qwen-plus", "api_key": "sk-test-1234"})
    d = r.json()
    assert d["ok"] is True and d["reply"] == "ok" and d["latency_ms"] >= 0

    def bad_post(base_url, api_key, payload, timeout=60):
        raise llm_service.LLMError("HTTP 401：无效的 API Key")

    monkeypatch.setattr(llm_service, "_post_chat", bad_post)
    r2 = client.post("/api/llm/test", headers=_h(tok), json={
        "base_url": _URL, "model": "qwen-plus", "api_key": "sk-bad"})
    assert r2.json()["ok"] is False and "401" in r2.json()["error"]


def test_connectivity_reuses_saved_key(client, accounts, monkeypatch):
    """api_key 留空 → 复用已保存的 Key（不再让用户重复粘贴）。"""
    tok = accounts["user"]["token"]
    seen = {}

    def fake_post(base_url, api_key, payload, timeout=60):
        seen["key"] = api_key
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm_service, "_post_chat", fake_post)
    r = client.post("/api/llm/test", headers=_h(tok), json={
        "base_url": _URL, "model": "qwen-plus", "api_key": ""})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert seen["key"] == "sk-test-1234"


def test_connectivity_requires_key(client, accounts):
    # 无已保存 Key 的管理员（平台配置已存但 admin 自己无个人配置 → 复用平台 Key）
    # 先清平台配置制造无 Key 场景
    db = SessionLocal()
    db.query(LLMConfig).filter(LLMConfig.user_id == 0).delete()
    db.commit()
    db.close()
    r = client.post("/api/llm/test", headers=_h(accounts["admin"]["token"]), json={
        "base_url": _URL, "model": "qwen-plus", "api_key": ""})
    assert r.status_code == 400


# ---------- 生成管线：注入真实 client ----------

def test_generate_with_injected_client():
    """CaseGenerator 优先使用平台注入的 client（OpenAI 兼容）。"""
    from app.services.pipeline_lib import lib_generate
    from src.models.testcase import RequirementUnit

    class FakeClient:
        def generate(self, prompt):
            return ('[{"title":"AI注入生成用例","module":"登录","case_type":"正向",'
                    '"priority":"P1","steps":["输入用户名","输入密码","点击登录"],'
                    '"expected":"登录成功"}]')

    units = [RequirementUnit(name="登录", kind="action", description="用户登录功能")]
    cases = lib_generate(units, client=FakeClient())
    assert any(c.title == "AI注入生成用例" for c in cases)


def test_full_task_with_real_llm_path(client, accounts, monkeypatch):
    """端到端：配置好 Key 的用户提交任务 → 引擎走真实调用路径（HTTP mock）。"""
    tok = accounts["user"]["token"]
    client.put("/api/llm/config", headers=_h(tok), json={
        "slot": "text", "provider": "bailian", "base_url": _URL,
        "model": "qwen-plus", "api_key": "sk-test-1234"})

    def fake_post(base_url, api_key, payload, timeout=60):
        assert base_url == _URL and api_key == "sk-test-1234"
        return {"choices": [{"message": {"content":
            '[{"title":"真实模型用例","module":"采购","case_type":"正向","priority":"P1",'
            '"steps":["填写采购单","提交"],"expected":"创建成功"}]'
        }}]}

    monkeypatch.setattr(llm_service, "_post_chat", fake_post)
    r = client.post("/api/tasks", headers=_h(tok), data={
        "text": "采购管理 - 采购订单创建。功能点：新增、编辑、提交审批。",
        "kind": "business", "formats": "json", "name": "LLM路径任务"})
    assert r.status_code == 201, r.text
    task_id = r.json()["id"]

    t = client.get(f"/api/tasks/{task_id}", headers=_h(tok)).json()
    assert t["status"] == "completed", t["steps"]
    gen = [s for s in t["steps"] if s["name"] == "generator"][0]
    # 摘要里标注真实模型（非 mock）
    assert "qwen-plus" in gen["output_summary"]
    assert "mock" not in gen["output_summary"]


# ---------- 两段式视觉理解 ----------

class _FakeVision:
    def describe_image(self, url, hint=""):
        return "登录页面截图：包含用户名、密码输入框和登录按钮"


def test_extract_image_refs():
    text = "说明文字 ![登录页](https://x.com/login.png) 以及 data:image/png;base64,AAAA"
    refs = llm_service.extract_image_refs(text)
    assert len(refs) == 2
    assert refs[0] == "https://x.com/login.png"


def test_vision_enrich_replaces_images():
    text = "登录功能\n![登录页](https://x.com/login.png)\n"
    out, n = llm_service.vision_enrich(text, _FakeVision())
    assert n == 1
    assert "截图解读" in out
    assert "https://x.com/login.png" not in out


def test_vision_note_without_vision_model(client, accounts, monkeypatch):
    """未配置视觉模型时，含图片输入不报错，解析步骤标注'已忽略图片'。"""
    tok = accounts["user"]["token"]
    r = client.post("/api/tasks", headers=_h(tok), data={
        "text": "登录功能\n![登录页](https://x.com/login.png)\n功能点：登录、找回密码。",
        "kind": "business", "formats": "json", "name": "含图任务"})
    assert r.status_code == 201
    t = client.get(f"/api/tasks/{r.json()['id']}", headers=_h(tok)).json()
    assert t["status"] == "completed"
    parser = [s for s in t["steps"] if s["name"] == "parser"][0]
    assert "图片" in parser["output_summary"] and "忽略" in parser["output_summary"]
