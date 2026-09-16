"""生成核心编排：解析单元 →（mock 或 真实模型，支持多角色）→ 合并去重 → 编号。

V3.1 多角色协作（轻量版）：每个测试点可按角色（产品 pm / 测试 qa / 开发 dev）
分别以各自视角生成用例，再合并去重。默认仅测试（qa），行为与旧版完全一致。
"""
import json
import re
from config import settings
from src.models.testcase import TestCase, RequirementUnit, align_step_expectations
from src.generator.llm_client import BailianClient
from src.generator.mock_generator import mock_generate

# 角色标识 → 展示名（进度回调/汇总用）
ROLES_ORDER = ["pm", "qa", "dev"]
ROLE_LABELS = {"pm": "产品", "qa": "测试", "dev": "开发"}

# 角色 → 模板后缀（qa 用现有模板，无后缀）
_ROLE_TPL_SUFFIX = {"pm": "_pm", "dev": "_dev", "qa": ""}


def parse_roles(raw) -> list[str]:
    """把任务上的 roles 配置解析为合法角色列表（未知角色忽略，空则默认 qa）。"""
    if isinstance(raw, str):
        raw = raw.replace("，", ",")
        items = [s.strip().lower() for s in raw.split(",") if s.strip()]
    elif isinstance(raw, (list, tuple)):
        items = [str(s).strip().lower() for s in raw if str(s).strip()]
    else:
        items = []
    roles = [r for r in ROLES_ORDER if r in items]
    return roles or ["qa"]


class CaseGenerator:
    def __init__(self, client=None, roles=None):
        """client：平台注入的 LLM 客户端（OpenAI 兼容）。
        注入时优先使用；否则回退旧逻辑（dashscope SDK / mock）。
        roles：参与生成的角色列表（pm/qa/dev），默认 ["qa"]。"""
        self.injected = client
        self.use_mock = settings.is_mock()
        self.client = None if self.use_mock else BailianClient()
        self.roles = parse_roles(roles)

    def _load_template(self, kind: str, role: str = "qa") -> str:
        suffix = _ROLE_TPL_SUFFIX.get(role, "")
        fname = f"api_case{suffix}.txt" if kind in ("api", "action") else f"requirement_case{suffix}.txt"
        return (settings.PROMPTS_DIR / fname).read_text(encoding="utf-8")

    def _build_prompt(self, unit: RequirementUnit, role: str = "qa") -> str:
        tpl = self._load_template(unit.kind, role)
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
        """兼容 LLM 返回的字段名大小写/中英差异，并对齐逐步预期。"""
        ct = raw.get("case_type") or raw.get("caseType") or "正向"
        pr = raw.get("priority") or raw.get("优先级") or "P1"
        steps = raw.get("steps", []) or []
        expected = raw.get("expected", "")
        se = raw.get("step_expectations") or raw.get("stepExpectations") or []
        return {
            "title": raw.get("title", "未命名用例"),
            "module": raw.get("module", ""),
            "case_type": ct,
            "priority": pr,
            "pre_condition": raw.get("pre_condition", "") or raw.get("preCondition", ""),
            "steps": steps,
            "step_expectations": align_step_expectations(steps, se, expected),
            "expected": expected,
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

    def generate_for_unit(self, unit: RequirementUnit, role: str = "qa") -> list[TestCase]:
        try:
            return self._generate_inner(unit, role)
        except Exception:  # noqa: BLE001
            # 单点失败回退 mock
            return mock_generate(unit)

    def _generate_inner(self, unit: RequirementUnit, role: str = "qa") -> list[TestCase]:
        if self.injected is not None:
            # 平台注入的真实模型：OpenAI 兼容调用
            prompt = self._build_prompt(unit, role)
            text = self.injected.generate(prompt)
            return self._parse_llm(text)
        if self.use_mock or self.client is None:
            return mock_generate(unit)
        prompt = self._build_prompt(unit, role)
        text = self.client.generate(prompt)
        return self._parse_llm(text)

    def generate(self, units: list[RequirementUnit], progress_cb=None, roles=None) -> list[TestCase]:
        """为全部测试点按角色生成用例（unit × role 双层循环）。

        progress_cb(cur, total, unit_name, cases_so_far)：每个「测试点×角色」完成后
        回调一次，供上层做实时子进度展示（默认 None 不影响旧调用）。
        """
        role_list = parse_roles(roles) if roles is not None else self.roles
        all_cases: list[TestCase] = []
        total = len(units) * len(role_list)
        cur = 0
        for role in role_list:
            role_label = ROLE_LABELS.get(role, role)
            for u in units:
                cur += 1
                all_cases.extend(self.generate_for_unit(u, role))
                if progress_cb:
                    try:
                        progress_cb(
                            cur, total,
                            f"{u.name} · {role_label}视角",
                            len(all_cases),
                        )
                    except Exception:  # noqa: BLE001  进度回调失败绝不能影响生成
                        pass
        # 去重（跨角色输出合并后，按标题/模块/类型去重）
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
