"""对话 RAG 接入验证：建库传文档 → /chat/stream 带 kb_id / 不带 kb_id。"""
import os

os.environ["AITF_ROOT_DIR"] = "/tmp/kb_test"
os.environ["DATABASE_URL"] = "sqlite:////tmp/kb_test/app.db"
os.environ["EMBEDDING_API_KEY"] = ""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.db import init_db

init_db()

from main import app  # noqa: E402

u = SimpleNamespace(id=1, role="user")
app.dependency_overrides[get_current_user] = lambda: u
c = TestClient(app)

r = c.post("/api/knowledge/bases", json={"name": "采购退货知识库", "visibility": "private"})
kb_id = r.json()["id"]
doc_text = "# 采购退货管理需求\n## 业务规则\n退货金额超过5000元需要财务审批。\n质量问题退货必须上传质检报告。"
r = c.post(f"/api/knowledge/bases/{kb_id}/documents", files={"file": ("需求.md", doc_text.encode(), "text/markdown")})
assert r.json()["parse_status"] == "ready", r.text

r = c.post("/api/chat/stream", json={"message": "退货超过5000元需要怎么处理？", "kb_id": kb_id})
print("chat(带kb) status:", r.status_code)
print("  done:", "event: done" in r.text, "| error:", "event: error" in r.text)
assert r.status_code == 200 and "event: error" not in r.text

r2 = c.post("/api/chat/stream", json={"message": "退货审批流程是什么？"})
print("chat(无kb) status:", r2.status_code)
print("  done:", "event: done" in r2.text, "| error:", "event: error" in r2.text)
assert r2.status_code == 200 and "event: error" not in r2.text
print("CHAT RAG OK")
