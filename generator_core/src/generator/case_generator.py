"""生成核心编排：解析单元 →（mock 或 百炼）→ 合并去重 → 编号。"""
import json
import re
from config import settings
from src.models.testcase import TestCase, RequirementUnit
from src.generator.llm_client import BailianClient
from src.generator.mock_generator import mock_generate


class CaseGenerator:
    def __init__(self):
        self.use_mock = settings.is_mock()
        self.client = None if self.use_mock else BailianClient()

    def _load_template(self, kind: str) -> str:
        fname = "api_case.txt" if kind in ("api", "action") else "requirement_case.txt"
        return (settings.PROMPTS_DIR / fname).read_text(encoding="utf-8")

    def _build_prompt(self, unit: RequirementUnit) -> str:
        tpl = self._load_template(unit.kind)
        return tpl.format(
            name=unit.name,
            path=unit.path or "-",
            description=unit.description or "-",
            params="; ".join(unit.params) or "-",
            kind=unit.kind,
            constraints=unit.constraints or "-",
        )

    @staticmethod
    def _normalize(raw: dict) -> dict:
        """兼容 LLM 返回的字段名大小写/中英差异。"""
        ct = raw.get("case_type") or raw.get("caseType") or "正向"
        pr = raw.get("priority") or raw.get("优先级") or "P1"
        return {
            "title": raw.get("title", "未命名用例"),
            "module": raw.get("module", ""),
            "case_type": ct,
            "priority": pr,
            "pre_condition": raw.get("pre_condition", "") or raw.get("preCondition", ""),
            "steps": raw.get("steps", []) or [],
            "expected": raw.get("expected", ""),
            "test_data": raw.get("test_data") or raw.get("testData"),
        }

    @staticmethod
    def _parse_llm(text: str) -> list[TestCase]:
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        try:
            arr = json.loads(m.group(0))
        except Exception:
            return []
        out = []
        for item in arr:
            try:
                out.append(TestCase(**CaseGenerator._normalize(item)))
            except Exception:
                continue
        return out

    def generate_for_unit(self, unit: RequirementUnit) -> list[TestCase]:
        if self.use_mock or self.client is None:
            return mock_generate(unit)
        prompt = self._build_prompt(unit)
        text = self.client.generate(prompt)
        return self._parse_llm(text)

    def generate(self, units: list[RequirementUnit]) -> list[TestCase]:
        all_cases: list[TestCase] = []
        for u in units:
            all_cases.extend(self.generate_for_unit(u))
        # 去重
        seen, dedup = set(), []
        for c in all_cases:
            key = (c.title, c.module, c.case_type.value)
            if key in seen:
                continue
            seen.add(key)
            dedup.append(c)
        for i, c in enumerate(dedup, 1):
            c.case_id = f"TC-{i:03d}"
        return dedup
