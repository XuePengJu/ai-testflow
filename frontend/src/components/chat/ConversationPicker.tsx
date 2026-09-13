/**
 * 会话历史侧栏：列表 / 切换回放 / 删除 / 新建会话。
 * V4：按 updated_at 分组今天/昨天/更早；删除按钮用图标；新建用 + 图标。
 */
import { useChatStore } from "../../store/chatStore";
import { useAuth } from "../../hooks/useAuth";
import { Trash2, Plus } from "lucide-react";

function fmtTime(s?: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

type Conv = {
  id: string;
  title: string;
  message_count?: number;
  task_count?: number;
  updated_at?: string | null;
  created_at?: string | null;
};
type Group = "今天" | "昨天" | "更早";

function groupByDate(list: Conv[]): Record<Group, Conv[]> {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const yest = new Date(today);
  yest.setDate(today.getDate() - 1);
  const out: Record<Group, Conv[]> = { 今天: [], 昨天: [], 更早: [] };
  list.forEach((c) => {
    const d = new Date(c.updated_at || c.created_at || 0);
    if (isNaN(d.getTime())) {
      out["更早"].push(c);
      return;
    }
    const dd = new Date(d);
    dd.setHours(0, 0, 0, 0);
    if (dd.getTime() === today.getTime()) out["今天"].push(c);
    else if (dd.getTime() === yest.getTime()) out["昨天"].push(c);
    else out["更早"].push(c);
  });
  return out;
}

export default function ConversationPicker() {
  const conversations = useChatStore((s) => s.conversations);
  const currentId = useChatStore((s) => s.conversationId);
  const loadConversation = useChatStore((s) => s.loadConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const newConversation = useChatStore((s) => s.newConversation);
  const { token } = useAuth();

  const renderItem = (c: Conv) => (
    <div
      key={c.id}
      className={`hist-item ${c.id === currentId ? "active" : ""}`}
      onClick={() => void loadConversation(c.id)}
    >
      <button
        className="h-del"
        title="删除会话"
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          const n = c.task_count || 0;
          const tip = n
            ? `该会话关联 ${n} 个任务，删除会话不影响任务。确定删除「${c.title}」？`
            : `确定删除「${c.title}」？`;
          if (window.confirm(tip)) void deleteConversation(c.id);
        }}
      >
        <Trash2 size={14} />
      </button>
      <div className="h-name">{c.title}</div>
      <div className="h-meta">
        <span>{c.message_count || 0} 条消息</span>
        {c.task_count ? (
          <>
            <span>·</span>
            <span>{c.task_count} 个任务</span>
          </>
        ) : null}
        <span>·</span>
        <span>{fmtTime(c.updated_at || c.created_at)}</span>
      </div>
    </div>
  );

  if (!token) {
    return (
      <aside className="side-panel conv-panel">
        <div className="side-head">
          <span>历史会话</span>
          <button className="qtag" type="button" onClick={newConversation}>
            <Plus size={14} /> 新对话
          </button>
        </div>
        <div className="hist-empty">登录或游客体验后查看历史会话</div>
      </aside>
    );
  }

  const groups = groupByDate(conversations);

  return (
    <aside className="side-panel conv-panel">
      <div className="side-head">
        <span>历史会话</span>
        <button className="qtag" type="button" onClick={newConversation}>
          <Plus size={14} /> 新对话
        </button>
      </div>
      <div className="hist-list">
        {conversations.length === 0 ? (
          <div className="hist-empty">
            还没有会话
            <br />
            右侧发条消息开始
          </div>
        ) : (
          (["今天", "昨天", "更早"] as Group[]).map((label) =>
            groups[label].length === 0 ? null : (
              <div key={label}>
                <div className="group-label">{label}</div>
                {groups[label].map((c) => renderItem(c))}
              </div>
            )
          )
        )}
      </div>
    </aside>
  );
}
