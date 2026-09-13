/**
 * V2.8 应用壳（M4 更新）+ V4 导航重设计：
 * - main：对话驱动三栏布局（左历史会话 | 中对话流 | 右任务列表+分类树）
 * - settings：设置页（个人中心 + LLM 配置）——登录用户可用
 * - admin：管理页（统计 + 用户 + 平台默认模型）——仅 admin
 * V4：顶部导航改用 lucide 图标；自检改为状态 pill；用户区改为头像 + 退出图标按钮。
 */
import { useEffect, useState, type ReactNode } from "react";
import { LayoutDashboard, Settings, Shield, ShieldCheck, Loader2, LogOut } from "lucide-react";
import { useAuth } from "./hooks/useAuth";
import { useChatStore } from "./store/chatStore";
import { useTaskStore } from "./store/taskStore";
import { useSettingsStore } from "./store/settingsStore";
import { useCategoryStore } from "./store/categoryStore";
import ChatPanel from "./components/chat/ChatPanel";
import ConversationPicker from "./components/chat/ConversationPicker";
import TaskList from "./components/task/TaskList";
import TaskDetailDrawer from "./components/task/TaskDetailDrawer";
import SettingsPage from "./pages/SettingsPage";
import AdminPage from "./pages/AdminPage";
import { toast } from "./api/client";

type View = "main" | "settings" | "admin";

export default function App() {
  const { me, role, ready, token, showLogin, logout } = useAuth();
  const refreshConversations = useChatStore((s) => s.refreshConversations);
  const newConversation = useChatStore((s) => s.newConversation);
  const refreshTasks = useTaskStore((s) => s.refresh);
  const [checking, setChecking] = useState(false);
  const [view, setView] = useState<View>("main");

  // 登录态变化：拉会话/任务；登出清对话并回主视图
  useEffect(() => {
    if (token) {
      void refreshConversations();
      void refreshTasks();
    } else {
      newConversation();
      useSettingsStore.getState().reset();
      useCategoryStore.getState().reset();
      setView("main");
    }
  }, [token, refreshConversations, refreshTasks, newConversation]);

  // 非 admin 切到 admin 视图时踢回 main（登出/角色变化兜底）
  useEffect(() => {
    if (view === "admin" && ready && role !== "admin") setView("main");
    if (view === "settings" && ready && !me) setView("main");
  }, [view, ready, role, me]);

  const runSelfCheck = async () => {
    if (checking) return;
    setChecking(true);
    try {
      const { api } = await import("./api/client");
      const r = await api("/api/auth/me");
      const text = await r.text();
      const encNote =
        role && role !== "admin"
          ? `✓ AES-256-GCM 透明解密正常（role=${role}）`
          : `- 明文直通（role=${role ?? "未登录"}）`;
      toast(`HTTP ${r.status} · ${encNote} · ${text.slice(0, 120)}`);
    } catch (e) {
      toast("✗ " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setChecking(false);
    }
  };

  if (!ready) {
    return <div className="m1-loading">加载中…</div>;
  }

  const navBtn = (v: View, icon: ReactNode, label: string, show: boolean, extra = "") =>
    show ? (
      <button
        key={v}
        className={"btn nav-btn " + (view === v ? "on " : "ghost ") + extra}
        onClick={() => setView(v)}
      >
        {icon}
        <span>{label}</span>
      </button>
    ) : null;

  return (
    <div className="app-shell">
      <header className="app-header">
        <div
          className="app-logo"
          onClick={() => setView("main")}
          title="回到工作台"
          style={{ display: "flex", alignItems: "center", gap: 8 }}
        >
          <Shield size={20} color="#165dff" />
          AI 测试工作流平台
        </div>
        <nav className="app-nav">
          {navBtn("main", <LayoutDashboard size={16} />, "工作台", true)}
          {navBtn("settings", <Settings size={16} />, "设置", !!me)}
          {navBtn("admin", <Shield size={16} />, "管理", role === "admin", "admin-nav")}
        </nav>
        <div className="app-identity">
          <button
            className="status-pill ok"
            style={{ border: "none", cursor: "pointer", fontFamily: "inherit" }}
            onClick={runSelfCheck}
            disabled={checking}
            title="验证加密链路透明解密"
          >
            {checking ? <Loader2 size={14} className="spin" /> : <ShieldCheck size={14} />}
            <span>{checking ? "检测中…" : "系统自检正常"}</span>
          </button>
          {!me || !role ? (
            <button className="btn-primary btn-sm" onClick={() => showLogin("login")}>
              登录 / 注册
            </button>
          ) : (
            <>
              <button
                className="status-pill"
                style={{
                  background: "var(--gray-100)",
                  color: "var(--gray-700)",
                  border: "none",
                  cursor: "pointer",
                  fontFamily: "inherit",
                  gap: 8,
                }}
                onClick={() => setView("settings")}
                title="个人中心（设置页）"
              >
                <span className="avatar">{me.username.slice(0, 1).toUpperCase()}</span>
                <span>
                  {role === "guest"
                    ? `访客 · 剩 ${Math.max(0, Math.round(me.remaining_hours || 0))} 小时`
                    : `${role === "admin" ? "管理员" : "用户"} · ${me.username}`}
                </span>
              </button>
              <button className="icon-btn" onClick={logout} title="退出登录" aria-label="退出登录">
                <LogOut size={16} />
              </button>
            </>
          )}
        </div>
      </header>

      {view === "main" && (
        <main className="app-main">
          <ConversationPicker />
          <ChatPanel />
          <TaskList />
        </main>
      )}
      {view === "settings" && <SettingsPage />}
      {view === "admin" && <AdminPage />}

      <TaskDetailDrawer />
    </div>
  );
}
