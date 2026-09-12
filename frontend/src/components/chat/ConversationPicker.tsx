/**
 * 会话历史侧栏：列表 / 切换回放 / 删除 / 新建会话。
 * 平移旧版 chatHistory + focusHistItem + confirmDeleteConversation。
 */
import { useChatStore } from "../../store/chatStore";
import { useAuth } from "../../hooks/useAuth";

function fmtTime(s?: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function ConversationPicker() {
  const conversations = useChatStore((s) => s.conversations);
  const currentId = useChatStore((s) => s.conversationId);
  const loadConversation = useChatStore((s) => s.loadConversation);
  const deleteConversation = useChatStore((s) => s.deleteConversation);
  const newConversation = useChatStore((s) => s.newConversation);
  const { token } = useAuth();

  if (!token) {
    return (
      <aside className="side-panel conv-panel">
        <div className="side-head">
          <span>历史会话</span>
          <button className="qtag" type="button" onClick={newConversation}>＋ 新对话</button>
        </div>
        <div className="hist-empty">登录或游客体验后查看历史会话</div>
      </aside>
    );
  }

  return (
    <aside className="side-panel conv-panel">
      <div className="side-head">
        <span>历史会话</span>
        <button className="qtag" type="button" onClick={newConversation}>＋ 新对话</button>
      </div>
      <div className="hist-list">
        {conversations.length === 0 ? (
          <div className="hist-empty">
            还没有会话
            <br />
            右侧发条消息开始
          </div>
        ) : (
          conversations.map((c) => (
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
                  const tip = n ? `该会话关联 ${n} 个任务，删除会话不影响任务。确定删除「${c.title}」？` : `确定删除「${c.title}」？`;
                  if (window.confirm(tip)) void deleteConversation(c.id);
                }}
              >
                🗑
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
          ))
        )}
      </div>
    </aside>
  );
}
