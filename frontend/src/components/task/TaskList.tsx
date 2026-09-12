/**
 * 任务列表侧栏：5s 轮询刷新，状态徽章。
 * - 点击任务项 → 打开详情抽屉（M3）
 * - 「⌖」定位按钮 → 滚动到聊天内任务卡（M2 行为保留）
 */
import { useEffect } from "react";
import { startListPolling, useTaskStore } from "../../store/taskStore";
import { useChatStore } from "../../store/chatStore";
import { statusBadge } from "../chat/TaskStepsCard";
import { useAuth } from "../../hooks/useAuth";

function fmtTime(s?: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function TaskList() {
  const tasks = useTaskStore((s) => s.tasks);
  const listLoaded = useTaskStore((s) => s.listLoaded);
  const deleteTask = useTaskStore((s) => s.deleteTask);
  const openDetail = useTaskStore((s) => s.openDetail);
  const focusTask = useChatStore((s) => s.focusTask);
  const { token } = useAuth();

  // 登录期间 5s 轮询；登出/未登录时停
  useEffect(() => {
    if (!token) return;
    const stop = startListPolling();
    return stop;
  }, [token]);

  if (!token) {
    return (
      <aside className="side-panel task-panel">
        <div className="side-head"><span>我的任务</span></div>
        <div className="hist-empty">登录或游客体验后查看任务</div>
      </aside>
    );
  }

  return (
    <aside className="side-panel task-panel">
      <div className="side-head">
        <span>我的任务{tasks.length ? `（${tasks.length}）` : ""}</span>
      </div>
      <div className="hist-list">
        {!listLoaded && tasks.length === 0 ? (
          <div className="hist-empty">加载中…</div>
        ) : tasks.length === 0 ? (
          <div className="hist-empty">
            还没有任务
            <br />
            在对话中点「✨ 生成测试用例」
          </div>
        ) : (
          tasks.map((t) => {
            const b = statusBadge(t.status);
            return (
              <div
                key={t.id}
                className={`task-item ${t.status}`}
                onClick={() => void openDetail(t.id)}
                title="点击查看任务详情（用例 / 思维导图 / 导出）"
              >
                <div className="t-name">{t.name}</div>
                <div className="t-meta">
                  <span className={`pill pill-${b.cls}`}>{b.text}</span>
                  {t.source_type === "iterate" && (
                    <span className="pill pill-sub" title={`迭代自 ${t.parent_task_id || ""}`}>
                      迭代
                    </span>
                  )}
                  <span>{t.cases_count || 0} 用例</span>
                  <span>{fmtTime(t.created_at)}</span>
                  <button
                    className="h-del t-locate"
                    type="button"
                    title="定位到对话中的任务卡"
                    onClick={(e) => {
                      e.stopPropagation();
                      focusTask(t.id);
                    }}
                  >
                    ⌖
                  </button>
                  <button
                    className="h-del"
                    type="button"
                    title="删除任务"
                    onClick={(e) => {
                      e.stopPropagation();
                      if (window.confirm(`确定删除任务「${t.name}」（${t.cases_count} 个用例）？`)) void deleteTask(t.id);
                    }}
                  >
                    🗑
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
