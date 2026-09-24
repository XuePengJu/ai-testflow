"""e2e 全链路任务自动创建会话：create_task(kind=e2e) 建会话 + 消息顺序 + 占位回填 task_id + 越权拒绝。"""
from app.core.db import SessionLocal
from app.models.conversation import Conversation, Message
from app.models.task import Task
from app.models.user import User


def _user_id(username: str) -> str:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        assert u, f"用户 {username} 不存在"
        return u.id
    finally:
        db.close()


class TestE2EAutoConversation:
    def test_auto_create_conversation_and_messages(self, client, accounts, db_session):
        """kind=e2e 未传 conversation_id：自动建会话 + user/assistant 两条消息，任务指向会话。"""
        token = accounts["user"]["token"]
        r = client.post("/api/tasks", headers={"Authorization": f"Bearer {token}"}, data={
            "kind": "e2e", "url": "https://example.com", "name": "全链路冒烟",
        })
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        conv_id = r.json()["conversation_id"]
        assert conv_id, "创建响应必须带回 conversation_id"

        conv = db_session.get(Conversation, conv_id)
        assert conv is not None
        assert conv.user_id == _user_id("alice")
        assert conv.title == "全链路冒烟"

        msgs = (db_session.query(Message)
                .filter(Message.conversation_id == conv_id)
                .order_by(Message.id.asc()).all())
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "🌐 发起全链路测试：https://example.com"
        assert msgs[1].role == "assistant"
        assert msgs[1].content == "正在探索被测系统并生成测试用例…"

        task = db_session.get(Task, tid)
        assert task.conversation_id == conv_id

    def test_auto_title_fallback_to_url(self, client, accounts, db_session):
        """未传任务名：会话标题取「🌐 全链路测试-{url}」。"""
        token = accounts["user"]["token"]
        r = client.post("/api/tasks", headers={"Authorization": f"Bearer {token}"}, data={
            "kind": "e2e", "url": "https://example.com",
        })
        assert r.status_code == 201, r.text
        conv = db_session.get(Conversation, r.json()["conversation_id"])
        assert conv is not None
        assert conv.title == "🌐 全链路测试-https://example.com"

    def test_placeholder_task_id_backfilled(self, client, accounts, db_session):
        """create_task 末尾既有回填逻辑：新会话占位 assistant 消息被回填新任务 id。"""
        token = accounts["user"]["token"]
        r = client.post("/api/tasks", headers={"Authorization": f"Bearer {token}"}, data={
            "kind": "e2e", "url": "https://example.com",
        })
        assert r.status_code == 201, r.text
        tid = r.json()["id"]
        conv_id = r.json()["conversation_id"]
        placeholder = (db_session.query(Message)
                       .filter(Message.conversation_id == conv_id, Message.role == "assistant")
                       .order_by(Message.id.desc()).first())
        assert placeholder is not None
        assert placeholder.task_id == tid

    def test_foreign_conversation_rejected_404(self, client, accounts):
        """传入他人会话 id → 404（不暴露存在性），任务不创建。"""
        # alice 先建一个自己的会话
        user_token = accounts["user"]["token"]
        r = client.post("/api/conversations", headers={"Authorization": f"Bearer {user_token}"},
                        json={"title": "alice 的会话"})
        assert r.status_code == 201, r.text
        conv_id = r.json()["id"]

        # admin 拿别人的会话提交任务 → 404
        admin_token = accounts["admin"]["token"]
        r2 = client.post("/api/tasks", headers={"Authorization": f"Bearer {admin_token}"}, data={
            "kind": "e2e", "url": "https://example.com", "conversation_id": conv_id,
        })
        assert r2.status_code == 404

    def test_nonexistent_conversation_rejected_404(self, client, accounts):
        """传入不存在的会话 id → 404。"""
        token = accounts["user"]["token"]
        r = client.post("/api/tasks", headers={"Authorization": f"Bearer {token}"}, data={
            "kind": "e2e", "url": "https://example.com", "conversation_id": "no-such-conv",
        })
        assert r.status_code == 404

    def test_own_conversation_accepted(self, client, accounts, db_session):
        """传自己的会话 id：任务挂到该会话，最后一条无 task_id 的 assistant 消息被回填。"""
        token = accounts["user"]["token"]
        r = client.post("/api/conversations", headers={"Authorization": f"Bearer {token}"},
                        json={"title": "我的会话"})
        assert r.status_code == 201, r.text
        conv_id = r.json()["id"]

        r2 = client.post("/api/tasks", headers={"Authorization": f"Bearer {token}"}, data={
            "kind": "e2e", "url": "https://example.com", "conversation_id": conv_id,
        })
        assert r2.status_code == 201, r2.text
        assert r2.json()["conversation_id"] == conv_id
        # 不新增消息：自动建消息只发生在未传 conversation_id 时
        count = (db_session.query(Message)
                 .filter(Message.conversation_id == conv_id).count())
        assert count == 0
