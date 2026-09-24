/**
 * V4.2 应用壳 · 全量对齐设计稿（knowledge-hub-redesign）：
 * - 布局：左侧深色图标 rail（64px）+ 右侧内容区（.app-body）
 *   rail = logo / 对话 / 知识库 / 管理(admin) | spacer | 自检 / GitHub / 设置 / 头像·登出
 * - main：对话驱动三栏（左历史会话 | 中对话流 | 右任务列表+分类树）
 * - knowledge：知识库管理台（V4.2 已对齐设计稿视觉）
 * - settings / admin：整页视图
 * 原 V2.8 顶部 header 全部功能保留，仅形态迁移到 rail（图标 + title 提示）。
 */
import { useEffect, useState } from "react";
import {
  Settings, Shield, ShieldCheck, Loader2, LogOut,
  BookOpen, MessageSquare, UserPlus, ChevronsLeft, ChevronsRight,
  FlaskConical, Boxes, ChevronDown, ExternalLink,
} from "lucide-react";
import { useAuth } from "./hooks/useAuth";
import { useChatStore } from "./store/chatStore";
import { useTaskStore } from "./store/taskStore";
import { useSettingsStore } from "./store/settingsStore";
import { useCategoryStore } from "./store/categoryStore";
import ChatPanel from "./components/chat/ChatPanel";
import ConversationPicker from "./components/chat/ConversationPicker";
import SelfCheckToast from "./components/SelfCheckToast";
import TaskList from "./components/task/TaskList";
import TaskDetailDrawer from "./components/task/TaskDetailDrawer";
import SettingsPage from "./pages/SettingsPage";
import AdminPage from "./pages/AdminPage";
import KnowledgePage from "./pages/KnowledgePage";
import QualityPage from "./pages/QualityPage";

type View = "main" | "settings" | "admin" | "knowledge" | "quality";

const GITHUB_REPO = "https://github.com/XuePengJu/ai-testflow";

/** GitHub 官方 mark（单色，随 currentColor） */
function GithubMark({ size = 18 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12" />
    </svg>
  );
}

