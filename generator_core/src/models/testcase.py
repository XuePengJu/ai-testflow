"""测试用例数据模型（Pydantic v2，缺失时降级 dataclass，保证零依赖也能跑）。"""
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


if _PYDANTIC:
    class TestCase(BaseModel):
        case_id: str = ""
        title: str
        module: str = ""
        case_type: CaseType
        priority: Priority = Priority.P1
        pre_condition: str = ""
        steps: List[str] = Field(default_factory=list)
        expected: str = ""
        test_data: Optional[str] = None

        def to_row(self):
            return [self.case_id, self.title, self.module, self.case_type.value,
                    self.priority.value, self.pre_condition,
                    "\n".join(self.steps), self.expected, self.test_data or ""]

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
        expected: str = ""
        test_data: Optional[str] = None

        def to_row(self):
            return [self.case_id, self.title, self.module, self.case_type.value,
                    self.priority.value, self.pre_condition,
                    "\n".join(self.steps), self.expected, self.test_data or ""]

        def to_dict(self):
            return {
                "case_id": self.case_id, "title": self.title, "module": self.module,
                "case_type": self.case_type.value, "priority": self.priority.value,
                "pre_condition": self.pre_condition, "steps": self.steps,
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
