/**
 * 对话驱动主面板：消息流 + 输入区。
 * 平移旧版 chatStream/chatText/chatSendBtn 行为：
 * - Enter 发送 / Shift+Enter 换行 / textarea 自适应高度
 * - 流式中发送按钮变「⏹ 停止生成」
 * - 文件附加 chip + 移除
 * - 新消息/流式增量自动滚底（用户上滚时暂停跟随）
 * V4：图标改用 lucide-react（纸飞机/灯泡/附件/停止）。
 * V2.10：输入框为唯一入口 —— 挂载「迭代引用 chip」时本次发送走 iterate（基于旧任务合并用例），
 *        无 chip 时为新建任务；chip 由详情页「继续优化」或会话内任务卡挂载。
 */
import { useEffect, useRef, useState } from "react";
import { Bot, Paperclip, Lightbulb, Send, Square, FileUp, ClipboardList, Sparkles, Globe } from "lucide-react";
import { useChatStore } from "../../store/chatStore";
import { toast } from "../../api/client";
import type { ChatDraft } from "../../types";
import MessageView from "./MessageView";
import E2ETaskModal from "./E2ETaskModal";

function fmtSize(b: number): string {
  return b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(2) + " MB";
}

/** 允许上传的文档格式（与后端 doc_extract.SUPPORTED_EXTS 保持一致） */
const ACCEPT = ".docx,.pdf,.md,.markdown,.txt";
const ACCEPT_HINT = "docx / pdf / md / txt";
const ACCEPT_RE = /\.(docx|pdf|md|markdown|txt)$/i;

/** 空态示例 chips 的示例需求（点选直接填入输入框） */
const SAMPLE_ECOM =
  "电商订单流程。\n功能点：下单、支付、取消订单、申请退款、订单状态流转、按订单号/状态查询。\n业务规则：超时未支付自动取消；已发货订单不可取消；退款需审核。";
const SAMPLE_LOGIN =
  "用户登录注册。\n功能点：注册、登录、找回密码、验证码、记住登录态、退出登录。\n业务规则：密码强度校验；连续 5 次错误锁定 10 分钟；验证码 5 分钟有效。";
const SAMPLE_DBERP =
  "DBERP 采购入库。\n功能点：创建采购入库单、关联采购订单、质检、上架、库存更新、单据查询。\n业务规则：入库数量不可超采购数量；质检不合格可退货；库存实时扣减。";

/** 「总是深度思考」开关的本地记忆键（默认关＝按需：简单问题直接答，复杂问题由后端自动推理） */
const THINK_KEY = "aitf_deep_think";

/** 多角色协作（V3.1）：参与生成用例的视角（与后端 src/generator.case_generator 对齐） */
const ROLE_OPTIONS: { id: string; label: string; title: string }[] = [
  { id: "pm", label: "产品", title: "产品视角：业务价值/需求覆盖/验收标准" },
  { id: "qa", label: "测试", title: "测试视角：正向/异常/边界/场景组合" },
  { id: "dev", label: "开发", title: "开发视角：契约/幂等/并发/数据一致性" },
];

