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

/** 允许上传的文档格式（与后端 doc_extract.SUPPORTED_EXTS 保持一致） */
const ACCEPT = ".docx,.pdf,.md,.markdown,.txt";
const ACCEPT_HINT = "docx / pdf / md / txt";
const ACCEPT_RE = /\.(docx|pdf|md|markdown|txt)$/i;

const SAMPLE =
  "采购管理 - 采购订单创建。\n功能点：新增采购单、编辑未提交单据、提交审批、审批通过/驳回、删除草稿、按供应商/日期查询。\n业务规则：提交后不可编辑；金额超 5 万需二级审批；供应商需为有效状态。";

/** 「深度思考」开关的本地记忆键（默认开） */
const THINK_KEY = "aitf_deep_think";

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
  /** 深度思考开关：默认开，本地记忆（关掉则不请求模型思考，也不显示思考面板） */
  const [deepThink, setDeepThink] = useState<boolean>(() => {
    try {
      return localStorage.getItem(THINK_KEY) !== "0";
    } catch {
      return true;
    }
  });
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
    el.style.height = Math.min(220, el.scrollHeight) + "px";
  }

  function doSend(): void {
    const txt = text.trim();
    if (!txt && !file) return;
    if (streaming) return;
    const draft: ChatDraft = { text: txt, file, kind, formats, thinking: deepThink };
    // 只传附件不打字时正文保持为空（气泡显示 📎 文件名徽标），不再写「(仅附加文档)」占位符
    void send(txt, draft);
    setText("");
    setFile(null);
    if (fileRef.current) fileRef.current.value = "";
    stickBottom.current = true;
  }

  /** 选择附件：前端先按白名单拦一道，与后端 doc_extract 支持的格式保持一致 */
  function onPickFile(f: File | null): void {
    if (f && !ACCEPT_RE.test(f.name)) {
      toast(`暂不支持该格式，请上传 ${ACCEPT_HINT}`);
      if (fileRef.current) fileRef.current.value = "";
      setFile(null);
      return;
    }
    setFile(f);
  }

  /** 切换深度思考：写本地记忆，下一次发送即生效 */
  function toggleThink(): void {
    const next = !deepThink;
    setDeepThink(next);
    try {
      localStorage.setItem(THINK_KEY, next ? "1" : "0");
    } catch {
      /* 隐私模式 / 存储禁用：仅本次会话生效 */
    }
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
            accept={ACCEPT}
            onChange={(e) => onPickFile(e.target.files?.[0] || null)}
          />
          <button
            className="icon-btn"
            type="button"
            title={`附加文档（${ACCEPT_HINT}），AI 会读取文档内容`}
            disabled={streaming}
            onClick={() => fileRef.current?.click()}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          </button>
          <button
            className={`think-toggle ${deepThink ? "active" : ""}`}
            type="button"
            aria-pressed={deepThink}
            title={
              deepThink
                ? "深度思考：已开启 —— 模型会先推理再作答（点击关闭）"
                : "深度思考：已关闭 —— 直接作答，不展示思考过程（点击开启）"
            }
            disabled={streaming}
            onClick={toggleThink}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2z"/><path d="M9.5 21h5"/></svg>
            <span className="tt-text">深度思考</span>
          </button>
          <div className="input-pill">
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
          </div>
          {streaming ? (
            <button className="send-btn streaming" type="button" onClick={stop}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>
              停止生成
            </button>
          ) : (
            <button className="send-btn" type="button" onClick={doSend} disabled={!text.trim() && !file}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              发送
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
