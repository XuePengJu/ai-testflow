/**
 * 单条消息气泡：用户 / AI 流式 / AI 完成（含思考折叠 + 生成按钮）/ 任务卡。
 */
import { useState } from "react";
import type { ChatMsg } from "../../store/chatStore";
import { useChatStore } from "../../store/chatStore";
import { toast } from "../../api/client";
import TaskStepsCard from "./TaskStepsCard";

function ThinkingPanel({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`think-panel ${open ? "open" : ""}`}>
      <button className="think-head" type="button" onClick={() => setOpen(!open)}>
        <span className="arrow">{open ? "▾" : "▸"}</span>
        <span className="label">思考过程</span>
      </button>
      {open && <div className="think-body">{text}</div>}
    </div>
  );
}

/** 简易 markdown：换行段落 + **加粗**（平移旧版 renderReplyMarkdown，先 escape 再替换） */
function renderReply(text: string): string {
  const esc = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return esc
    .replace(/\*\*([^*\n]+)\*\*/g, "<b>$1</b>")
    .replace(/\n\n+/g, "<br><br>")
    .replace(/\n/g, "<br>");
}

export default function MessageView({ msg }: { msg: ChatMsg }) {
  const streaming = msg.state === "streaming";
  const confirmCreateTask = useChatStore((s) => s.confirmCreateTask);

  if (msg.role === "user") {
    return (
      <div className="msg msg-user">
        <div className="avatar">你</div>
        <div className="bubble">
          {msg.content.split("\n").map((l, i) => (
            <p key={i}>{l}</p>
          ))}
        </div>
      </div>
    );
  }

  // ---- AI 消息 ----
  const mockTag = msg.source === "mock" ? <span className="mock-tag">(演示模式)</span> : null;

  return (
    <div className="msg msg-ai">
      <div className="avatar">AI{mockTag}</div>
      <div className="bubble chat-bubble">
        <div className="bubble-title">AI 测试工程师</div>

        {msg.thinking && <ThinkingPanel text={msg.thinking} />}

        {streaming ? (
          <div className="reply-body streaming">
            {msg.content ? (
              <span dangerouslySetInnerHTML={{ __html: renderReply(msg.content) }} />
            ) : (
              <span className="thinking-dots">
                <span />
                <span />
                <span />
              </span>
            )}
            <span className="type-cursor" />
          </div>
        ) : msg.state === "error" ? (
          <div className="reply-body msg-error">⚠ {msg.error || "生成失败"}</div>
        ) : (
          <>
            <div className="reply-body" dangerouslySetInnerHTML={{ __html: renderReply(msg.content) }} />
            {msg.state === "stopped" && <div className="msg-stopped">（已停止生成）</div>}
          </>
        )}

        {/* 任务卡（消息升级后） */}
        {msg.task && <TaskStepsCard task={msg.task} />}

        {/* 回复完成且未消费草稿 → 生成用例确认按钮 */}
        {msg.state === "done" && !msg.task && msg.draft && (
          <div className="quick-row">
            <button
              className="qtag confirm-btn"
              type="button"
              onClick={() => void confirmCreateTask(msg.id)}
            >
              ✨ 生成测试用例
            </button>
            <button
              className="qtag"
              type="button"
              onClick={() => toast("可在输入框继续补充规则后发送")}
            >
              继续补充
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
