/**
 * 对话驱动主面板：消息流 + 输入区。
 * 平移旧版 chatStream/chatText/chatSendBtn 行为：
 * - Enter 发送 / Shift+Enter 换行 / textarea 自适应高度
 * - 流式中发送按钮变「⏹ 停止生成」
 * - 文件附加 chip + 移除
 * - 新消息/流式增量自动滚底（用户上滚时暂停跟随）
 */
import { useEffect, useRef, useState } from "react";
import { useChatStore } from "../../store/chatStore";
import { toast } from "../../api/client";
import type { ChatDraft } from "../../types";
import MessageView from "./MessageView";

function fmtSize(b: number): string {
  return b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(2) + " MB";
}

const SAMPLE =
  "采购管理 - 采购订单创建。\n功能点：新增采购单、编辑未提交单据、提交审批、审批通过/驳回、删除草稿、按供应商/日期查询。\n业务规则：提交后不可编辑；金额超 5 万需二级审批；供应商需为有效状态。";

export default function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const streaming = useChatStore((s) => s.streaming);
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);
  const focusSeq = useChatStore((s) => s.focusSeq);
  const focusTaskId = useChatStore((s) => s.focusTaskId);

  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [kind] = useState("business");
  const [formats] = useState<string[]>(["xlsx"]);
  const streamRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  /** 用户上滚后暂停自动跟随，回到底部恢复 */
  const stickBottom = useRef(true);

  // 自动滚底：消息变化 + 流式增量
  useEffect(() => {
    const el = streamRef.current;
    if (el && stickBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  // 任务定位：TaskList 点击 → 滚动到对应任务卡并高亮
  useEffect(() => {
    if (!focusSeq) return;
    const el = streamRef.current;
    if (!el) return;
    const target = focusTaskId ? el.querySelector(`[data-task-card="${focusTaskId}"]`) : null;
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("flash");
      setTimeout(() => target.classList.remove("flash"), 1600);
    } else if (focusTaskId) {
      // 当前消息流没有该任务的卡（如已切到新会话）→ 提示而非静默
      toast("该任务不在当前会话，可切换到对应会话查看");
    }
  }, [focusSeq, focusTaskId]);

  function onScroll(): void {
    const el = streamRef.current;
    if (!el) return;
    stickBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }

  function autoGrow(el: HTMLTextAreaElement): void {
    el.style.height = "auto";
    el.style.height = Math.min(140, el.scrollHeight) + "px";
  }

  function doSend(): void {
    const txt = text.trim();
    if (!txt && !file) return;
    if (streaming) return;
    const draft: ChatDraft = { text: txt, file, kind, formats };
    void send(txt || "(仅附加文档)", draft);
    setText("");
    setFile(null);
    if (fileRef.current) fileRef.current.value = "";
    stickBottom.current = true;
  }

  return (
    <div className="chat-panel">
      <div className="chat-stream" ref={streamRef} onScroll={onScroll}>
        {messages.length === 0 ? (
          <div className="welcome">
            <h2>👋 我是 Buddy</h2>
            <p>把你的测试需求告诉我，我来拆解需求、生成用例、质量校验、导出文件。</p>
            <button className="qtag" type="button" onClick={() => setText(SAMPLE)}>
              填入示例业务需求
            </button>
          </div>
        ) : (
          messages.map((m) => <MessageView key={m.id} msg={m} />)
        )}
      </div>

      <div className={`chat-input-wrap ${streaming ? "streaming" : ""}`}>
        {file && (
          <div className="file-chip">
            📎 {file.name} <span className="fc-size">({fmtSize(file.size)})</span>
            <button type="button" onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = ""; }}>
              移除
            </button>
          </div>
        )}
        <div className="chat-input-row">
          <input
            ref={fileRef}
            type="file"
            hidden
            onChange={(e) => setFile(e.target.files?.[0] || null)}
          />
          <button
            className="icon-btn"
            type="button"
            title="附加文档（docx/pdf/xlsx/txt）"
            disabled={streaming}
            onClick={() => fileRef.current?.click()}
          >
            📎
          </button>
          <textarea
            ref={inputRef}
            value={text}
            placeholder={streaming ? "生成中…" : "把你的测试需求告诉 Buddy…"}
            disabled={streaming}
            onChange={(e) => {
              setText(e.target.value);
              autoGrow(e.target);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                doSend();
              }
            }}
          />
          {streaming ? (
            <button className="send-btn streaming" type="button" onClick={stop}>
              ⏹ 停止生成
            </button>
          ) : (
            <button className="send-btn" type="button" onClick={doSend} disabled={!text.trim() && !file}>
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
