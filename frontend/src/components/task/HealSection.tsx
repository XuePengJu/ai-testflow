/**
 * M4 自愈过程展示（ExecutionPanel 报告视图内挂载）：heal 存在（触发过自愈循环）才渲染。
 *
 * - 「自愈过程」折叠区：heal.log 每轮一张卡（第 N 轮 / 根因诊断 / 修复文件 / 重跑结果 / 涉及失败用例）
 * - 「疑似缺陷」独立高亮区块（橙色警示）：suspected_bugs 每条含类别/诊断说明/涉及用例，一键复制文本
 * - 数据结构与 app/services/auto_runner.py:_run_heal_flow 写入 report_json.heal 的结构对齐（见 types.ts HealMeta）
 */
import { useState } from "react";
import type { CSSProperties } from "react";
import type { HealLogEntry, HealMeta } from "../../types";

/** 根因四分类 → 中文标签（与 auto_healer._VALID_ROOT_CAUSES 对齐） */
const ROOT_CAUSE_LABELS: Record<string, string> = {
  selector: "选择器失效",
  timing: "等待不足",
  env: "环境/数据问题",
  product_bug: "疑似产品缺陷",
};

/** rerun_outcome → 展示文案（rejected_* 等原始值原样透出，便于排查） */
function outcomeText(outcome: string): { text: string; color: string } {
  if (outcome === "passed") return { text: "重跑全部通过", color: "#00b42a" };
  if (outcome === "failed") return { text: "重跑仍有失败", color: "#d83931" };
  if (outcome === "skipped") return { text: "跳过修复（不可修复类根因）", color: "#d25f00" };
  if (outcome === "error") return { text: "重跑执行出错", color: "#d83931" };
  if (outcome === "diagnosis_failed") return { text: "诊断调用失败", color: "#d83931" };
  if (outcome.startsWith("rejected_syntax_error")) return { text: "修复被拒（语法校验未过）", color: "#d25f00" };
  if (outcome === "rejected_assert_change") return { text: "修复被拒（试图修改断言）", color: "#d25f00" };
  return { text: outcome || "—", color: "#4e5969" };
}

/** 疑似缺陷类别 → 中文标签 */
const BUG_KIND_LABELS: Record<string, string> = {
  product_bug: "疑似产品缺陷",
  env: "环境/数据问题",
  exhausted: "自愈轮次耗尽",
};

/** 橙色警示区块样式（suspected_bugs 独立高亮用） */
const warnBoxStyle: CSSProperties = {
  border: "1px solid #ffd6a0",
  background: "#fff7e8",
  borderRadius: 8,
  padding: "10px 12px",
  marginBottom: 10,
};

/** 单轮自愈卡片 */
function HealRoundCard({ entry }: { entry: HealLogEntry }) {
  const outcome = outcomeText(entry.rerun_outcome);
  const cause = entry.diagnosis?.root_cause || "";
  const suspects = Array.isArray(entry.suspects) ? entry.suspects : [];
  const changed = Array.isArray(entry.changed_files) ? entry.changed_files : [];
  return (
    <div style={{ border: "1px solid #f0f1f3", borderRadius: 8, padding: "8px 12px", marginBottom: 8, background: "#fff" }}>
      {/* 卡头：轮次 + 根因徽标 + 重跑结果 */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 4 }}>
        <span style={{ fontWeight: 600, fontSize: 12.5, color: "#1f2329" }}>第 {entry.round} 轮</span>
        {cause && (
          <span className="pill pill-sub">{ROOT_CAUSE_LABELS[cause] || cause}</span>
        )}
        <span style={{ fontSize: 12, color: outcome.color }}>{outcome.text}</span>
      </div>
      {/* 诊断结论 */}
      {entry.diagnosis?.analysis && (
        <div style={{ fontSize: 12.5, color: "#4e5969", marginBottom: 4 }}>
          诊断结论：{entry.diagnosis.analysis}
        </div>
      )}
      {/* 修复文件 */}
      {changed.length > 0 ? (
        <div style={{ fontSize: 12.5, color: "#4e5969", marginBottom: 4 }}>
          修复文件：{changed.map((f) => (
            <span key={f} className="pill pill-sub" style={{ marginRight: 6 }}>{f}</span>
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 12.5, color: "#8f959e", marginBottom: 4 }}>本轮未修改脚本文件</div>
      )}
      {/* 涉及失败用例 */}
      {suspects.length > 0 && (
        <div style={{ fontSize: 12, color: "#4e5969" }}>
          涉及用例：{suspects.map((s, i) => (
            <span key={`${s.case_id}-${i}`} title={s.error || undefined} style={{ color: "#d83931", marginRight: 10 }}>
              {s.case_id || "—"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** 自愈过程区块：折叠面板（默认展开有疑似缺陷时）+ 疑似缺陷高亮区；heal 不存在不渲染 */
export default function HealSection({ heal }: { heal: HealMeta }) {
  const log = Array.isArray(heal.log) ? heal.log : [];
  const bugs = Array.isArray(heal.suspected_bugs) ? heal.suspected_bugs : [];
  const [open, setOpen] = useState(true);
  const [copied, setCopied] = useState(false);

  // 一键复制：疑似缺陷清单 → 纯文本（用例 / 类别 / 说明逐行）
  function copyBugs(): void {
    const text = bugs.length === 0
      ? "无疑似缺陷记录"
      : bugs.map((b, i) =>
          `${i + 1}. [${BUG_KIND_LABELS[b.kind] || b.kind}] ${b.case_id}\n   ${b.reason}`,
        ).join("\n");
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  }

  if (log.length === 0 && bugs.length === 0) return null;

  return (
    <div style={{ marginTop: 10 }}>
      {/* 疑似缺陷高亮区块（自愈无法修复收敛出的真缺陷/环境问题） */}
      {bugs.length > 0 && (
        <div style={warnBoxStyle}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 6 }}>
            <span style={{ fontWeight: 600, fontSize: 12.5, color: "#d25f00" }}>
              ⚠ 疑似缺陷（{bugs.length} 条，自愈无法自动修复，请人工确认）
            </span>
            <button
              type="button"
              className="btn-ghost"
              style={{ height: 24, padding: "0 10px", fontSize: 12 }}
              title="复制疑似缺陷清单为纯文本"
              onClick={copyBugs}
            >
              {copied ? "✓ 已复制" : "复制清单"}
            </button>
          </div>
          {bugs.map((b, i) => (
            <div key={`${b.case_id}-${i}`} style={{ fontSize: 12.5, color: "#4e5969", padding: "3px 0" }}>
              <span className="pill" style={{ background: "#ffe4ba", color: "#d25f00", marginRight: 8 }}>
                {BUG_KIND_LABELS[b.kind] || b.kind}
              </span>
              <span style={{ color: "#1f2329", fontWeight: 600, marginRight: 8 }}>{b.case_id || "—"}</span>
              <span>{b.reason}</span>
            </div>
          ))}
        </div>
      )}

      {/* 自愈过程折叠区 */}
      {log.length > 0 && (
        <div style={{ border: "1px solid #f0f1f3", borderRadius: 8, padding: "8px 12px", background: "#fafbfc" }}>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0, fontWeight: 600, fontSize: 12.5, color: "#1f2329" }}
            title="展开/收起每轮自愈明细"
          >
            {open ? "▾" : "▸"} 自愈过程（共 {heal.rounds || log.length} 轮）
          </button>
          {open && (
            <div style={{ marginTop: 8 }}>
              {log.map((entry, i) => (
                <HealRoundCard key={entry.round ?? i} entry={entry} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
