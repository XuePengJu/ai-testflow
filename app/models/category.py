"""任务分类（多级树，用户隔离）数据模型。"""
from app.core.utils import utcnow

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey

from app.core.db import Base


class Category(Base):
    """自定义任务分类节点，parent_id 构成树；user_id 隔离，各用户独立分类树。"""
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)
    parent_id = Column(Integer, nullable=True, index=True)   # NULL = 顶级分类
    user_id = Column(Integer, nullable=False, index=True)
    sort = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow)
