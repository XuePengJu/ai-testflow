import { useEffect, useState } from "react";
import { useAuth } from "./hooks/useAuth";
import { useChatStore } from "./store/chatStore";
import { useTaskStore } from "./store/taskStore";
import ChatPanel from "./components/chat/ChatPanel";
import ConversationPicker from "./components/chat/ConversationPicker";
import TaskList from "./components/task/TaskList";
import TaskDetailDrawer from "./components/task/TaskDetailDrawer";
import { toast } from "./api/client";

/**
 * V2.8 M2：对话驱动三栏布局。
 * 左：历史会话 | 中：对话流（SSE 流式 + 任务步骤卡）| 右：任务列表（5s 轮询）。
 * header 保留 M1 认证区 + 加密链路自检（M2 收为轻量按钮）。
 */
export default function App() {
  const { me, role, ready, token, showLogin, logout } = useAuth();
  const refreshConversations = useChatStore((s) => s.refreshConversations);
  const newConversation = useChatStore((s) => s.newConversation);
  const refreshTasks = useTaskStore((s) => s.refresh);
  const [checking, setChecking] = useState(false);

  // 登录态变化：拉会话/任务；登出清对话
  useEffect(() => {
    if (token) {
      void refreshConversations();
      void refreshTasks();
    } else {
      newConversation();
    }
  }, [token, refreshConversations, refreshTasks, newConversation]);

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

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-logo">AI 测试工作流平台</div>
        <div className="app-identity">
          <button className="btn ghost" onClick={runSelfCheck} disabled={checking} title="验证加密链路透明解密">
            🔐 自检
          </button>
          {!me || !role ? (
            <button className="btn" onClick={() => showLogin("login")}>
              登录 / 注册
            </button>
          ) : (
            <>
              {role === "guest" ? (
                <span className="uc guest" title="个人中心（M4）">
                  访客 · 剩 {Math.max(0, Math.round(me.remaining_hours || 0))} 小时
                </span>
              ) : (
                <span className={"uc" + (role === "admin" ? " admin" : "")} title="个人中心（M4）">
                  {role === "admin" ? "管理员" : "用户"} · {me.username}
                </span>
              )}
              <button className="btn out" onClick={logout}>
                退出
              </button>
            </>
          )}
        </div>
      </header>

      <main className="app-main">
        <ConversationPicker />
        <ChatPanel />
        <TaskList />
      </main>

      <TaskDetailDrawer />
    </div>
  );
}
