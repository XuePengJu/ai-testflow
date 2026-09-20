/**
 * 任务列表侧栏：5s 轮询刷新，状态徽章。
 * - 点击任务项 → 打开详情抽屉（M3）
 * - 「⌖」定位按钮 → 滚动到聊天内任务卡（M2 行为保留）
 * - M4：分类入口改为「☰ 分类」弹层（两级树 + 过滤），顶部筛选 chip 可清除
 * - V4：图标改用 lucide-react。
 */
import { useEffect, useRef, useState } from "react";
import { groupByChain } from "../../utils/taskChain";
import { ListFilter, X, Tag, Crosshair, Trash2 } from "lucide-react";
import { startListPolling, useTaskStore } from "../../store/taskStore";
import { useChatStore } from "../../store/chatStore";
import { useCategoryStore } from "../../store/categoryStore";
import { statusBadge } from "../chat/TaskStepsCard";
import CategoryTree from "./CategoryTree";
import { useAuth } from "../../hooks/useAuth";
import { toast } from "../../api/client";
import type { Task } from "../../types";
import { parseServerTime } from "../../utils/time";

function fmtTime(s?: string | null): string {
  const d = parseServerTime(s);
  if (!d) return "";
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
  const setFilter = useCategoryStore((s) => s.setFilter);
  const refreshCats = useCategoryStore((s) => s.refresh);
  const moveTask = useCategoryStore((s) => s.moveTask);

  /** 当前展开「归类」菜单的任务 id */
  const [menuTaskId, setMenuTaskId] = useState<string | null>(null);
  /** 展开历史版本的任务（最新版本行的 id） */
  const [openHist, setOpenHist] = useState<string | null>(null);
  /** 分类弹层开关 + 定位 ref（fixed 浮层，避开 side-panel overflow:hidden 裁切） */
  const [catOpen, setCatOpen] = useState(false);
  const catBtnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

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

  // 分类弹层：打开时基于触发按钮坐标定位（fixed），并监听外部点击关闭
  useEffect(() => {
    if (!catOpen) return;
    const btn = catBtnRef.current;
    const pop = popRef.current;
    if (btn && pop) {
      const r = btn.getBoundingClientRect();
      const W = 264;
      let left = r.right - W;
      if (left < 8) left = 8;
      let top = r.bottom + 6;
      const h = pop.offsetHeight;
      if (top + h > window.innerHeight - 8) {
        top = Math.max(8, r.top - h - 6); // 底部空间不足则上翻到按钮上方
      }
      pop.style.left = left + "px";
      pop.style.top = top + "px";
      pop.style.width = W + "px";
    }
    function onDown(e: MouseEvent) {
      const t = e.target as Node;
      if (pop?.contains(t) || btn?.contains(t)) return;
      setCatOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [catOpen]);

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

  // 同一条迭代链只占一行（最新版本），历史版本折叠在「含 N 个版本」里展开
  const groups = groupByChain(visible);

  const catName = (id: number | null | undefined): string =>
    id == null ? "未分类" : categories.find((c) => c.id === id)?.name || "未分类";

  const doMove = async (taskId: string, categoryId: number | null) => {
    setMenuTaskId(null);
    if (await moveTask(taskId, categoryId)) {
      toast("任务已归入「" + (categoryId == null ? "未分类" : catName(categoryId)) + "」");
    }
  };

  return (
    <aside className="side-panel task-panel">
      <div className="side-head">
        <span>我的任务{tasks.length ? `（${tasks.length}）` : ""}</span>
        <button
          ref={catBtnRef}
          className="cat-toggle-btn"
          type="button"
          aria-haspopup="dialog"
          aria-expanded={catOpen}
          onClick={() => setCatOpen((o) => !o)}
        >
          <ListFilter size={14} /> 分类
        </button>
      </div>

      {filter !== "all" && (
        <div className="cat-chip-row">
          <span className="cat-chip">
            {filter === "none" ? "未分类" : catName(filter)}
            <button type="button" onClick={() => setFilter("all")} aria-label="清除分类筛选"><X size={12} /></button>
          </span>
        </div>
      )}

      {catOpen && (
        <div className="cat-popover" ref={popRef} role="dialog" aria-label="任务分类">
          <CategoryTree />
        </div>
      )}

      <div className="hist-list">
        {!listLoaded && tasks.length === 0 ? (
          <div className="hist-empty">加载中…</div>
        ) : visible.length === 0 ? (
          <div className="hist-empty">
            {tasks.length > 0 ? "该分类下暂无任务" : <>还没有任务<br />在对话中点「✨ 生成测试用例」</>}
          </div>
        ) : (
          groups.map((g) => {
            const t = g.latest;
            const b = statusBadge(t.status);
            return (
              <div className="task-group" key={t.id}>
              <div
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
                    <Tag size={14} />
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
                    <Crosshair size={14} />
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
                    <Trash2 size={14} />
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
                {g.versions > 1 && (
                  <button
                    type="button"
                    className="tg-hist-toggle"
                    title="展开/收起该任务的历史迭代版本"
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpenHist(openHist === t.id ? null : t.id);
                    }}
                  >
                    <span className="tht-arrow">{openHist === t.id ? "▴" : "▾"}</span>
                    共 {g.versions} 个版本
                  </button>
                )}
              </div>
              {openHist === t.id && (
                <div className="hist-wrap">
                  {g.history
                    .slice()
                    .reverse()
                    .map((h, i) => {
                      const hb = statusBadge(h.status);
                      const v = g.history.length - i; // 最新的历史版本 = versions-1
                      return (
                        <div
                          key={h.id}
                          className="task-item hist-sub"
                          data-task-id={h.id}
                          onClick={() => void openDetail(h.id)}
                          title={`查看历史版本 v${v}（详情内可切换版本）`}
                        >
                          <div className="t-name">
                            <span className="hv-badge">v{v}</span>
                            <span className="hv-name">{h.name}</span>
                          </div>
                          <div className="t-meta">
                            <span className={`pill pill-${hb.cls}`}>{hb.text}</span>
                            <span>{h.cases_count || 0} 用例</span>
                            <span>{fmtTime(h.created_at)}</span>
                          </div>
                        </div>
                      );
                    })}
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
