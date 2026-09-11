"""测试用例数据模型（Pydantic v2，缺失时降级 dataclass，保证零依赖也能跑）。"""
import re
from enum import Enum
from typing import List, Optional

try:
    from pydantic import BaseModel, Field
    _PYDANTIC = True
except ImportError:
    from dataclasses import dataclass, field
    _PYDANTIC = False


class CaseType(str, Enum):
    POSITIVE = "正向"
    NEGATIVE = "异常"
    BOUNDARY = "边界值"
    SCENARIO = "场景组合"


class Priority(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


def align_step_expectations(
    steps: list[str],
    step_expectations: list[str] | None,
    expected: str,
) -> list[str]:
    """保证「每个步骤都有预期结果」的硬约束。

    规则：
    1. step_expectations 数量与步骤数一致且全部非空 → 原样使用
    2. 只有 1 个步骤 → 用整体 expected 作为该步骤预期
    3. 多步骤但逐步预期缺失/不齐 → 每步挂整体 expected（冗余但信息完整）
    4. steps 为空 → 返回空列表

    返回与过滤空串后的 steps 一一对应的预期列表。
    """
    steps = [s for s in (steps or []) if str(s).strip()]
    if not steps:
        return []
    if step_expectations and len(step_expectations) == len(steps) and all(
        str(x).strip() for x in step_expectations
    ):
        return [str(x).strip() for x in step_expectations]
    exp = (expected or "").strip()
    if len(steps) == 1:
        return [exp]
    return [exp] * len(steps)


def ensure_case_ids(cases: list) -> list:
    """补全缺失的用例ID，保证每条用例都有 TC 编号（兼容 TestCase 对象与 dict）。

    规则：沿用现有最大的 `TC-数字` 序号（兼容 TC-001 / TC001 / TC-1）递增；
    全部缺失时从 TC-001 开始。已有 ID 保持不变。
    """
    _get = lambda c: (c.case_id if not isinstance(c, dict) else (c.get("case_id") or ""))
    _set = lambda c, v: (setattr(c, "case_id", v) if not isinstance(c, dict) else c.__setitem__("case_id", v))

    max_n = 0
    for c in cases:
        cid = str(_get(c) or "").strip()
        m = re.match(r"^TC-?0*(\d+)$", cid)
        if m:
            max_n = max(max_n, int(m.group(1)))
    for c in cases:
        if not str(_get(c) or "").strip():
            max_n += 1
            _set(c, f"TC-{max_n:03d}")
    return cases


if _PYDANTIC:
    class TestCase(BaseModel):
        case_id: str = ""
        title: str
        module: str = ""
        case_type: CaseType
        priority: Priority = Priority.P1
        pre_condition: str = ""
        steps: List[str] = Field(default_factory=list)
        step_expectations: List[str] = Field(default_factory=list)
        expected: str = ""
        test_data: Optional[str] = None

        def resolved_expectations(self) -> list[str]:
            """返回与 steps 一一对应的逐步预期（旧数据缺失时自动兜底）。"""
            return align_step_expectations(self.steps, self.step_expectations, self.expected)

        def to_row(self):
            return [self.case_id, self.title, self.module, self.case_type.value,
                    self.priority.value, self.pre_condition,
                    "\n".join(self.steps), "\n".join(self.resolved_expectations()),
                    self.expected, self.test_data or ""]

        def to_dict(self):
            return self.model_dump()

    class RequirementUnit(BaseModel):
        """解析后的测试单元（接口 / 模块 / action）"""
        name: str
        kind: str = "api"            # api | module | action
        path: str = ""
        description: str = ""
        params: List[str] = Field(default_factory=list)
        constraints: str = ""
else:
    @dataclass
    class TestCase:
        case_id: str = ""
        title: str = ""
        module: str = ""
        case_type: CaseType = CaseType.POSITIVE
        priority: Priority = Priority.P1
        pre_condition: str = ""
        steps: List[str] = field(default_factory=list)
        step_expectations: List[str] = field(default_factory=list)
        expected: str = ""
        test_data: Optional[str] = None

        def resolved_expectations(self) -> list[str]:
            """返回与 steps 一一对应的逐步预期（旧数据缺失时自动兜底）。"""
            return align_step_expectations(self.steps, self.step_expectations, self.expected)

        def to_row(self):
            return [self.case_id, self.title, self.module, self.case_type.value,
                    self.priority.value, self.pre_condition,
                    "\n".join(self.steps), "\n".join(self.resolved_expectations()),
                    self.expected, self.test_data or ""]

        def to_dict(self):
            return {
                "case_id": self.case_id, "title": self.title, "module": self.module,
                "case_type": self.case_type.value, "priority": self.priority.value,
                "pre_condition": self.pre_condition, "steps": self.steps,
                "step_expectations": self.step_expectations,
                "expected": self.expected, "test_data": self.test_data,
            }

    @dataclass
    class RequirementUnit:
        name: str = ""
        kind: str = "api"
        path: str = ""
        description: str = ""
        params: List[str] = field(default_factory=list)
        constraints: str = ""
