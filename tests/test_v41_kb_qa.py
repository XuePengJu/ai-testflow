"""V4.1 知识库问答合一验证：citations 事件 + 会话 mode/kb_id 隔离。

脚本式 e2e（与 test_chat_rag.py 同风格，pytest 不收集）：
    PYTHONPATH=.:'.venv/lib/python3.13/site-packages' python tests/test_v41_kb_qa.py

前置：/tmp/kb_test3 目录可写；EMBEDDING_API_KEY 为空走 mock 向量。
"""
import os
import json
import shutil

shutil.rmtree("/tmp/kb_test3", ignore_errors=True)
os.makedirs("/tmp/kb_test3")
os.environ["AITF_ROOT_DIR"] = "/tmp/kb_test3"
os.environ["DATABASE_URL"] = "sqlite:////tmp/kb_test3/app.db"
os.environ["EMBEDDING_API_KEY"] = ""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.db import init_db, SessionLocal
from app.models.conversation import Conversation

init_db()

from main import app  # noqa: E402

u = SimpleNamespace(id=1, role="user")
app.dependency_overrides[get_current_user] = lambda: u
c = TestClient(app)

# 建库 + 入库文档
r = c.post("/api/knowledge/bases", json={"name": "V41测试库", "visibility": "private"})
kb_id = r.json()["id"]
doc = "# 采购退货管理需求\n## 业务规则\n退货金额超过5000元需要财务审批。\n质量问题退货必须上传质检报告。"
r = c.post(f"/api/knowledge/bases/{kb_id}/documents",
           files={"file": ("需求.md", doc.encode(), "text/markdown")})
assert r.json()["parse_status"] == "ready", r.text


def events_of(resp):
    return [l[7:].strip() for l in resp.text.split("\n") if l.startswith("event: ")]


def done_payload(resp):
    for block in resp.text.split("\n\n"):
        if "event: done" in block and "data: " in block:
            return json.loads(block.split("data: ", 1)[1])
    return {}


# 1) kb_qa：citations 事件先于正文 + 会话落库 mode/kb_id
r = c.post("/api/chat/stream", json={
    "message": "退货金额超过5000元需要怎么处理？", "kb_id": kb_id, "mode": "kb_qa"})
evs = events_of(r)
assert "citations" in evs, f"citations 缺失: {evs}"
assert evs.index("citations") < evs.index("done"), "citations 应先于 done"
cid = done_payload(r).get("conversation_id")
db = SessionLocal()
conv = db.get(Conversation, cid)
assert conv.mode == "kb_qa" and conv.kb_id == kb_id, (conv.mode, conv.kb_id)
db.close()

# 2) workflow（无 kb_id）：citations 同样出现（全库检索），会话默认 workflow
r = c.post("/api/chat/stream", json={"message": "退货金额超过5000元需要怎么处理？"})
evs2 = events_of(r)
cid2 = done_payload(r).get("conversation_id")
db = SessionLocal()
conv2 = db.get(Conversation, cid2)
assert conv2.mode == "workflow" and not conv2.kb_id
db.close()

# 3) 会话列表 ?mode= 过滤互不串
kb_qa_list = c.get("/api/conversations?mode=kb_qa").json()
wf_list = c.get("/api/conversations?mode=workflow").json()
assert len(kb_qa_list) == 1 and kb_qa_list[0]["mode"] == "kb_qa"
assert all(x.get("mode", "workflow") != "kb_qa" for x in wf_list)

# 4) citations items 元数据结构（引用溯源映射链路）
for block in r.text.split("\n\n"):
    if "event: citations" in block and "data: " in block:
        item = json.loads(block.split("data: ", 1)[1])["items"][0]
        for k in ("chunk_id", "knowledge_id", "doc_title", "snippet", "score"):
            assert k in item, f"citations 缺字段 {k}: {item}"
        break

print("V41 KB-QA OK")
