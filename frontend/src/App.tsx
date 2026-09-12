import { useState } from "react";
import { useAuth } from "./hooks/useAuth";

/**
 * M1 脚手架版 App：认证壳 + 加密链路自检。
 * M2 起主页替换为对话驱动视图（chat/），此处结构会随里程碑演进。
 */
export default function App() {
  const { me, role, ready, showLogin, logout } = useAuth();
  const [check, setCheck] = useState<string>("");

  const runSelfCheck = async () => {
    setCheck("请求中…");
    try {
      const { api } = await import("./api/client");
      const r = await api("/api/auth/me");
      const text = await r.text();
      setCheck(
        `HTTP ${r.status}\n${text.slice(0, 400)}` +
          (role && role !== "admin"
            ? `\n\n✓ 响应已走 AES-256-GCM 透明解密（role=${role}）`
            : `\n\n- admin 明文直通（role=${role ?? "未登录"}）`),
      );
    } catch (e) {
      setCheck("✗ " + (e instanceof Error ? e.message : String(e)));
    }
  };

  if (!ready) {
    return <div className="m1-loading">加载中…</div>;
  }

  return (
    <div className="m1-shell">
      <header className="m1-header">
        <div className="m1-logo">AI 测试工作流平台</div>
        <div className="m1-identity">
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

      <main className="m1-main">
        <h1>V2.8 · M1 脚手架就绪</h1>
        <p className="m1-desc">
          React 18 + TypeScript(strict) + Vite 5 · monorepo（frontend-legacy 可一键回退）。
          <br />
          M2 起这里将是对话驱动主页；当前页面用于 M1 验收：三级角色登录 + 加密链路。
        </p>

        <section className="m1-card">
          <h3>加密链路自检</h3>
          <p className="m1-hint">
            通过 api client 请求 <code>/api/auth/me</code>：
            user/guest 的 JSON 响应会被后端加密为 <code>{"{enc:...}"}</code>，
            前端透明解密后应显示明文用户信息——双向打通即证明 AES-256-GCM 迁移无误。
          </p>
          <button className="btn primary" onClick={runSelfCheck}>
            运行自检
          </button>
          {check && <pre className="m1-pre">{check}</pre>}
        </section>
      </main>
    </div>
  );
}
