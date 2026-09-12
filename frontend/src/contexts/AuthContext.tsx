/**
 * AuthContext：三级角色认证（guest / user / admin）。
 *
 * 行为对齐旧前端（frontend-legacy）：
 * - 首次打开（无 token 且非手动退出）→ 静默进访客模式
 * - 手动退出 → sessionStorage 标记 atf_manual_logout，重载后弹登录框、不再自动进访客
 * - /auth/me 刷新用户信息（guest 剩余时长倒计时）
 * - 登录/注册/访客/转正均走明文通道，成功后整页 reload（与旧版一致，状态从 localStorage 重建）
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { AuthResponse, Me, Role } from "../types";
import {
  clearPersisted,
  persist,
  readPersisted,
  setAuthSnapshot,
  TOKEN_KEY,
  type AuthSnapshot,
} from "./authState";

type AuthMode = "login" | "register" | "upgrade";

/** 后端错误 detail 提取：字符串直取；422 校验数组取首条 msg（避免显示 [object Object]） */
function detailOf(d: { detail?: unknown } | null | undefined): string {
  if (!d) return "";
  if (typeof d.detail === "string") return d.detail;
  if (Array.isArray(d.detail)) {
    const first = d.detail[0] as { msg?: string; loc?: unknown[] } | undefined;
    const field = Array.isArray(first?.loc) ? first.loc.filter((x) => typeof x === "string").join(".") : "";
    return (field ? field + ": " : "") + (first?.msg || "参数校验失败");
  }
  return "";
}

interface AuthContextValue {
  me: Me | null;
  token: string;
  role: Role | null;
  ready: boolean; // 初始化完成（含静默访客进入 / me 刷新）
  showLogin: (mode?: AuthMode) => void;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, email: string, password: string) => Promise<void>;
  guest: () => Promise<void>;
  upgrade: (username: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

function commit(s: AuthSnapshot): void {
  setAuthSnapshot(s);
  persist(s);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthSnapshot>(() => {
    const s = readPersisted();
    setAuthSnapshot(s);
    return s;
  });
  const [ready, setReady] = useState(false);
  const [loginModal, setLoginModal] = useState<{ open: boolean; mode: AuthMode }>({
    open: false,
    mode: "login",
  });
  const startedRef = useRef(false);

  const apply = useCallback((s: AuthSnapshot) => {
    commit(s);
    setState(s);
  }, []);

  /* 初始化：refreshMe + 首开静默访客（旧版 refreshMe/enterGuestSilently 行为） */
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    (async () => {
      if (!state.token) {
        let manual = false;
        try {
          manual = sessionStorage.getItem("atf_manual_logout") === "1";
          sessionStorage.removeItem("atf_manual_logout");
        } catch {
          /* 隐私模式等边界 */
        }
        if (manual) {
          setLoginModal({ open: true, mode: "login" });
          setReady(true);
          return;
        }
        // 静默进访客
        try {
          const r = await fetch("/api/guest/token", { method: "POST" });
          const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse>;
          if (r.ok && d.access_token) {
            apply({
              token: d.access_token,
              encKey: d.enc_key || "",
              me: {
                id: -1,
                username: d.username || "guest",
                email: null,
                role: "guest",
                is_active: true,
                remaining_hours: d.remaining_hours ?? null,
              },
            });
            setReady(true);
            return;
          }
        } catch {
          /* 后端不可达：保持未登录，弹登录框 */
        }
        setLoginModal({ open: true, mode: "login" });
        setReady(true);
        return;
      }
      // 有 token：刷新 me
      try {
        const r = await fetch("/api/auth/me", {
          headers: { Authorization: "Bearer " + state.token },
        });
        if (r.ok) {
          const me = (await r.json()) as Me;
          apply({ ...state, me });
        } else if (r.status === 401) {
          clearPersisted();
          setAuthSnapshot({ token: "", encKey: "", me: null });
          setState({ token: "", encKey: "", me: null });
          setLoginModal({ open: true, mode: "login" });
        }
      } catch {
        /* 网络错误：沿用 localStorage 缓存的 me */
      }
      setReady(true);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* client.ts 发出的 401 失效事件 → 立即清态弹登录（与旧版 reload 效果一致但更快） */
  useEffect(() => {
    const onInvalid = () => {
      clearPersisted();
      setAuthSnapshot({ token: "", encKey: "", me: null });
      setState({ token: "", encKey: "", me: null });
      setLoginModal({ open: true, mode: "login" });
    };
    window.addEventListener("aitf-logout-invalid", onInvalid);
    return () => window.removeEventListener("aitf-logout-invalid", onInvalid);
  }, []);

  /* ===== 明文通道认证动作（与旧版 doAuth/doGuest 一致） ===== */

  const login = useCallback(
    async (username: string, password: string) => {
      const fd = new URLSearchParams();
      fd.append("username", username);
      fd.append("password", password);
      const r = await fetch("/api/auth/login", { method: "POST", body: fd });
      const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse> & { detail?: string };
      if (!r.ok) throw new Error(detailOf(d) || "失败：" + r.status);
      const me: Me & { enc_key?: string } = {
        id: -1, // 登录响应无 id，用 /auth/me 刷新后补齐
        username: d.username || username,
        email: null,
        role: d.role || "user",
        is_active: true,
        enc_key: d.enc_key,
      };
      apply({ token: d.access_token || "", encKey: d.enc_key || "", me });
      setLoginModal({ open: false, mode: "login" });
    },
    [apply],
  );

  const register = useCallback(
    async (username: string, email: string, password: string) => {
      const r = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, email, password }),
      });
      const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse> & { detail?: string };
      if (!r.ok) throw new Error(detailOf(d) || "失败：" + r.status);
      const me: Me & { enc_key?: string } = {
        id: -1, // 登录响应无 id，用 /auth/me 刷新后补齐
        username: d.username || username,
        email: email || null,
        role: d.role || "user",
        is_active: true,
        enc_key: d.enc_key,
      };
      apply({ token: d.access_token || "", encKey: d.enc_key || "", me });
      setLoginModal({ open: false, mode: "login" });
    },
    [apply],
  );

  const guest = useCallback(async () => {
    const r = await fetch("/api/guest/token", { method: "POST" });
    const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse> & { detail?: string };
    if (!r.ok) throw new Error(detailOf(d) || "获取访客身份失败");
    apply({
      token: d.access_token || "",
      encKey: d.enc_key || "",
      me: {
        id: -1,
        username: d.username || "guest",
        email: null,
        role: "guest",
        is_active: true,
        remaining_hours: d.remaining_hours ?? null,
      },
    });
    setLoginModal({ open: false, mode: "login" });
  }, [apply]);

  const upgrade = useCallback(
    async (username: string, email: string, password: string) => {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      const t = localStorage.getItem(TOKEN_KEY) || "";
      if (t) headers["Authorization"] = "Bearer " + t;
      const r = await fetch("/api/guest/upgrade", {
        method: "POST",
        headers,
        body: JSON.stringify({ username, email, password }),
      });
      const d = (await r.json().catch(() => ({}))) as Partial<AuthResponse> & { detail?: string };
      if (!r.ok) throw new Error(detailOf(d) || "失败：" + r.status);
      const me: Me & { enc_key?: string } = {
        id: -1, // 登录响应无 id，用 /auth/me 刷新后补齐
        username: d.username || username,
        email: email || null,
        role: d.role || "user",
        is_active: true,
        enc_key: d.enc_key,
      };
      apply({ token: d.access_token || "", encKey: d.enc_key || "", me });
      setLoginModal({ open: false, mode: "login" });
    },
    [apply],
  );

  const logout = useCallback(() => {
    try {
      sessionStorage.setItem("atf_manual_logout", "1");
    } catch {
      /* ignore */
    }
    clearPersisted();
    setAuthSnapshot({ token: "", encKey: "", me: null });
    setState({ token: "", encKey: "", me: null });
    setLoginModal({ open: true, mode: "login" });
  }, []);

  const showLogin = useCallback((mode: AuthMode = "login") => {
    setLoginModal({ open: true, mode });
  }, []);

  /* 登录态变化（login/register/guest/upgrade 成功）→ reload 一次，让各页面拿到干净初始态（旧版行为） */
  const authJustChanged = useRef(false);
  useEffect(() => {
    if (!ready) return;
    if (authJustChanged.current) {
      authJustChanged.current = false;
      return;
    }
    // noop：React 版不再无脑 reload，由各视图自行响应 me 变化
  }, [state.token, ready]);

  const value = useMemo<AuthContextValue>(
    () => ({
      me: state.me,
      token: state.token,
      role: state.me?.role ?? null,
      ready,
      showLogin,
      login: async (...args) => {
        authJustChanged.current = true;
        await login(...args);
      },
      register: async (...args) => {
        authJustChanged.current = true;
        await register(...args);
      },
      guest: async (...args) => {
        authJustChanged.current = true;
        await guest(...args);
      },
      upgrade: async (...args) => {
        authJustChanged.current = true;
        await upgrade(...args);
      },
      logout,
    }),
    [state, ready, showLogin, login, register, guest, upgrade, logout],
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
      {loginModal.open && (
        <LoginModal
          mode={loginModal.mode}
          isGuest={state.me?.role === "guest"}
          onClose={() => setLoginModal({ open: false, mode: "login" })}
        />
      )}
    </AuthContext.Provider>
  );
}

