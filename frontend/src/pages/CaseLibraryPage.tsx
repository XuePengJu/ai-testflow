/**
 * V5.5 测试用例库页：测试用例（集）资产化管理，取代原会话页右侧「我的任务」栏。
 *
 * 定位：会话 = 生成过程（用完即走），用例库 = 长期沉淀的测试资产。
 * 数据源：复用 taskStore 的任务列表（5s 轮询，会话页/库页共享同一份数据），客户端筛选。
 * 布局：两栏 —— 左侧分类面板（CategoryTree 常驻，点行即筛选）+ 右侧列表区（搜索/状态筛选 + 表格）。
 * - 筛选：分类走 categoryStore.filter（与树共享状态）；关键词 / 评审状态在工具栏
 * - 版本：groupByChain 聚合迭代链，一行 = 一条用例集（展示最新版版本号）
 * - 操作：详情（复用全局 TaskDetailDrawer）/ 重命名（行内编辑）/ 归类 / 评审切换 / 来源会话回跳 / 删除（hover 显示）
 */
import { useEffect, useMemo, useState } from "react";
import {
  Search, Tag, Trash2, Pencil, Check, X, BookCheck, MessageCircle, ArrowLeftRight,
} from "lucide-react";
import { useTaskStore } from "../store/taskStore";
import { useCategoryStore } from "../store/categoryStore";
import { useChatStore } from "../store/chatStore";
import { useAuth } from "../hooks/useAuth";
import { apiJson, toast } from "../api/client";
import { groupByChain } from "../utils/taskChain";
import { statusBadge } from "../components/chat/TaskStepsCard";
import CategoryTree from "../components/task/CategoryTree";
import type { Task } from "../types";
import { parseServerTime } from "../utils/time";

type ReviewFilter = "all" | "draft" | "reviewed";

