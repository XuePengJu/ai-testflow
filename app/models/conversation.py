"""会话与消息数据模型（对话记录持久化）。

会话（conversation）承载首页的多轮对话；消息（message）存每条 user/assistant
发言，assistant 消息可关联一个生成任务（task_id），聊天流回放时按 task_id 实时拉
该任务的节点步骤（StepLog）与用例（Task.cases_json），避免冗余存储。
"""
from app.core.utils import utcnow

from sqlalchemy import (
    Column, String, Integer, Text, DateTime, ForeignKey,
)
from app.core.db import Base


class Conversation(Base):
    """一次对话会话（所有对话都落库，跨刷新可回放）。"""
    __tablename__ = "conversations"

    id = Column(String, primary_key=True)                # uuid hex 12
    user_id = Column(Integer, nullable=False, index=True)
    title = Column(String, default="新会话")              # 首条用户消息截断
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Message(Base):
    """会话中的一条消息（role: user / assistant）。"""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String, nullable=False)                # user / assistant
    content = Column(Text, default="")
    thinking = Column(Text, default="")                  # assistant 思考过程
    task_id = Column(String, nullable=True, index=True)  # 关联生成任务（点"生成测试用例"后回填）
    created_at = Column(DateTime, default=utcnow)