export default function App() {
  const { me, role, ready, token, showLogin, logout } = useAuth();
  const refreshConversations = useChatStore((s) => s.refreshConversations);
  const newConversation = useChatStore((s) => s.newConversation);
  const refreshTasks = useTaskStore((s) => s.refresh);
  const [view, setView] = useState<View>(
    () => (localStorage.getItem("aitf_view") as View) || "main"
  );
  useEffect(() => { localStorage.setItem("aitf_view", view); }, [view]);

  // V4.3 侧边栏折叠态（默认展开，记忆用户选择）
  const [railCollapsed, setRailCollapsed] = useState(
    () => localStorage.getItem("aitf_rail_collapsed") === "1"
  );
  useEffect(() => { localStorage.setItem("aitf_rail_collapsed", railCollapsed ? "1" : "0"); }, [railCollapsed]);

  // V4.5.3 被测系统子菜单：数据源数组，后续加链接只需在 TARGETS 里追加一行
  const TARGETS: { name: string; url: string }[] = [
    { name: "DBERP 进销存", url: "https://erp.agentest.vip/" },
  ];
  const [tOpen, setTOpen] = useState(
    () => localStorage.getItem("aitf_target_open") !== "0"
  );
  useEffect(() => { localStorage.setItem("aitf_target_open", tOpen ? "1" : "0"); }, [tOpen]);

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
    // V4.2：guest 也可进知识库（只读共享库，写操作后端 403 + 前端隐藏按钮）
    if (view === "knowledge" && ready && !me) setView("main");
    // 方案 A：质量报告对所有登录角色开放（含访客），未登录回主视图
    if (view === "quality" && ready && !me) setView("main");
  }, [view, ready, role, me]);

  // V4.0：跨页面跳转事件（如知识库创建后引导去设置页配置向量模型）
  useEffect(() => {
    const h = (e: Event) => {
      const d = (e as CustomEvent<string>).detail;
      if (d === "main" || d === "settings" || d === "admin" || d === "knowledge" || d === "quality") setView(d);
    };
    window.addEventListener("nav-to", h);
    return () => window.removeEventListener("nav-to", h);
  }, []);

  // V4.5.1 系统自检：改为右上角悬浮通知（自动消失 / hover 暂停 / 手动关）
  const [scRun, setScRun] = useState(0);      // 递增触发一次检测
  const [scShow, setScShow] = useState(false);
  const runSelfCheck = () => { setScShow(true); setScRun((n) => n + 1); };

  if (!ready) {
    return <div className="m1-loading">加载中…</div>;
  }

  /**
   * V4.3 rail 导航按钮：图标 + 文字（展开态一目了然）；
   * 收起态隐藏文字，hover 弹出 CSS 浮层标签（.rl-tip）替代系统 title。
   */
  const railBtn = (
    v: View | "selfcheck" | "github" | "logout",
    icon: React.ReactNode,
    label: string,
    show: boolean,
    opts: { active?: boolean; onClick?: () => void; cls?: string; aria?: string; ico?: string } = {}
  ) =>
    show ? (
      <button
        key={v}
        className={"rail-btn " + (opts.active ? "active " : "") + (opts.cls || "")}
        onClick={opts.onClick}
        aria-label={opts.aria || label}
      >
        <span className={"rl-ico " + (opts.ico || "")}>{icon}</span>
        <span className="rl-txt">{label}</span>
        <span className="rl-tip" aria-hidden="true">{opts.aria || label}</span>
      </button>
    ) : null;

  const canKb = !!me;

  return (
    <div className="app-shell">
      {/* V4.3 左侧可折叠侧边栏：默认展开（图标+文字），可收起为 64px 图标态 */}
      <nav className={"rail" + (railCollapsed ? " collapsed" : "")} aria-label="主导航">
        <div className="rail-head">
          <div className="rl-logo" title="AI 测试工作流平台" onClick={() => setView("main")} style={{ cursor: "pointer" }}>
            <Shield size={19} />
          </div>
          <div className="rl-title" onClick={() => setView("main")} style={{ cursor: "pointer" }}>
            AI TestFlow
          </div>
        </div>
        <button
          className="rail-collapse"
          onClick={() => setRailCollapsed((c) => !c)}
          aria-label={railCollapsed ? "展开导航" : "收起导航"}
        >
          {railCollapsed ? <ChevronsRight size={13} /> : <ChevronsLeft size={13} />}
        </button>

        <div className="rl-group">工作区</div>
        {railBtn("main", <MessageSquare />, "AI 对话", true, { active: view === "main", onClick: () => setView("main"), aria: "AI 对话 · 测试工作台", ico: "i-chat" })}
        {railBtn("knowledge", <BookOpen />, "知识库", canKb, { active: view === "knowledge", onClick: () => setView("knowledge"), aria: role === "guest" ? "知识库（访客 · 只读共享库）" : "知识库 · 文档与问答", ico: "i-kb" })}
        {/* 方案 A：质量报告对所有登录角色开放（展示用途，运行按钮 admin 专属） */}
        {railBtn("quality", <FlaskConical />, "质量报告", canKb, { active: view === "quality", onClick: () => setView("quality"), aria: "质量报告 · 平台测试量化数据", ico: "i-quality" })}
        {railBtn("admin", <Shield />, "管理后台", role === "admin", { active: view === "admin", onClick: () => setView("admin"), aria: "管理后台（仅管理员）", ico: "i-admin" })}

        <div className="rail-spacer"><span className="rail-sticker" aria-hidden="true">🧪</span></div>

        <div className="rl-group">系统</div>
        {railBtn("selfcheck", scShow ? <Loader2 className="spin" /> : <ShieldCheck />, "系统自检", true, { onClick: runSelfCheck, aria: "系统自检（验证加密链路）", ico: "i-check" })}
        {/* V4.5.3 被测系统：可折叠父项 + 子项外链（TARGETS 数组，后续追加即可） */}
        <div className={"rail-sub" + (tOpen ? " open" : "")}>
          <button
            className="rail-btn"
            onClick={() => setTOpen((o) => !o)}
            aria-expanded={tOpen}
            aria-label="被测系统菜单"
          >
            <span className="rl-ico i-target"><Boxes /></span>
            <span className="rl-txt">被测系统</span>
            <ChevronDown className="rail-chev" />
            <span className="rl-tip" aria-hidden="true">被测系统</span>
          </button>
          <div className="rail-submenu">
            {TARGETS.map((t) => (
              <a
                key={t.url}
                className="rail-subitem"
                href={t.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                <ExternalLink />
                <span className="rs-name">{t.name}</span>
              </a>
            ))}
          </div>
        </div>
        <a
          className="rail-btn"
          href={GITHUB_REPO}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="在 GitHub 上查看源码"
        >
          <span className="rl-ico i-git"><GithubMark /></span>
          <span className="rl-txt">源码</span>
          <span className="rl-tip" aria-hidden="true">在 GitHub 上查看源码</span>
        </a>
        {railBtn("settings", <Settings />, "设置", !!me, { active: view === "settings", onClick: () => setView("settings"), ico: "i-set" })}
        {!me || !role ? (
          <button className="rail-login" onClick={() => showLogin("login")} aria-label="登录 / 注册">
            <span className="rl-txt">登录 / 注册</span>
            <span className="rl-tip" aria-hidden="true">登录 / 注册</span>
          </button>
        ) : (
          <>
            <div className="rail-user">
              <button
                className="rail-avatar"
                onClick={() => setView("settings")}
                aria-label={`${role === "guest" ? "访客" : role === "admin" ? "管理员" : "用户"} · ${me.username}（进设置）`}
              >
                {role === "guest" ? <UserPlus size={16} /> : me.username.slice(0, 1).toUpperCase()}
              </button>
              <div className="rl-user-info" onClick={() => setView("settings")} role="button" tabIndex={0}>
                <div className="rl-user-name">{me.username}</div>
                <div className="rl-user-role">{role === "guest" ? "访客" : role === "admin" ? "管理员" : "用户"}</div>
              </div>
            </div>
            {railBtn("logout", <LogOut />, "退出登录", true, { onClick: logout, cls: "rail-danger", ico: "i-out" })}
          </>
        )}
      </nav>

      {/* 右侧内容区 */}
      <div className="app-body">
        {view === "main" && (
          <main className="app-main">
            <ConversationPicker />
            <ChatPanel showCitations />
            <TaskList />
          </main>
        )}
        {view === "knowledge" && <KnowledgePage />}
        {view === "quality" && <QualityPage />}
        {view === "settings" && <SettingsPage />}
        {view === "admin" && <AdminPage />}
      </div>

      <TaskDetailDrawer />
      {scShow && <SelfCheckToast runId={scRun} onDone={() => setScShow(false)} />}
    </div>
  );
}