export default function ChatPanel({
  showCitations = false,
  onCiteClick,
  kbName,
  suggests,
}: {
  /** V4.1：知识库问答传 true → 渲染引用溯源 chips；首页不传，界面零变化 */
  showCitations?: boolean;
  /** 引用 chip 点击回调（知识库页切 tab + 高亮定位） */
  onCiteClick?: (knowledgeId: string) => void;
  /** V4.1：知识库问答模式下欢迎语展示的库名 */
  kbName?: string;
  /** V4.2：知识库问答的建议问题（来自该库 Wiki 索引标题），点击填入输入框 */
  suggests?: string[];
}) {
  const chatMode = useChatStore((s) => s.chatMode);
  const kbMode = chatMode === "kb_qa";
  const messages = useChatStore((s) => s.messages);
  const conversationId = useChatStore((s) => s.conversationId);
  const streamingByConversation = useChatStore((s) => s.streamingByConversation);
  // 按会话隔离的流式状态：当前会话在输出中才禁用输入框，其他会话不受影响
  const streaming = conversationId ? (streamingByConversation[conversationId] ?? false) : false;
  const send = useChatStore((s) => s.send);
  const stop = useChatStore((s) => s.stop);
  const focusSeq = useChatStore((s) => s.focusSeq);
  const focusTaskId = useChatStore((s) => s.focusTaskId);
  /** 迭代引用：非空 → 迭代沟通模式（先沟通，点「生成用例」才生成） */
  const iterTaskId = useChatStore((s) => s.iterTaskId);
  const iterTaskName = useChatStore((s) => s.iterTaskName);
  const iterNotes = useChatStore((s) => s.iterNotes);
  const iterGenerating = useChatStore((s) => s.iterGenerating);
  const requestIterate = useChatStore((s) => s.requestIterate);
  const clearIterRef = useChatStore((s) => s.clearIterRef);
  const inputFocusSeq = useChatStore((s) => s.inputFocusSeq);

  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [kind] = useState("business");
  const [formats] = useState<string[]>(["xlsx", "json", "xmind"]);
  /** 多角色协作（V3.1）：参与生成用例的视角（pm/qa/dev），默认仅测试 */
  const [roles, setRoles] = useState<string[]>(["qa"]);
  /** 「总是深度思考」：默认关＝按需 —— 简单问题直接答，复杂问题（排查/分析/报错等）
   *  由后端自动判定是否推理。开启后每轮都先推理再作答。本地记忆，未表态时后端走自动判定。 */
  const [alwaysThink, setAlwaysThink] = useState<boolean>(() => {
    try {
      return localStorage.getItem(THINK_KEY) === "1";
    } catch {
      return false;
    }
  });
  const streamRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  /** 用户上滚后暂停自动跟随，回到底部恢复 */
  const stickBottom = useRef(true);
  /** M1 全链路测试发起弹窗 */
  const [e2eOpen, setE2eOpen] = useState(false);

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

  // 迭代入口聚焦：详情页「继续优化」跳回会话后自动聚焦输入框（inputFocusSeq 递增触发）
  useEffect(() => {
    if (!inputFocusSeq) return;
    inputRef.current?.focus();
  }, [inputFocusSeq]);

  function onScroll(): void {
    const el = streamRef.current;
    if (!el) return;
    stickBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  }

  function autoGrow(el: HTMLTextAreaElement): void {
    el.style.height = "auto";
    el.style.height = Math.min(320, el.scrollHeight) + "px";
  }

  function doSend(): void {
    const txt = text.trim();
    if (!txt && !file) return;
    if (streaming) return;
    // V4.2.2：kb_qa 模式不传 roles——避免 QA 人设污染知识问答（回答不再带用例生成话术）
    // thinking：true=总是深度思考；null=未表态 → 后端按需自动判定（llm_service.should_deep_think）
    const draft: ChatDraft = {
      text: txt,
      file,
      kind,
      formats,
      thinking: alwaysThink ? true : null,
      roles: kbMode ? undefined : roles,
    };
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

  /** 切换「总是深度思考」：写本地记忆，下一次发送即生效 */
  function toggleThink(): void {
    const next = !alwaysThink;
    setAlwaysThink(next);
    try {
      localStorage.setItem(THINK_KEY, next ? "1" : "0");
    } catch {
      /* 隐私模式 / 存储禁用：仅本次会话生效 */
    }
  }

  /** 多角色协作：切换某角色参与生成（至少保留一个角色） */
  function toggleRole(id: string): void {
    setRoles((prev) => {
      const next = prev.includes(id) ? prev.filter((r) => r !== id) : [...prev, id];
      return next.length ? next : ["qa"];
    });
  }

  return (
    <div className="chat-panel">
      <div className="chat-stream" ref={streamRef} onScroll={onScroll}>
        {messages.length === 0 ? (
          <div className="welcome">
            <h2>
              <Bot size={24} style={{ verticalAlign: "-4px", marginRight: 6 }} />
              {kbMode ? (
                <>Hi，我是<em style={{ fontStyle: "normal", color: "var(--itf-p600, #4f46e5)" }}>{kbName || "知识库"}助手</em></>
              ) : (
                "我是 Buddy"
              )}
            </h2>
            {kbMode ? (
              <p>
                基于{kbName ? `「${kbName}」` : "当前知识库"}回答问题，回答附引用来源，点引用可跳转到原文。
              </p>
            ) : (
              <p>把你的测试需求告诉我，我来拆解需求、生成用例、质量校验、导出文件。</p>
            )}

            {/* V4.2：建议问题（设计稿 .suggest）—— 来自该库 Wiki 索引，点击填入输入框 */}
            {kbMode && !!suggests?.length && (
              <div className="kb-suggest">
                {suggests.map((s) => (
                  <button key={s} className="kb-sug" type="button" onClick={() => { setText(s); inputRef.current?.focus(); }}>
                    {s}
                  </button>
                ))}
              </div>
            )}

            {!kbMode && (
              <>
            <div className="quick-cards">
              <button className="quick-card" type="button" onClick={() => fileRef.current?.click()}>
                <FileUp size={18} />
                <span className="qc-title">上传文档</span>
                <span className="qc-desc">上传需求文档，AI 自动读取</span>
              </button>
              <button className="quick-card" type="button" onClick={() => inputRef.current?.focus()}>
                <ClipboardList size={18} />
                <span className="qc-title">输入场景</span>
                <span className="qc-desc">直接描述你的业务场景</span>
              </button>
              <button className="quick-card" type="button" onClick={() => setE2eOpen(true)}>
                <Globe size={18} />
                <span className="qc-title">全链路测试</span>
                <span className="qc-desc">输入网址自动抓取生成用例</span>
              </button>
              <button className="quick-card" type="button" onClick={() => setText(SAMPLE_ECOM)}>
                <Sparkles size={18} />
                <span className="qc-title">查看示例</span>
                <span className="qc-desc">点下方示例一键填入</span>
              </button>
            </div>

            <div className="sample-chips">
              <span className="sc-label">试试这些示例：</span>
              <button className="sample-chip" type="button" onClick={() => setText(SAMPLE_ECOM)}>电商订单流程</button>
              <button className="sample-chip" type="button" onClick={() => setText(SAMPLE_LOGIN)}>用户登录注册</button>
              <button className="sample-chip" type="button" onClick={() => setText(SAMPLE_DBERP)}>DBERP 采购入库</button>
            </div>
              </>
            )}
          </div>
        ) : (
          messages.map((m) => (
            <MessageView key={m.id} msg={m} showCitations={showCitations} onCiteClick={onCiteClick} />
          ))
        )}
      </div>

      <div className={`chat-input-wrap ${streaming ? "streaming" : ""} ${iterTaskId ? "iter-mode" : ""}`}>
        {/* 迭代引用 chip：常驻可见，明确告知本次发送是「迭代旧任务」而非新建；✕ 一键回到新建模式 */}
        {iterTaskId && (
          <div className="iter-ref-chip">
            <span className="irc-icon" aria-hidden="true">🔁</span>
            <span className="irc-text">基于《{iterTaskName}》迭代</span>
            <span className="irc-hint">
              {iterNotes.length ? `已记录 ${iterNotes.length} 条补充要求` : "先沟通补充方向"}
            </span>
            <button
              type="button"
              className="irc-gen"
              disabled={iterGenerating || streaming}
              title="按沟通确认的补充要求生成用例（合并原有用例，版本号 +1）"
              onClick={() => void requestIterate()}
            >
              {iterGenerating ? "生成中…" : "⚡ 生成用例"}
            </button>
            <button
              type="button"
              className="irc-close"
              title="取消迭代，恢复为新建任务模式"
              aria-label="取消迭代引用"
              onClick={clearIterRef}
            >
              ✕
            </button>
          </div>
        )}
        {file && (
          <div className="file-chip">
            <Paperclip size={14} /> {file.name} <span className="fc-size">({fmtSize(file.size)})</span>
            <button type="button" onClick={() => { setFile(null); if (fileRef.current) fileRef.current.value = ""; }}>
              移除
            </button>
          </div>
        )}
        <div className="chat-input-row">
          <div className="input-toolbar">
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
              title="🌐 全链路测试：输入网址，AI 自动抓取页面并生成用例"
              disabled={streaming}
              onClick={() => setE2eOpen(true)}
            >
              <Globe size={20} />
            </button>
            <button
              className="icon-btn"
              type="button"
              title={`附加文档（${ACCEPT_HINT}），AI 会读取文档内容`}
              disabled={streaming}
              onClick={() => fileRef.current?.click()}
            >
              <Paperclip size={20} />
            </button>
            <button
              className={`think-toggle ${alwaysThink ? "active" : ""}`}
              type="button"
              aria-pressed={alwaysThink}
              title={
                alwaysThink
                  ? "总是深度思考：已开启 —— 每轮都会先推理再作答（点击改为按需）"
                  : "按需深度思考：简单问题直接作答，复杂问题（排查/分析/报错等）自动推理（点击改为每轮都思考）"
              }
              disabled={streaming}
              onClick={toggleThink}
            >
              <Lightbulb size={16} />
              <span className="tt-text">总是深度思考</span>
            </button>
            {/* 角色选择器仅工作流模式展示：RAG 问答与用例视角无关（V4.2.2） */}
            {!kbMode && (
              <span className="role-picker" title="多角色协作：以多个视角分别生成用例后合并去重">
                {ROLE_OPTIONS.map((r) => (
                  <button
                    key={r.id}
                    className={`role-chip ${roles.includes(r.id) ? "active" : ""}`}
                    type="button"
                    title={r.title}
                    disabled={streaming}
                    onClick={() => toggleRole(r.id)}
                  >
                    {r.label}
                  </button>
                ))}
              </span>
            )}
            {/* V4.2：检索范围 pill（设计稿 .scope）—— kb_qa 固定检索当前库，状态展示 */}
            {kbMode && (
              <span className="kb-scope" title="知识库问答固定检索当前知识库">
                📚 检索范围：{kbName || "当前知识库"}
              </span>
            )}
          </div>
          <div className="input-body">
            <textarea
              ref={inputRef}
              value={text}
              placeholder={
                streaming
                  ? "生成中…"
                  : iterTaskId
                    ? `和 Buddy 沟通《${iterTaskName}》要补充什么…（确认后点「⚡ 生成用例」）`
                    : kbMode
                      ? `基于${kbName ? "「" + kbName + "」" : "知识库"}提问，如“退货超过5000元怎么处理？”…`
                      : "把你的测试需求告诉 Buddy…"
              }
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
              <button className="send-btn streaming" type="button" onClick={stop} title="停止生成">
                <Square size={16} fill="currentColor" />
              </button>
            ) : (
              <button className="send-btn" type="button" onClick={doSend} disabled={!text.trim() && !file} title="发送">
                <Send size={18} />
              </button>
            )}
          </div>
        </div>
      </div>
      {/* M1 全链路测试发起弹窗 */}
      <E2ETaskModal open={e2eOpen} onClose={() => setE2eOpen(false)} />
    </div>
  );
}
