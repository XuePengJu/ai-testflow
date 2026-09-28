"""分层决策中间层（阶段 1/2）：小模型复刻 Jev 风格的封闭式快速判断。

设计依据：docs/项目1-探索Agent分层决策改造-阶段0基线-V1.0.md
- 基线事实：探索每步的决策 LLM 调用占总耗时 79%~82%；决策输出 p50 仅 95~117
  字符 → 90% 以上的步骤本质是「从有限候选里选一个」的封闭判断，不需要生成能力。
- 本层做法：每步先用「短 prompt + 小模型」做快速判断（动作 + 目标 ref + 置信度），
  高置信直接执行；低置信、解析失败或需要生成（填表内容 / 用例正文）→ 升级主 LLM。
- 与 Jev 的差距（诚实说明）：Jev 是模型层多判断头并行、概率经校准；本层用
  「一次 chat 输出多字段 JSON」做工程近似，置信度校准性依赖业务数据，需阶段 3
  影子运行校准阈值。收益主要来自 prompt/输出大幅缩短与震荡步消除。

开关：EXPLORE_LAYERED=1 开启（explorer_agent 主循环读取），默认关闭保持原行为。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("services.decision_provider")

# 快速判断允许直接执行的动作（纯判断类，无需生成任何文本内容）
FAST_ACTIONS = ("browser_click", "browser_navigate", "browser_back",
                "browser_snapshot", "browser_screenshot")

# 置信度阈值：>= HIGH 直接执行，否则升级主 LLM（阶段 3 用影子数据校准）
CONFIDENCE_HIGH = 0.7

# 快判输出的短动作名 → 主循环完整工具名（prompt 用短名省 token）
_ACTION_MAP = {
    "click": "browser_click",
    "navigate": "browser_navigate",
    "back": "browser_back",
    "snapshot": "browser_snapshot",
    "screenshot": "browser_screenshot",
}


def llm_client_meta(llm_client: Any) -> str:
    """提取 LLM 客户端的 provider / model 标识（用于调用日志，不含 prompt 内容）。

    客户端实现各异（LangChainClient / httpx 直连 / 测试 fake），属性取不到时
    逐级兜底：model_name → model → unknown；provider 无则用类名。
    """
    model = getattr(llm_client, "model_name", None) or getattr(llm_client, "model", None)
    if not isinstance(model, str) or not model:
        model = "unknown"
    provider = getattr(llm_client, "provider", None) or llm_client.__class__.__name__
    return f"provider={provider} model={model}"


@dataclass
class FastDecision:
    """FastDecider 的判断结果。action 为 escalate 时表示交给主 LLM。"""
    action: str = "escalate"
    args: dict = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.0
    next: str = "main"          # 下一步路由预测：fast（纯浏览）/ main（需生成）


_SYSTEM_PROMPT = """你是浏览器探索决策器。站点域：{host}。探索目标：{goal}
每步从下方元素表选一个动作，只输出一个 JSON（不要 markdown 围栏）：
{{"action": "...", "ref": "...", "url": "...", "reason": "一句话", "confidence": 0.85, "next": "fast"}}

action 只能取：click、navigate、back、snapshot、screenshot、escalate。
- click：ref 必填，必须来自当前元素表
- navigate：url 必填，仅限 {host} 域内
- back：浏览器后退（当前页无价值时用）
- snapshot：重新获取元素表
- screenshot：截图存档
- escalate：需要往输入框填写内容、需要编写测试用例、或你不确定时选它（上层模型接管）

next：预测执行完本步后的下一个动作类型——仍是点击/导航/后退等浏览类输出 "fast"；
将需要填表、写用例或复杂思考输出 "main"。

