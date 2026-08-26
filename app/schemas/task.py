"""任务相关的 Pydantic 响应模型。"""
from datetime import datetime
from typing import Optional

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
    created_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    steps: list[StepLogOut] = []

    class Config:
        from_attributes = True
