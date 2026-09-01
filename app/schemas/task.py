"""任务相关的 Pydantic 响应模型。"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class StepLogOut(BaseModel):
    name: str
    title: str
    status: str
    duration_ms: Optional[float] = None
    input_summary: Optional[str] = None
    output_summary: Optional[str] = None
    error: Optional[str] = None


class TaskOut(BaseModel):
    id: str
    name: str
    kind: str
    source_type: str
    status: str
    cases_count: int
    duration_ms: float
    formats: str = "xlsx,json,xmind"
    category_id: Optional[int] = None
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    steps: list[StepLogOut] = []
    # 结构化用例列表（仅详情接口包含；列表接口为 []）。
    # 由 api/tasks.py 的 _to_out(include_cases=True) 注入，避免大 payload 拖慢列表渲染。
    cases: list[dict[str, Any]] = []

    class Config:
        from_attributes = True