规则：
- 不要重复做过的事情；同一动作失败过就不要再试
- 元素表里没有值得交互的元素时，用 navigate 换页面或 back
- confidence 是 0~1 的把握度：完全确定才给 0.9 以上"""


def _build_fast_messages(goal: str, host: str, history: list[str], obs: str,
                         step_no: int, max_steps: int) -> list[dict]:
    """短上下文：最近 6 条历史 + 全量元素表（快照成本 0.1%，不值得裁剪）。"""
    recent = history[-6:]
    hist_text = "\n".join(recent) if recent else "（暂无历史动作）"
    user = (f"【最近动作】\n{hist_text}\n\n【当前元素表】\n{obs}\n\n"
            f"【进度】第 {step_no}/{max_steps} 步。输出决策 JSON。")
    return [
        {"role": "system", "content": _SYSTEM_PROMPT.format(host=host, goal=goal)},
        {"role": "user", "content": user[:8000]},
    ]


def _parse_fast(raw: str) -> FastDecision:
    """解析快速判断输出；格式非法 → escalate（升级而不是报错）。

    注意：不复用 explorer_agent.parse_decision——那是主协议（tool 字段），
    本层协议是 action 字段，校验规则不同。
    """
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return FastDecision(reason="快速判断输出不可解析")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return FastDecision(reason="快速判断输出不可解析")
    if not isinstance(obj, dict):
        return FastDecision(reason="快速判断输出不可解析")

    action = str(obj.get("action") or "").strip().lower()
    reason = str(obj.get("reason") or "").strip()
    try:
        confidence = max(0.0, min(1.0, float(obj.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0

    action = _ACTION_MAP.get(action, action)   # 短名归一化（click → browser_click）
    if action == "escalate" or action not in FAST_ACTIONS:
        return FastDecision(reason=reason or "需要生成能力或动作超范围", confidence=confidence)

    nxt = str(obj.get("next") or "main").strip().lower()
    nxt = nxt if nxt in ("fast", "main") else "main"

    args: dict = {}
    if action == "browser_click":
        ref = str(obj.get("ref") or "").strip()
        if not ref:
            return FastDecision(reason="click 缺 ref", confidence=confidence)
        args = {"ref": ref}
    elif action == "browser_navigate":
        url = str(obj.get("url") or "").strip()
        if not url:
            return FastDecision(reason="navigate 缺 url", confidence=confidence)
        args = {"url": url}

    return FastDecision(action=action, args=args, reason=reason,
                        confidence=confidence, next=nxt)


class FastDecider:
    """快速判断器：包装任意有 .chat() 的客户端，输出封闭式决策。"""

    def __init__(self, llm_client: Any):
        self._llm = llm_client

    def decide(self, goal: str, host: str, history: list[str], obs: str,
               step_no: int, max_steps: int) -> tuple[FastDecision, dict]:
        """返回 (决策, 计量)。决策不可用/需升级时 action=escalate。

        计量：{"ms": 耗时, "in": 输入字符, "out": 输出字符}
        """
        import time

        messages = _build_fast_messages(goal, host, history, obs,
                                        step_no, max_steps)
        meta = llm_client_meta(self._llm)
        t0 = time.monotonic()
        try:
            # enable_thinking=False：判断类调用必须关思考链，否则 reasoning 生成
            # 耗时全部落在 RTT 里（实测快判 avg 从 2.4s 级别降不下来的主因）
            raw = self._llm.chat(messages, temperature=0.1, max_tokens=256,
                                 enable_thinking=False)
        except Exception as e:  # noqa: BLE001  快速判断失败不致命 → 升级主 LLM
            ms = int((time.monotonic() - t0) * 1000)
            logger.info("LLM 快判调用失败 %s ms=%d err=%s", meta, ms, str(e)[:120])
            logger.warning("快速判断调用失败：%s", e)
            return (FastDecision(reason=f"快速判断异常：{str(e)[:80]}"),
                    {"ms": ms,
                     "in": sum(len(str(m["content"])) for m in messages), "out": 0})
        ms = (time.monotonic() - t0) * 1000
        decision = _parse_fast(raw)
        # 调用观测日志：provider / model / 耗时 / 结果（不记 prompt 全文）
        logger.info("LLM 快判调用成功 %s ms=%d action=%s confidence=%.2f",
                    meta, int(ms), decision.action, decision.confidence)
        return decision, {"ms": int(ms),
                          "in": sum(len(str(m["content"])) for m in messages),
                          "out": len(raw or "")}
