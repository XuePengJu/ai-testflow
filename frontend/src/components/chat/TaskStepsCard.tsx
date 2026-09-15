/**
 * 任务步骤卡：四 Agent 编排节点实时进度（平移旧版 renderLiveSteps）。
 * 数据源：chatStore 消息上的 task（taskStore 轮询回写）。
 * 方案 B：每个步骤可折叠，默认展开，展开区显示该步思考详情（输入/输出/错误）。
 */
import { useState } from "react";
import type { Task } from "../../types";
import { useTaskStore } from "../../store/taskStore";
import { isLatestOfChain } from "../../utils/taskChain";

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
  /** running 期间实时子进度（后端 StepLog.progress，轮询回写） */
  progress?: string | null;
  input_summary?: string | null;
  output_summary?: string | null;
  error?: string | null;
}

/** showIterate：会话内任务卡传 true（挂「继续优化」→ 挂 chip）；详情页 running 卡不传（避免重复入口） */
export default function TaskStepsCard({ task, showIterate = false }: { task: Task; showIterate?: boolean }) {
  const tasks = useTaskStore((s) => s.tasks);
  const openDetail = useTaskStore((s) => s.openDetail);
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(STEP_TITLES.map((t) => [t, true]))
  );
  // 会话流里同一条迭代链的旧版本卡折叠成一行细条：入口只留最新一张完整卡，避免翻页找入口。
  // 抽屉内的卡（showIterate=false）不做折叠，那里由版本切换器导航。
  const stale = showIterate && !isLatestOfChain(tasks, task.id);
  // 步骤卡优先用列表轮询里的最新任务：列表 5s 轮询永不停，完成后的 steps 全量都在；
  // 活跃任务轮询（pollTask）有 TTL，到期后卡片不能停在旧 running 状态。
  const latest = tasks.find((t) => t.id === task.id);
  const live = latest && latest.steps && latest.steps.length > 0 ? latest : task;
  const badge = statusBadge(live.status);
  const stepMap: Record<string, StepInfo> = {};
  (live.steps || []).forEach((s) => {
    if (STEP_TITLES.includes(s.title))
      stepMap[s.title] = {
        status: s.status,
        progress: s.progress,
        input_summary: s.input_summary,
        output_summary: s.output_summary,
        error: s.error,
      };
  });

  const toggle = (title: string) => setExpanded((prev) => ({ ...prev, [title]: !prev[title] }));

  // 旧版本折叠态：一行细条，点击直接开抽屉看该版本（抽屉内可切回最新版）
  if (stale) {
    return (
      <div className="task-card-stale" data-task-card={task.id}>
        <span className="tcs-icon" aria-hidden="true">📜</span>
        <span className="tcs-name">{task.name}</span>
        <span className="tcs-meta">
          {task.status === "completed" ? `${task.cases_count || 0} 条用例 · ` : ""}已被新版本取代
        </span>
        <button
          type="button"
          className="tcs-open"
          title="在详情里查看该版本（可切换其他版本）"
          onClick={(e) => {
            e.stopPropagation();
            void openDetail(task.id);
          }}
        >
          查看 ▸
        </button>
      </div>
    );
  }

  return (
    <div className="task-steps-card" data-task-card={task.id}>
      <div className="tsc-head">
        <span className="tsc-name">{live.name}</span>
        <span className={`pill pill-${badge.cls}`}>{badge.text}</span>
        {live.status === "completed" && <span className="tsc-cnt">共 {live.cases_count || 0} 个用例</span>}
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
                  {st === "running"
                    ? (s?.progress || RUNNING_HINTS[title])
                    : s?.error ? "失败" : ""}
                </span>
                <span className={`tsc-toggle ${open ? "open" : ""}`}>{open ? "▾" : "▸"}</span>
              </button>
              {open && (
                <div className="tsc-step-detail">
                  {st === "running" && (
                    <div className="tsc-io-text tsc-muted">{s?.progress || RUNNING_HINTS[title]}</div>
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
      {live.status === "completed" && (
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
          {/* 会话内闭环：直接挂载迭代引用 chip，用户无需先开详情抽屉 */}
          {showIterate && (
            <button
              type="button"
              className="qtag"
              title="基于该任务继续补充用例（在输入框中说明补充内容后发送）"
              onClick={(e) => {
                e.stopPropagation();
                void import("../../store/chatStore").then((m) => {
                  void m.useChatStore.getState().openIterate(task);
                });
              }}
            >
              💬 继续优化
            </button>
          )}
        </div>
      )}
    </div>
  );
}
