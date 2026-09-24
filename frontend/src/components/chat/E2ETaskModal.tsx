/**
 * M1 全链路测试发起弹窗：输入被测系统 URL（可选登录账密）→ kind=e2e / kind=explore 任务。
 * M5 增量：任务类型切换（全链路测试 e2e 默认 / 探索式测试 explore），复用 url/账密字段。
 * 自包含组件：类型 inline 定义（client.ts/types.ts 为共享文件，本 Modal 不改），
 * 请求走现有 apiJson 通用封装；需要新增的共享条目见 docs/handoff-M1-frontend-shared.md。
 */
import { useEffect, useState } from "react";
import { Globe, X } from "lucide-react";
import { API, apiJson, toast } from "../../api/client";
import { useTaskStore } from "../../store/taskStore";
import { useChatStore } from "../../store/chatStore";

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

export default function E2ETaskModal({ open, onClose }: { open: boolean; onClose: () => void }) {
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

  // 每次打开拉一次已保存的被测系统列表（新→旧）
  useEffect(() => {
    if (!open) return;
    void apiJson<TargetItem[]>(`${API}/targets`).then((rows) => setTargets(rows || []));
  }, [open]);

  if (!open) return null;

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
    onClose();
    const ts = useTaskStore.getState();
    void ts.refresh();
    ts.startPolling(task.id);
  }

  return (
    <div className="auth-overlay" onClick={onClose}>
      <div
        className="auth-modal"
        style={{ width: 420 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h2 style={{ margin: 0 }}>
            <Globe size={18} style={{ verticalAlign: "-3px", marginRight: 6 }} />
            全链路测试
          </h2>
          <button
            type="button"
            className="icon-btn"
            aria-label="关闭"
            onClick={onClose}
            style={{ border: "none", background: "transparent", cursor: "pointer" }}
          >
            <X size={18} />
          </button>
        </div>
        <p className="m1-hint" style={{ margin: 0 }}>
          {kind === "explore"
            ? "AI Agent 将自主打开被测系统、逐步探索页面功能并生成测试用例（可选登录账密）。"
            : "填入被测 Web 系统地址，AI 自动抓取页面结构、拆解测试点并生成测试用例（可选登录账密）。"}
        </p>

        {/* 任务类型切换（M5：explore 复用同一套 url/账密字段，默认仍为 e2e） */}
        <div style={{ display: "flex", gap: 8 }}>
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
                  padding: "8px 10px",
                  borderRadius: 8,
                  border: active ? "1.5px solid #165dff" : "1px solid #dcdfe6",
                  background: active ? "#e8f3ff" : "#fff",
                  color: active ? "#165dff" : "#4e5969",
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  cursor: "pointer",
                  textAlign: "left",
                }}
              >
                {opt.label}
                <div style={{ fontSize: 11, fontWeight: 400, marginTop: 2, color: active ? "#165dff" : "#8f959e" }}>
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
        />

        {!!targets.length && (
          <select
            value={targetId}
            onChange={(e) => setTargetId(e.target.value)}
            style={{
              padding: "10px 12px", border: "1px solid #dcdfe6", borderRadius: 8,
              fontSize: 14, background: "#fff", outline: "none",
            }}
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
            />
            <input
              placeholder="登录账号（可选）"
              value={username}
              autoComplete="off"
              onChange={(e) => setUsername(e.target.value)}
            />
            <input
              placeholder="登录密码（可选）"
              type="password"
              value={password}
              autoComplete="new-password"
              onChange={(e) => setPassword(e.target.value)}
            />
          </>
        )}

        <button className="auth-btn" type="button" disabled={submitting} onClick={() => void doSubmit()}>
          {submitting ? "提交中…" : kind === "explore" ? "🧭 开始探索式测试" : "🌐 开始全链路测试"}
        </button>
      </div>
    </div>
  );
}
