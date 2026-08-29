"""任务与步骤日志的数据模型（SQLAlchemy）。"""
from app.core.utils import utcnow

from sqlalchemy import (
    Column, String, Integer, Text, DateTime, Float, ForeignKey,
)
from app.core.db import Base


class Task(Base):
    """一次测试用例生成任务。"""
    __tablename__ = "tasks"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False, default="未命名任务")
    kind = Column(String, nullable=False, default="api")       # api / business
    source_type = Column(String, nullable=False, default="file")  # file / text
    input_ref = Column(Text, default="")       # 上传文件名 或 直接粘贴的规格文本
    formats = Column(String, default="xlsx,json")  # 导出格式，逗号分隔
    status = Column(String, nullable=False, default="pending")  # pending/running/completed/failed
    user_id = Column(Integer, nullable=True, index=True)        # 归属用户（存量迁移归 admin）
    category_id = Column(Integer, nullable=True, index=True)    # 归属分类（NULL=未分类）
    input_summary = Column(Text, default="")
    cases_count = Column(Integer, default=0)
    duration_ms = Column(Float, default=0.0)
    cases_json = Column(Text, default="[]")   # 生成用例列表的 JSON
    report_json = Column(Text, default="{}")  # ReviewerAgent 质量报告
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime)

    def user_data_dir(self, db) -> str:
        """任务归属用户的文件目录；无归属（存量迁移前）返回平铺目录。"""
        if self.user_id:
            from app.models.user import User
            u = db.get(User, self.user_id)
            if u and u.data_dir:
                return u.data_dir
        return ""


class StepLog(Base):
    """工作流中每个 Agent 步骤的执行日志（可观测）。"""
    __tablename__ = "step_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False, index=True)
    name = Column(String, nullable=False)     # parser/generator/reviewer/exporter
    title = Column(String, default="")
    status = Column(String, nullable=False, default="pending")  # pending/running/completed/failed/skipped
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    duration_ms = Column(Float)
    input_summary = Column(Text, default="")
    output_summary = Column(Text, default="")
    error = Column(Text, default="")
