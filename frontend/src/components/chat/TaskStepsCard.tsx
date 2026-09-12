/**
 * 任务步骤卡：四 Agent 编排节点实时进度（平移旧版 renderLiveSteps）。
 * 数据源：chatStore 消息上的 task（taskStore 轮询回写）。
 */
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

export default function TaskStepsCard({ task }: { task: Task }) {
  const badge = statusBadge(task.status);
  const stepMap: Record<string, { status: string; error?: string | null }> = {};
  (task.steps || []).forEach((s) => {
    if (STEP_TITLES.includes(s.title)) stepMap[s.title] = { status: s.status, error: s.error };
  });

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
          return (
            <div key={title} className={`tsc-step tsc-${st}`}>
              <span className="tsc-ring">{ring}</span>
              <span className="tsc-title">{title}</span>
              <span className="tsc-hint">{st === "running" ? RUNNING_HINTS[title] : s?.error ? "失败" : ""}</span>
            </div>
          );
        })}
      </div>
      {task.steps?.some((s) => s.error) && (
        <div className="tsc-error">{task.steps.find((s) => s.error)?.error?.slice(0, 200)}</div>
      )}
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