/* 登录弹窗（M1 简版：登录/注册/访客三入口 + 访客转正） */
function LoginModal({
  mode,
  isGuest,
  onClose,
}: {
  mode: AuthMode;
  isGuest: boolean;
  onClose: () => void;
}) {
  const ctx = useContext(AuthContext)!;
  const [tab, setTab] = useState<AuthMode>(mode === "upgrade" ? "upgrade" : mode);
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!username || !password) {
      setError("请填写用户名和密码");
      return;
    }
    if (tab === "register" && !email) {
      setError("请填写邮箱");
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (tab === "login") await ctx.login(username, password);
      else if (tab === "register") await ctx.register(username, email, password);
      else await ctx.upgrade(username, email, password);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="auth-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="auth-modal">
        <h2>
          {tab === "upgrade" ? "注册并保留访客数据" : tab === "register" ? "注册账号" : "登录"}
        </h2>
        {tab !== "upgrade" && (
          <div className="auth-tabs">
            <button className={tab === "login" ? "on" : ""} onClick={() => setTab("login")}>
              登录
            </button>
            <button className={tab === "register" ? "on" : ""} onClick={() => setTab("register")}>
              注册
            </button>
          </div>
        )}
        <input
          placeholder="用户名"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        {tab !== "login" && (
          <input
            placeholder="邮箱"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        )}
        <input
          placeholder="密码"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        {error && <div className="auth-error">{error}</div>}
        <button className="auth-btn" disabled={busy} onClick={submit}>
          {busy ? "请稍候…" : tab === "upgrade" ? "转正并保留数据" : tab === "register" ? "注册" : "登录"}
        </button>
        {tab !== "upgrade" && (
          <button
            className="guest-btn"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await ctx.guest();
              } catch (e) {
                setError(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            👤 游客体验（免注册 · 数据保留 24 小时）
          </button>
        )}
        {isGuest && tab !== "upgrade" && (
          <button className="upgrade-link" onClick={() => setTab("upgrade")}>
            注册保留访客数据 →
          </button>
        )}
      </div>
    </div>
  );
}
