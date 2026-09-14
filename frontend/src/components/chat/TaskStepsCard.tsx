/**
 * 任务步骤卡：四 Agent 编排节点实时进度（平移旧版 renderLiveSteps）。
 * 数据源：chatStore 消息上的 task（taskStore 轮询回写）。
 * 方案 B：每个步骤可折叠，默认展开，展开区显示该步思考详情（输入/输出/错误）。
 */
import { useState } from "react";
import type { Task } from "../../types";

const STEP_TITLES = ["解析规格", "AI 生成用例", "质量校验", "导出文件"];
const STEP_ICONS: Record<string, string> = {
  解析规格: "🔍",
  "AI 生成用例": "✨",
  质量校验: "🛡️",
  导出文件: "📦",
};
const RUNNING_HINTS: Record<string, string> = {
  解析规格: "⏳ 正在拆解需求...",
  "AI 生成用例": "✨ 正在生成用例...",
  质量校验: "🛡️ 正在质量校验...",
  导出文件: "📦 正在导出文件...",
};

export function statusBadge(status: string): { text: string; cls: string } {
  if (status === "completed") return { text: "✓ 已完成", cls: "ok" };
  if (status === "running") return { text: "⏳ 生成中", cls: "run" };
  if (status === "failed") return { text: "✗ 失败", cls: "fail" };
  return { text: "排队中", cls: "sub" };
}

interface StepInfo {
  status: string;
  input_summary?: string | null;
  output_summary?: string | null;
  error?: string | null;
}

export default function TaskStepsCard({ task }: { task: Task }) {
  const badge = statusBadge(task.status);
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(STEP_TITLES.map((t) => [t, true]))
  );
  const stepMap: Record<string, StepInfo> = {};
  (task.steps || []).forEach((s) => {
    if (STEP_TITLES.includes(s.title))
      stepMap[s.title] = {
        status: s.status,
        input_summary: s.input_summary,
        output_summary: s.output_summary,
        error: s.error,
      };
  });

  const toggle = (title: string) => setExpanded((prev) => ({ ...prev, [title]: !prev[title] }));

  return (
    <div className="task-steps-card" data-task-card={task.id}>
      <div className="tsc-head">
        <span className="tsc-name">{task.name}</span>
        <span className={`pill pill-${badge.cls}`}>{badge.text}</span>
        {task.status === "completed" && <span className="tsc-cnt">共 {task.cases_count || 0} 个用例</span>}
      </div>
      <div className="tsc-steps">
        {STEP_TITLES.map((title) => {
          const s = stepMap[title];
          const st = s ? s.status : "pending";
          const ring = st === "completed" ? "✓" : st === "failed" ? "!" : STEP_ICONS[title] || "•";
          const open = expanded[title];
          const hasDetail = !!(s?.input_summary || s?.output_summary || s?.error);
          return (
            <div key={title} className={`tsc-step tsc-${st}`}>
              <button type="button" className="tsc-step-head" onClick={() => toggle(title)}>
                <span className="tsc-ring">{ring}</span>
                <span className="tsc-title">{title}</span>
                <span className="tsc-hint">
                  {st === "running" ? RUNNING_HINTS[title] : s?.error ? "失败" : ""}
                </span>
                <span className={`tsc-toggle ${open ? "open" : ""}`}>{open ? "▾" : "▸"}</span>
              </button>
              {open && (
                <div className="tsc-step-detail">
                  {st === "running" && (
                    <div className="tsc-io-text tsc-muted">{RUNNING_HINTS[title]}</div>
                  )}
                  {s?.input_summary && (
                    <div className="tsc-io">
                      <span className="tsc-io-label">📥 输入</span>
                      <div className="tsc-io-text">{s.input_summary}</div>
                    </div>
                  )}
                  {s?.output_summary && (
                    <div className="tsc-io">
                      <span className="tsc-io-label">📤 输出</span>
                      <div className="tsc-io-text">{s.output_summary}</div>
                    </div>
                  )}
                  {s?.error && (
                    <div className="tsc-io tsc-io-err">
                      <span className="tsc-io-label">✗ 错误</span>
                      <div className="tsc-io-text">{s.error}</div>
                    </div>
                  )}
                  {st !== "running" && !hasDetail && (
                    <div className="tsc-io-text tsc-muted">（暂无思考记录）</div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {task.status === "completed" && (
        <div className="tsc-actions">
          <button
            type="button"
            className="qtag confirm-btn"
            onClick={(e) => {
              e.stopPropagation();
              void import("../../store/taskStore").then((m) => m.useTaskStore.getState().openDetail(task.id));
            }}
          >
            查看用例 / 思维导图 →
          </button>
        </div>
      )}
    </div>
  );
}