function fmtTime(s?: string | null): string {
  const d = parseServerTime(s);
  if (!d) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 评审状态徽章 */
function reviewBadge(rs?: string): { text: string; cls: string } {
  return rs === "reviewed"
    ? { text: "已评审", cls: "rv-ok" }
    : { text: "草稿", cls: "rv-draft" };
}

export default function CaseLibraryPage() {
  const tasks = useTaskStore((s) => s.tasks);
  const listLoaded = useTaskStore((s) => s.listLoaded);
  const deleteTask = useTaskStore((s) => s.deleteTask);
  const openDetail = useTaskStore((s) => s.openDetail);
  const { token } = useAuth();

  const categories = useCategoryStore((s) => s.categories);
  const refreshCats = useCategoryStore((s) => s.refresh);
  const moveTask = useCategoryStore((s) => s.moveTask);
  // 分类筛选与左侧 CategoryTree 共享同一份 store 状态（树里点行 = 筛选，行为一致）
  const catFilter = useCategoryStore((s) => s.filter);

  const [q, setQ] = useState("");
  const [rvFilter, setRvFilter] = useState<ReviewFilter>("all");
  /** 行内重命名的任务 id 与草稿值 */
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  /** 展开归类菜单的任务 id */
  const [menuTaskId, setMenuTaskId] = useState<string | null>(null);

  // 列表轮询由 App 层统一接管（登录期间 5s 轮询，会话页任务卡与库页共享同一份数据）
  const taskCount = tasks.length;
  useEffect(() => {
    if (token) void refreshCats();
  }, [token, taskCount, refreshCats]);

  const groups = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const filtered = tasks.filter((t) => {
      if (kw && !t.name.toLowerCase().includes(kw)) return false;
      if (catFilter === "none" && t.category_id != null) return false;
      if (typeof catFilter === "number" && t.category_id !== catFilter) return false;
      if (rvFilter !== "all" && (t.review_status || "draft") !== rvFilter) return false;
      return true;
    });
    return groupByChain(filtered);
  }, [tasks, q, catFilter, rvFilter]);

  const catName = (id: number | null | undefined): string =>
    id == null ? "未分类" : categories.find((c) => c.id === id)?.name || "未分类";

  const startEdit = (t: Task) => {
    setEditingId(t.id);
    setEditName(t.name);
    setMenuTaskId(null);
  };

  const saveEdit = async () => {
    const id = editingId;
    if (!id) return;
    const name = editName.trim();
    if (!name) {
      toast("名称不能为空");
      return;
    }
    const r = await apiJson(`/api/tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    });
    if (r) {
      toast("已重命名");
      void useTaskStore.getState().refresh();
    } else {
      toast("重命名失败");
    }
    setEditingId(null);
  };

  const toggleReview = async (t: Task) => {
    const next = (t.review_status || "draft") === "reviewed" ? "draft" : "reviewed";
    const r = await apiJson(`/api/tasks/${t.id}`, {
      method: "PATCH",
      body: JSON.stringify({ review_status: next }),
    });
    if (r) {
      toast(next === "reviewed" ? "已标记为已评审" : "已退回草稿");
      void useTaskStore.getState().refresh();
    } else {
      toast("更新评审状态失败");
    }
  };

  const gotoConversation = (t: Task) => {
    if (!t.conversation_id) {
      toast("该任务没有关联会话");
      return;
    }
    void useChatStore.getState().loadConversation(t.conversation_id);
    window.dispatchEvent(new CustomEvent("nav-to", { detail: "main" }));
  };

  const doMove = async (taskId: string, categoryId: number | null) => {
    setMenuTaskId(null);
    if (await moveTask(taskId, categoryId)) {
      toast("已归入「" + (categoryId == null ? "未分类" : catName(categoryId)) + "」");
    }
  };

  if (!token) {
    return (
      <main className="cases-page">
        <div className="hist-empty">登录后查看用例库</div>
      </main>
    );
  }

  return (
    <main className="cases-page" aria-label="测试用例库">
      {/* 左栏：分类面板（常驻，点行筛选，增删改在树内完成） */}
      <aside className="cl-side" aria-label="用例分类">
        <CategoryTree />
      </aside>

      {/* 右栏：列表区 */}
      <div className="cl-main">
        <div className="cl-head">
          <div className="cl-title">
            测试用例库<span className="cl-count">{tasks.length ? `（${tasks.length}）` : ""}</span>
          </div>
          <div className="cl-toolbar">
            <div className="cl-search">
              <Search size={14} />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="搜索用例集名称…"
                aria-label="搜索用例集"
              />
            </div>
            <select
              className="cl-select"
              value={rvFilter}
              onChange={(e) => setRvFilter(e.target.value as ReviewFilter)}
              aria-label="按评审状态筛选"
            >
              <option value="all">全部状态</option>
              <option value="draft">草稿</option>
              <option value="reviewed">已评审</option>
            </select>
          </div>
        </div>

        <div className="cl-table-wrap">
          {!listLoaded && tasks.length === 0 ? (
            <div className="hist-empty">加载中…</div>
          ) : groups.length === 0 ? (
            <div className="hist-empty">
              {tasks.length > 0 ? "没有匹配的用例集，试试调整筛选条件" : <>用例库还是空的<br />在 AI 会话中生成用例后会自动入库</>}
            </div>
          ) : (
            <table className="cl-table">
              <thead>
                <tr>
                  <th>用例集</th>
                  <th className="cl-col-ver">版本</th>
                  <th className="cl-col-num">条数</th>
                  <th className="cl-col-st">状态</th>
                  <th className="cl-col-time">更新时间</th>
                  <th className="cl-col-ops">操作</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((g) => {
                  const t = g.latest;
                  const rb = reviewBadge(t.review_status);
                  const gb = statusBadge(t.status);
                  return (
                    <tr key={t.id} data-testid={`case-row-${t.id}`}>
                      <td>
                        {editingId === t.id ? (
                          <span className="cl-edit">
                            <input
                              value={editName}
                              onChange={(e) => setEditName(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter") void saveEdit();
                                if (e.key === "Escape") setEditingId(null);
                              }}
                              autoFocus
                              aria-label="重命名用例集"
                            />
                            <button type="button" className="cl-op" title="保存" onClick={() => void saveEdit()}><Check size={14} /></button>
                            <button type="button" className="cl-op" title="取消" onClick={() => setEditingId(null)}><X size={14} /></button>
                          </span>
                        ) : (
                          <button type="button" className="cl-name" title="查看详情（用例 / 思维导图 / 导出）" onClick={() => void openDetail(t.id)}>
                            {t.name}
                          </button>
                        )}
                      </td>
                      <td className="cl-col-ver">
                        {g.versions > 1 ? <span className="pill pill-sub" title={`含 ${g.versions} 个版本（详情内可切换）`}>v{g.versions}</span> : <span className="cl-muted">v1</span>}
                      </td>
                      <td className="cl-col-num">{t.cases_count || 0}</td>
                      <td className="cl-col-st">
                        <span className={`pill ${rb.cls}`}>{rb.text}</span>
                        {t.status !== "completed" && (
                          <span className={`pill pill-${gb.cls}`} style={{ marginLeft: 4 }}>{gb.text}</span>
                        )}
                      </td>
                      <td className="cl-col-time cl-muted">{fmtTime(t.created_at)}</td>
                      <td className="cl-col-ops">
                        <button type="button" className="cl-op" title={rb.text === "已评审" ? "退回草稿" : "标记已评审"} onClick={() => void toggleReview(t)}>
                          <BookCheck size={14} />
                        </button>
                        <button type="button" className="cl-op" title="重命名" onClick={() => startEdit(t)}><Pencil size={14} /></button>
                        <button
                          type="button"
                          className="cl-op"
                          title={`归类（当前：${catName(t.category_id)}）`}
                          data-testid={`cat-menu-${t.id}`}
                          onClick={() => setMenuTaskId(menuTaskId === t.id ? null : t.id)}
                        >
                          <Tag size={14} />
                        </button>
                        <button type="button" className="cl-op" title="回跳来源会话" onClick={() => gotoConversation(t)}>
                          <MessageCircle size={14} />
                        </button>
                        <button
                          type="button"
                          className="cl-op cl-danger"
                          title="删除用例集"
                          onClick={() => {
                            if (window.confirm(`确定删除用例集「${t.name}」（${t.cases_count} 个用例）？`)) void deleteTask(t.id);
                          }}
                        >
                          <Trash2 size={14} />
                        </button>
                        {menuTaskId === t.id && (
                          <span className="cl-move-menu" onClick={(e) => e.stopPropagation()} data-testid={`cat-move-${t.id}`}>
                            <span className="cmm-title">归类到…</span>
                            <button type="button" onClick={() => void doMove(t.id, null)}>未分类</button>
                            {categories.map((c) => (
                              <button key={c.id} type="button" onClick={() => void doMove(t.id, c.id)}>
                                {c.name}（{c.task_count}）
                              </button>
                            ))}
                            {categories.length === 0 && <span className="cmm-empty">暂无分类</span>}
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <div className="cl-foot">
          <ArrowLeftRight size={12} />
          会话中生成完成后自动入库为「草稿」；评审通过后标记「已评审」。生成过程请回 AI 会话查看。
        </div>
      </div>
    </main>
  );
}
