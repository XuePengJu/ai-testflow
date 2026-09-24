"""ScripterAgent：把测试用例转写为可执行的 pytest + Playwright(Python) 脚本（M2 实现）。

设计要点（契约 docs/contract-m2-execution.md 1/5 + V5.0 文档 3.3.2）：
- 按 module 分批（每批 ≤8 条）→ 每批一次 LLM 调用产出一个 test_cases_{n}.py
- LLM 只产出测试函数体：文件头（docstring / import / BASE_URL）由代码统一拼装，
  保证 import 一致性与 ast 校验对象完整
- ast.parse 语法校验失败自动重试 1 次（重试 prompt 附语法错误信息）；仍失败该批记
  error 但不中断整链（失败批不落盘，避免污染 pytest 收集）
- conftest.py / mapping.json 由代码模板生成，不经 LLM

接入点兼容：engine.py 以 `run_scripter(pages_md, cases, out_dir)` 三参调用（M1 接入
形态），本实现沿用该签名并追加可选参数（client / progress_cb），不破坏现有流水线。
"""
import ast
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("workflow.agents.scripter")

# 每批最多用例数（契约 1：按 module 分批 ≤8 条/批）
BATCH_SIZE = 8

# prompt 模板（app/workflow/agents/templates/scripter_case.txt，风格对齐 generator prompts）
_PROMPT_PATH = Path(__file__).parent / "templates" / "scripter_case.txt"

# markdown 代码块围栏剥离（模型偶发输出 ```python ... ```）
_FENCE_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.S)

# 文件头模板：LLM 不写 import，由代码统一拼装（契约 1：文件顶部不写 import playwright）
_FILE_HEADER = '''"""AI 生成 Playwright 测试脚本（模块：{module}）。由 scripter_agent 自动产出，勿手工修改。"""
import os
import pytest
from playwright.sync_api import expect

BASE_URL = os.environ.get("AITF_BASE_URL", "http://localhost:3000")
'''

# conftest.py 模板（契约 1：模板固化在代码里，不由 LLM 生成）
_CONFTEST_TEMPLATE = '''"""AITF 自动生成：pytest 全局配置（勿手工修改）。

- base_url：测试代码从环境变量 AITF_BASE_URL 读取被测系统地址
- 登录态：AITF_STORAGE_STATE 指向 storage_state.json 存在时，注入 browser context
- 失败截图：用例失败自动写入 AITF_SHOTS_DIR（文件名 = nodeid 安全化）
"""
import os
import re

import pytest

STORAGE_STATE = os.environ.get("AITF_STORAGE_STATE", "")
SHOTS_DIR = os.environ.get("AITF_SHOTS_DIR", "shots")


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """AITF_STORAGE_STATE 存在时注入登录态（pytest-playwright 官方扩展点）。"""
    if STORAGE_STATE and os.path.exists(STORAGE_STATE):
        return {**browser_context_args, "storage_state": STORAGE_STATE}
    return browser_context_args


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    """用例失败自动截图到 AITF_SHOTS_DIR，文件名为 nodeid 安全化后的字符串。"""
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed:
        page = item.funcargs.get("page")
        if page is None:
            return
        try:
            os.makedirs(SHOTS_DIR, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.nodeid)[-150:]
            page.screenshot(path=os.path.join(SHOTS_DIR, f"{name}.png"), full_page=True)
        except Exception:  # noqa: BLE001  截图失败不影响测试结果
            pass
'''


# ============ 用例归一化 ============

def _get(case, key: str, default=""):
    """兼容 TestCase 对象与 dict 的字段读取。"""
    if isinstance(case, dict):
        return case.get(key, default)
    return getattr(case, key, default)


def _normalize_cases(cases: list) -> list[dict]:
    """用例数组 → 统一 dict 列表；缺失 case_id 时按 TC-xxx 序号补齐（与 ensure_case_ids 规则一致）。"""
    out: list[dict] = []
    max_n = 0
    # 先扫一遍已有最大序号，避免与引擎末尾的 ensure_case_ids 结果冲突
    for c in cases:
        m = re.match(r"^TC-?0*(\d+)$", str(_get(c, "case_id") or "").strip())
        if m:
            max_n = max(max_n, int(m.group(1)))
    for c in cases:
        cid = str(_get(c, "case_id") or "").strip()
        if not cid:
            max_n += 1
            cid = f"TC-{max_n:03d}"
        steps = [str(s).strip() for s in (_get(c, "steps") or []) if str(s).strip()]
        expects = [str(s).strip() for s in (_get(c, "step_expectations") or []) if str(s).strip()]
        expected = str(_get(c, "expected") or "").strip()
        # 逐步预期缺失时兜底：单步挂整体预期，多步每步挂整体预期（冗余但信息完整）
        if not expects and steps:
            expects = [expected] if len(steps) == 1 else [expected] * len(steps)
        out.append({
            "case_id": cid,
            "title": str(_get(c, "title") or "").strip(),
            "module": str(_get(c, "module") or "").strip() or "通用",
            "case_type": str(_get(c, "case_type") or "").strip(),
            "priority": str(_get(c, "priority") or "").strip(),
            "pre_condition": str(_get(c, "pre_condition") or "").strip(),
            "steps": steps,
            "step_expectations": expects,
            "expected": expected,
            "test_data": str(_get(c, "test_data") or "").strip(),
        })
    return out


