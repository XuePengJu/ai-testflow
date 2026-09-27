/**
 * V5.4 全链路测试页：原 M1/M5 发起弹窗升级为独立整页（老板拍板：不做弹窗，做新入口）。
 * - 表单逻辑与原 E2ETaskModal 一致：类型切换（e2e/explore）→ 任务名 → 已存被测系统或手填
 *   网址 + 可选账密 → POST /tasks → 跳回用例设计视图看任务进度。
 * - 全角色可用（访客受后端任务配额约束）。
 * - 自包含 inline 类型定义（原 Modal 惯例，不动共享 types.ts）。
 */
import { useEffect, useState } from "react";
import { Globe } from "lucide-react";
import { API, apiJson, toast } from "../api/client";
import { useTaskStore } from "../store/taskStore";
import { useChatStore } from "../store/chatStore";

/** 与后端 TargetOut 对齐（inline 最小集） */
interface TargetItem {
  id: string;
  name: string;
  base_url: string;
  has_auth: boolean;
}

/** 与后端 TaskOut 对齐（发起任务只需最小集） */
interface CreatedTask {
  id: string;
  name: string;
  kind: string;
  status: string;
}

/** 任务类型切换项（M5：默认仍是 e2e 全链路） */
const KIND_OPTIONS: { value: string; label: string; hint: string }[] = [
  { value: "e2e", label: "全链路测试", hint: "抓取页面 → 拆解测试点 → 生成用例" },
  { value: "explore", label: "探索式测试", hint: "AI Agent 自主探索页面 → 生成用例" },
];

const inputStyle = {
  padding: "11px 12px",
  border: "1px solid #dcdfe6",
  borderRadius: 8,
  fontSize: 14,
  background: "#fff",
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
} as const;

export default function E2EPage() {
  const [targets, setTargets] = useState<TargetItem[]>([]);
  const [targetId, setTargetId] = useState("");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // 任务类型：e2e（默认）/ explore（M5 探索式）
  const [kind, setKind] = useState("e2e");
  // 当前会话 id：非空时随任务提交，任务与消息落进该会话（左侧列表立即可见）
  const conversationId = useChatStore((s) => s.conversationId);

  // 进页拉一次已保存的被测系统列表（新→旧）
  useEffect(() => {
    void apiJson<TargetItem[]>(`${API}/targets`).then((rows) => setTargets(rows || []));
  }, []);

  function reset(): void {
    setTargetId("");
    setName("");
    setUrl("");
    setUsername("");
    setPassword("");
    setKind("e2e"); // 类型也复位为默认全链路
  }

  async function doSubmit(): Promise<void> {
    if (submitting) return;
    if (!targetId && !url.trim()) {
      toast("请填写被测系统地址");
      return;
    }
    setSubmitting(true);
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("formats", "xlsx,json");
    if (name.trim()) fd.append("name", name.trim());
    if (targetId) fd.append("target_id", targetId);
    else {
      fd.append("url", url.trim());
      if (username.trim()) fd.append("username", username.trim());
      if (password) fd.append("password", password);
    }
    if (conversationId) fd.append("conversation_id", conversationId);
    const task = await apiJson<CreatedTask>(`${API}/tasks`, { method: "POST", body: fd });
    setSubmitting(false);
    if (!task) return; // apiJson 已 toast 错误
    toast(kind === "explore" ? "探索式任务已提交：AI Agent 将自主探索页面并生成用例" : "全链路任务已提交：抓取页面 → 生成用例");
    void useChatStore.getState().refreshConversations();
    reset();
    const ts = useTaskStore.getState();
    void ts.refresh();
    ts.startPolling(task.id);
    // 跳回 AI 会话视图（V5.5 改名）：任务卡在会话流里实时可见
    window.dispatchEvent(new CustomEvent("nav-to", { detail: "main" }));
  }

  return (
    <div className="page-wrap" data-testid="e2e-page">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <Globe size={20} aria-hidden="true" />
        <h2 style={{ margin: 0 }}>全链路测试</h2>
      </div>
      <div className="sub" style={{ marginBottom: 18 }}>
        填入被测 Web 系统地址，AI 自动抓取页面结构、拆解测试点并生成测试用例（可选登录账密）。
      </div>

      <div style={{ maxWidth: 560, display: "flex", flexDirection: "column", gap: 12 }}>
        {/* 任务类型切换（M5：explore 复用同一套 url/账密字段，默认仍为 e2e） */}
        <div style={{ display: "flex", gap: 10 }}>
          {KIND_OPTIONS.map((opt) => {
            const active = kind === opt.value;
            return (
              <button
                key={opt.value}
                type="button"
                title={opt.hint}
                onClick={() => setKind(opt.value)}
                style={{
                  flex: 1,
                  padding: "12px 14px",
                  borderRadius: 10,
                  border: active ? "1.5px solid #165dff" : "1px solid #dcdfe6",
                  background: active ? "#e8f3ff" : "#fff",
                  color: active ? "#165dff" : "#4e5969",
                  fontSize: 14,
                  fontWeight: active ? 600 : 400,
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                {opt.label}
                <div style={{ fontSize: 12, fontWeight: 400, marginTop: 3, color: active ? "#165dff" : "#8f959e" }}>
                  {opt.hint}
                </div>
              </button>
            );
          })}
        </div>

        <input
          placeholder="任务名称（可选，默认取网址）"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={inputStyle}
        />

        {!!targets.length && (
          <select
            value={targetId}
            onChange={(e) => setTargetId(e.target.value)}
            style={inputStyle}
          >
            <option value="">— 手动填写网址 —</option>
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}（{t.base_url}{t.has_auth ? " · 已存凭据" : ""}）
              </option>
            ))}
          </select>
        )}

        {!targetId && (
          <>
            <input
              placeholder="被测系统地址，如 https://example.com"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void doSubmit(); }}
              style={inputStyle}
            />
            <input
              placeholder="登录账号（可选）"
              value={username}
              autoComplete="off"
              onChange={(e) => setUsername(e.target.value)}
              style={inputStyle}
            />
            <input
              placeholder="登录密码（可选）"
              type="password"
              value={password}
              autoComplete="new-password"
              onChange={(e) => setPassword(e.target.value)}
              style={inputStyle}
            />
          </>
        )}

        <button
          className="auth-btn"
          type="button"
          disabled={submitting}
          style={{ maxWidth: 240 }}
          onClick={() => void doSubmit()}
        >
          {submitting ? "提交中…" : kind === "explore" ? "🧭 开始探索式测试" : "🌐 开始全链路测试"}
        </button>
      </div>
    </div>
  );
}
