/**
 * 任务列表侧栏：5s 轮询刷新，状态徽章。
 * - 点击任务项 → 打开详情抽屉（M3）
 * - 「⌖」定位按钮 → 滚动到聊天内任务卡（M2 行为保留）
 * - M4：顶部分类树（新建/重命名/删除 + 过滤）+ 每任务「归类」下拉
 */
import { useEffect, useState } from "react";
import { startListPolling, useTaskStore } from "../../store/taskStore";
import { useChatStore } from "../../store/chatStore";
import { useCategoryStore } from "../../store/categoryStore";
import { statusBadge } from "../chat/TaskStepsCard";
import CategoryTree from "./CategoryTree";
import { useAuth } from "../../hooks/useAuth";
import { toast } from "../../api/client";
import type { Task } from "../../types";

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

  const categories = useCategoryStore((s) => s.categories);
  const filter = useCategoryStore((s) => s.filter);
  const refreshCats = useCategoryStore((s) => s.refresh);
  const moveTask = useCategoryStore((s) => s.moveTask);

  /** 当前展开「归类」菜单的任务 id */
  const [menuTaskId, setMenuTaskId] = useState<string | null>(null);

  // 登录期间 5s 轮询；登出/未登录时停
  useEffect(() => {
    if (!token) return;
    const stop = startListPolling();
    return stop;
  }, [token]);

  // 分类变化（归类后）同步刷新计数；任务列表变化也刷一次分类计数（节流：仅在 task 数量变化时）
  const taskCount = tasks.length;
  useEffect(() => {
    if (token) void refreshCats();
  }, [token, taskCount, refreshCats]);

  if (!token) {
    return (
      <aside className="side-panel task-panel">
        <div className="side-head"><span>我的任务</span></div>
        <div className="hist-empty">登录或游客体验后查看任务</div>
      </aside>
    );
  }

  // 分类过滤
  const visible: Task[] = tasks.filter((t) => {
    if (filter === "all") return true;
    if (filter === "none") return t.category_id == null;
    return t.category_id === filter;
  });

  const catName = (id: number | null | undefined): string =>
    id == null ? "未分类" : categories.find((c) => c.id === id)?.name || "未分类";

  const doMove = async (taskId: string, categoryId: number | null) => {
    setMenuTaskId(null);
    if (await moveTask(taskId, categoryId)) {
      toast("任务已归入「" + (categoryId == null ? "未分类" : catName(categoryId)) + "」");
    }
  };

  const filterLabel =
    filter === "all" ? "" : ` · ${filter === "none" ? "未分类" : catName(filter)}`;

  return (
    <aside className="side-panel task-panel">
      <div className="side-head">
        <span>我的任务{filterLabel ? `（${visible.length}/${tasks.length}${filterLabel}）` : tasks.length ? `（${tasks.length}）` : ""}</span>
      </div>

      <CategoryTree />

      <div className="hist-list">
        {!listLoaded && tasks.length === 0 ? (
          <div className="hist-empty">加载中…</div>
        ) : visible.length === 0 ? (
          <div className="hist-empty">
            {tasks.length > 0 ? "该分类下暂无任务" : <>还没有任务<br />在对话中点「✨ 生成测试用例」</>}
          </div>
        ) : (
          visible.map((t) => {
            const b = statusBadge(t.status);
            return (
              <div
                key={t.id}
                className={`task-item ${t.status}`}
                data-task-id={t.id}
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
                    className="h-del t-cat-btn"
                    type="button"
                    title={`归类（当前：${catName(t.category_id)}）`}
                    data-testid={`cat-menu-${t.id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      setMenuTaskId(menuTaskId === t.id ? null : t.id);
                    }}
                  >
                    🏷
                  </button>
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
                {menuTaskId === t.id && (
                  <div className="cat-move-menu" onClick={(e) => e.stopPropagation()} data-testid={`cat-move-${t.id}`}>
                    <div className="cmm-title">归类到…</div>
                    <button type="button" onClick={() => void doMove(t.id, null)}>未分类</button>
                    {categories.map((c) => (
                      <button key={c.id} type="button" onClick={() => void doMove(t.id, c.id)}>
                        {c.name}（{c.task_count}）
                      </button>
                    ))}
                    {categories.length === 0 && <div className="cmm-empty">暂无分类，请先在上方新建</div>}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