def _case_batches(cases: list[dict]) -> list[tuple[str, list[dict]]]:
    """按 module 保序分组，每组内切 BATCH_SIZE → [(module, 批用例), ...]。"""
    groups: dict[str, list[dict]] = {}
    for c in cases:
        groups.setdefault(c["module"], []).append(c)
    batches: list[tuple[str, list[dict]]] = []
    for module, group in groups.items():
        for i in range(0, len(group), BATCH_SIZE):
            batches.append((module, group[i:i + BATCH_SIZE]))
    return batches


# ============ LLM 调用与代码拼装 ============

def _resolve_llm_client() -> tuple[object | None, str]:
    """无外部注入 client 时自解析（引擎未向 scripter 传 client）。

    解析链：llm_service.resolve_effective（用户/平台默认/百炼 env 兜底）。
    失败返回 (None, "")，由调用方按「模型不可用」记 error，不中断整链。
    """
    try:
        from app.core.db import SessionLocal
        from app.services import llm_service
        db = SessionLocal()
        try:
            eff = llm_service.resolve_effective(db, None)
        finally:
            db.close()
        cfg = eff.get("text")
        if cfg and cfg.get("api_key"):
            client = llm_service.OpenAICompatClient(cfg["base_url"], cfg["api_key"], cfg["model"])
            return client, str(cfg.get("model") or "")
    except Exception as e:  # noqa: BLE001  LLM 解析失败不阻断，走 error 批
        logger.warning("scripter 解析 LLM 配置失败：%s", e)
    return None, ""


def _strip_fences(raw: str) -> str:
    """剥掉模型偶发输出的 markdown 围栏与首尾空白。"""
    text = (raw or "").strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    return text


def _assemble_file(module: str, body: str) -> str:
    """文件头（代码生成）+ 测试函数体（LLM 产出）→ 完整测试文件。"""
    header = _FILE_HEADER.format(module=module)
    return header + "\n\n" + body.strip() + "\n"


def _gen_batch_code(client, module: str, batch: list[dict], pages_md: str, prev_error: str = "") -> str:
    """一次 LLM 调用产出一批测试函数体。prev_error 非空 = 重试（附语法错误信息）。"""
    prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    cases_json = json.dumps(batch, ensure_ascii=False, indent=1)
    prompt = (prompt
              .replace("{pages_md}", (pages_md or "（无页面结构摘要，按用例语义选择定位器）").strip()[:6000])
              .replace("{module}", module)
              .replace("{count}", str(len(batch)))
              .replace("{cases_json}", cases_json))
    if prev_error:
        prompt += (
            f"\n\n【上一次输出未通过 Python 语法校验，请修正后重新输出完整代码】\n"
            f"语法错误：{prev_error}\n"
            f"注意：只输出从 def test_ 开始的纯 Python 函数代码，不要围栏、不要解释。"
        )
    return _strip_fences(_call_llm(client, prompt))


def _call_llm(client, prompt: str) -> str:
    """统一 LLM 调用：优先 chat() 拉大输出预算，不可用则回退 generate()。

    thinking 模型的思考过程会占用输出 token 预算，默认 8192 常被思考耗尽
    导致正文 content 为空（实测 Qwen3 系列），故显式给到 32768。
    """
    messages = [{"role": "user", "content": prompt}]
    if hasattr(client, "chat"):
        try:
            return client.chat(messages, temperature=0.2, max_tokens=32768)
        except Exception:  # noqa: BLE001  大预算被端点拒绝等情况 → 默认参数重试
            return client.chat(messages)
    return client.generate(prompt)


# ============ 主入口 ============

