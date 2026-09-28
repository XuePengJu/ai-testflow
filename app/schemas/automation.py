"""被测系统（TestTarget）+ 自动化执行（ExecutionRun）的 Pydantic 响应模型。

安全约定：
- username / password 永不出现在任何响应里，只给 has_auth 布尔
- ExecutionRun.report_json 以解析后的结构化对象回传（contract-m2 2），不回传原始 JSON 文本
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class TargetOut(BaseModel):
    id: str
    name: str
    base_url: str
    auth_type: str = "none"
    # 是否已配置登录凭据（前端据此显示「需登录」徽标，不回传明文）
    has_auth: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ============ M2 执行引擎（contract-m2 2/3） ============

class ExecutionRunOut(BaseModel):
    """执行记录列表项（不含 report 大对象，列表页轻量轮询）。"""
    id: str
    task_id: str
    trigger: str = "manual"          # manual / retry
    status: str = "pending"          # pending / running / completed / failed
    progress: int = 0
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_ms: int = 0
    heal_round: int = 0               # M4 自愈：已进行的自愈轮次（0=未触发）
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CaseReport(BaseModel):
    """单用例执行结果（contract-m2 2 的 cases[] 元素，命名严格对齐契约）。"""
    case_id: str                     # TC-001
    node_id: str                     # test_cases_0.py::test_tc_001
    title: str = ""
    outcome: str                     # passed / failed / skipped
    duration_ms: int = 0
    error: Optional[str] = None      # 断言信息或 null
    screenshot: Optional[str] = None # shots/xxx.png 或 null（相对 auto_dir）


class ExecutionReport(BaseModel):
    """组装后的报告对象（contract-m2 2 的 report_json 结构）。"""
    summary: dict[str, Any]          # {total, passed, failed, skipped, duration_ms}
    cases: list[CaseReport]
    environment: dict[str, Any]      # {browser, base_url}
    heal: Optional[dict[str, Any]] = None  # M4 自愈：{rounds, log, suspected_bugs}


class ExecutionDetail(ExecutionRunOut):
    """执行详情（GET /api/executions/{run_id}）。

    report：终态时解析后的报告对象；pending/running 时为 None（前端轮询只看 progress/total）。
    """
    auto_dir: str = ""
    report: Optional[ExecutionReport] = None