def run_scripter(pages_md: str, cases: list, out_dir: str, client=None, progress_cb=None):
    """把结构化用例转写为 pytest + Playwright 脚本，写入 {out_dir}/auto/。

    入参：
        pages_md   ：被测系统页面结构摘要文本（crawler 步骤产物，辅助定位器选择）
        cases      ：用例数组（TestCase 对象或 dict，字段含 title/module/steps/expected 等）
        out_dir    ：任务输出目录（脚本落盘在其下 auto/ 子目录，契约 1）
        client     ：可选 LLM 客户端（需有 .generate(prompt)）；缺省时自解析
        progress_cb：可选回调 (done_batches, total_batches, module)，用于 StepLog 进度

    出参：(auto_dir, summary, details_json)
    """
    auto_dir = Path(out_dir) / "auto"
    auto_dir.mkdir(parents=True, exist_ok=True)

    norm = _normalize_cases(cases or [])
    batches = _case_batches(norm)
    total = len(norm)

    if total == 0:
        summary = "0 个用例 → 0 个脚本文件（无用例可生成）"
        return str(auto_dir), summary, json.dumps(
            {"auto_dir": str(auto_dir), "cases_count": 0, "files": [], "errors": []},
            ensure_ascii=False)

    if client is None:
        client, model_desc = _resolve_llm_client()
    else:
        model_desc = "外部注入客户端"
    if client is None:
        # 模型不可用：不产出任何脚本，整步记 failed（由引擎捕获），避免生成空壳产物
        msg = "未解析到可用 LLM 配置，无法生成自动化脚本（请到【模型设置】配置模型）"
        logger.error(msg)
        raise RuntimeError(msg)

    # conftest.py 固定产出（幂等覆盖）
    (auto_dir / "conftest.py").write_text(_CONFTEST_TEMPLATE, encoding="utf-8")

    mapping: dict[str, str] = {}
    files_meta: list[dict] = []
    errors: list[dict] = []
    done_cases = 0

    for idx, (module, batch) in enumerate(batches):
        if progress_cb:
            try:
                progress_cb(idx, len(batches), module)
            except Exception:  # noqa: BLE001  进度回调失败不影响主流程
                pass

        code, syntax_err = "", ""
        for attempt in (1, 2):  # 首次 + 失败重试 1 次
            try:
                code = _gen_batch_code(client, module, batch, pages_md, prev_error=syntax_err)
            except Exception as e:  # noqa: BLE001  LLM 调用异常：重试一次后放弃
                syntax_err = f"LLM 调用异常：{e}"
                code = ""
            if not code.strip():
                # 空产出（模型剥掉 think 后可能为空）：视为失败，与语法错误同等对待
                syntax_err = syntax_err or "LLM 返回空内容"
            else:
                file_src = _assemble_file(module, code)
                try:
                    ast.parse(file_src)
                    syntax_err = ""           # 校验通过
                    break
                except SyntaxError as e:
                    syntax_err = f"line {e.lineno}: {e.msg}"
                    code = ""                 # 校验失败清空，不落盘坏文件
        if not code:
            # 重试后仍失败：该批记 error 但不中断整链（契约 5）
            errors.append({
                "module": module,
                "case_ids": [c["case_id"] for c in batch],
                "error": syntax_err or "未知错误",
            })
            logger.error("脚本生成失败（module=%s，%d 条用例）：%s", module, len(batch), syntax_err)
            continue

        file_name = f"test_cases_{len(files_meta)}.py"
        (auto_dir / file_name).write_text(_assemble_file(module, code), encoding="utf-8")
        file_cases = []
        for c in batch:
            func_name = "test_" + c["case_id"].lower().replace("-", "_")
            mapping[func_name] = c["case_id"]
            file_cases.append(c["case_id"])
        files_meta.append({"file": file_name, "module": module, "case_ids": file_cases})
        done_cases += len(batch)

    # mapping.json：{函数名 → 用例ID}（契约 1）
    (auto_dir / "mapping.json").write_text(
        json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")

    ok_files = len(files_meta)
    summary = f"AI 生成 Playwright 脚本：{total} 个用例 → {ok_files} 个脚本文件"
    if errors:
        lost = sum(len(e["case_ids"]) for e in errors)
        summary += f"（{lost} 条生成失败，详见步骤详情）"
    details = {
        "auto_dir": str(auto_dir),
        "cases_count": total,
        "generated_cases": done_cases,
        "files": files_meta,
        "mapping_count": len(mapping),
        "model": model_desc,
        "errors": errors,
    }
    return str(auto_dir), summary, json.dumps(details, ensure_ascii=False)
