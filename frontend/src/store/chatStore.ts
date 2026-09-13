/**
 * 对话驱动核心 store（zustand）。
 * 平移旧前端全局变量范式：pendingDraft / chatMessages / currentConversationId /
 * chatStreamAbort / supplementTaskId → 单一 store 收口。
 *
 * 流式状态机：send() → user 消息 + streaming assistant 消息 → SSE delta 累积
 * → done/error/abort → done 后 AI 消息挂 draft（「生成测试用例」按钮消费）。
 */
import { create } from "zustand";
import { api, API, toast } from "../api/client";
import { sseStream } from "../api/sse";
import { getAuthSnapshot } from "../contexts/authState";
import type { ChatDraft, Conversation, Task } from "../types";

export interface ChatMsg {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking: string;
  /** 用户消息携带的附件名（气泡上单独显示 📎 徽标，不混进正文） */
  fileName?: string;
  /** AI 消息回复完成后的生成草稿（生成用例按钮消费，消费后置 null） */
  draft?: ChatDraft | null;
  /** 消息升级为任务后挂载（步骤卡渲染 + TaskList 定位） */
  task?: Task | null;
  state: "streaming" | "done" | "error" | "stopped";
  error?: string;
  /** 非终态提示（如「真实模型失败，已切换演示模式」），以黄色提示条展示，不改变消息状态 */
  notice?: string;
  /** LLM 来源：mock / deepseek 等 */
  source?: string;
}

export interface HistoryItem {
  role: string;
  content: string;
}

let seq = 0;
const nextId = (): string => `m${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** 去掉流式中残留在正文末尾的未闭合标签前缀（如 "<thi" / "</thinkin"），避免闪现半截标签 */
function stripPartialTag(s: string): string {
  const m = s.match(/<[^>]*$/);
  if (m && m.index !== undefined) {
    const frag = m[0].toLowerCase();
    const cands = ["<think>", "<thinking>", "</think>", "</thinking>"];
    if (cands.some((c) => c.startsWith(frag))) return s.slice(0, m.index).trimEnd();
  }
  return s;
}

/** 合并两路思考：上游独立字段（reasoning_content）+ 正文里切出的标签内容 */
export function mergeThink(streamThink: string, tagThink: string): string {
  return [streamThink.trim(), tagThink.trim()].filter(Boolean).join("\n\n");
}

/** 切分思考块：支持 <think>/<thinking> 标签、中文 思考...思考、英文 thinking 列表块 */
export function splitThink(text: string): { thinking: string; reply: string } {
  // 1) XML 标签 <think> / <thinking>；流式中可能尚未闭合 → 开标签之后的内容全归思考面板
  const tag = text.match(/<think(?:ing)?>([\s\S]*?)(<\/think(?:ing)?>|$)/i);
  if (tag) {
    const start = tag.index!;
    const closed = tag[2].slice(0, 2) === "</";
    const rest = text.slice(0, start) + (closed ? text.slice(start + tag[0].length) : "");
    return { thinking: tag[1].trim(), reply: stripPartialTag(rest.trim()) };
  }
  // 2) 中文标记： 思考 ... 思考
  let m = text.match(/ 思考([\s\S]*?)思考/);
  if (m) {
    return {
      thinking: m[1].trim(),
      reply: (text.slice(0, m.index!) + text.slice(m.index! + m[0].length)).trim(),
    };
  }
  // 3) 英文 thinking 独占一行 + 后续列表块（直到空行/结尾）
  m = text.match(/(?:^|\n)thinking\s*\n((?:[ \t]*[-*][^\n]*\n?)+)(?:\n\s*\n|$)/i);
  if (m) {
    return {
      thinking: m[1].trim(),
      reply: (text.slice(0, m.index!) + text.slice(m.index! + m[0].length)).trim(),
    };
  }
  return { thinking: "", reply: text };
}

interface ChatState {
  conversationId: string | null;
  conversations: Conversation[];
  messages: ChatMsg[];
  streaming: boolean;
  /** 消息流滚动定位：递增序号触发 ChatPanel scrollIntoView */
  focusSeq: number;
  focusTaskId: string | null;

  refreshConversations: () => Promise<void>;
  loadConversation: (id: string) => Promise<void>;
  newConversation: () => void;
  deleteConversation: (id: string) => Promise<void>;

  send: (text: string, draft: ChatDraft) => Promise<void>;
  stop: () => void;

  confirmCreateTask: (msgId: string) => Promise<void>;
  updateMsgTask: (taskId: string, task: Task) => void;
  focusTask: (taskId: string) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversationId: null,
  conversations: [],
  messages: [],
  streaming: false,
  focusSeq: 0,
  focusTaskId: null,

  async refreshConversations() {
    const snap = getAuthSnapshot();
    if (!snap.token) {
      set({ conversations: [] });
      return;
    }
    try {
      const r = await api(API + "/conversations");
      if (!r.ok) return;
      const list = (await r.json()) as Conversation[];
      set({ conversations: Array.isArray(list) ? list.slice(0, 50) : [] });
    } catch {
      /* 网络异常静默，侧栏下次轮询再刷 */
    }
  },

  async loadConversation(id) {
    set({ conversationId: id });
    try {
      const r = await api(API + "/conversations/" + id);
      if (!r.ok) return;
      const conv = (await r.json()) as Conversation;
      const messages: ChatMsg[] = (conv.messages || []).map((m) => ({
        id: "h" + m.id,
        role: m.role === "user" ? "user" : "assistant",
        content: m.content || m.task?.report?.summary || "",        thinking: m.thinking || "",
        // 历史消息带任务摘要时直接挂轻量任务对象（步骤卡渲染 cases_count/status）
        task: m.task
          ? ({
              id: m.task.id,
              name: m.task.name,
              kind: "",
              source_type: "",
              status: m.task.status,
              cases_count: m.task.cases_count || 0,
              duration_ms: 0,
              formats: "",
              steps: [],
            } as Task)
          : null,
        state: "done",
      }));
      set({ messages });
    } catch {
      /* 加载失败保持空消息，用户可重试切换 */
    }
  },

  newConversation() {
    set({ conversationId: null, messages: [], focusTaskId: null });
  },

  async deleteConversation(id) {
    const r = await api(API + "/conversations/" + id, { method: "DELETE" }).catch(() => null);
    if (!r || !r.ok) {
      toast("删除会话失败");
      return;
    }
    if (get().conversationId === id) get().newConversation();
    await get().refreshConversations();
    const { useTaskStore } = await import("./taskStore");
    useTaskStore.getState().refresh();
  },

  async send(text, draft) {
    const snap = getAuthSnapshot();
    if (!snap.token) {
      toast("请先登录或使用游客体验");
      return;
    }
    if (get().streaming) {
      toast("请先停止当前生成");
      return;
    }

    // 首次发送：先建会话，后续消息挂同一会话（平移旧版行为）
    if (!get().conversationId) {
      try {
        const r = await api(API + "/conversations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: (text || "新对话").slice(0, 40) }),
        });
        if (r.ok) {
          const c = (await r.json()) as Conversation;
          set({ conversationId: c.id });
        }
      } catch {
        /* 会话创建失败不阻断发送（消息不落会话，仍可生成） */
      }
    }

    const userMsg: ChatMsg = {
      id: nextId(), role: "user", content: text, thinking: "", state: "done",
      fileName: draft.file?.name,
    };
    const aiMsg: ChatMsg = { id: nextId(), role: "assistant", content: "", thinking: "", state: "streaming" };
    set({ messages: [...get().messages, userMsg, aiMsg], streaming: true });

    // history：done 消息去掉刚追加的 user（与旧版 chatMessages.slice(0,-1) 对齐；
    // streaming 的 aiMsg 因 state 未 done 被 filter 排除）；只传附件没打字的消息正文为空，
    // 滤掉避免把空串塞进模型上下文
    const history: HistoryItem[] = get()
      .messages.filter((m) => m.state === "done")
      .slice(0, -1)
      .filter((m) => m.content.trim())
      .map((m) => ({ role: m.role, content: m.content }));

    const aborter = new AbortController();
    (get as unknown as { _aborter?: AbortController | null })._aborter = aborter;

    const patchAi = (patch: Partial<ChatMsg>) => {
      const msgs = get().messages;
      const i = msgs.findIndex((m) => m.id === aiMsg.id);
      if (i === -1) return; // 流式中切换会话：消息已被替换，丢弃
      const next = msgs.slice();
      next[i] = { ...next[i], ...patch };
      set({ messages: next });
    };

    let fullText = "";
    let streamThink = ""; // 上游独立字段（reasoning_content）累积的思考内容
    try {
      // 有附件时先单独上传拿 file_id（SSE 流没法带 multipart），
      // 后端抽取文档文本后在对话时注入，AI 才能读到文档内容
      let fileId: string | undefined;
      if (draft.file) {
        const fd = new FormData();
        fd.append("file", draft.file);
        const up = await api(API + "/files", { method: "POST", body: fd });
        if (!up.ok) {
          const e = await up.text();
          patchAi({ state: "error", error: "附件上传失败：" + e.slice(0, 160) });
          return;
        }
        const meta = (await up.json()) as { file_id?: string };
        fileId = meta.file_id;
      }

      await sseStream(
        API + "/chat/stream",
        {
          message: text,
          history,
          conversation_id: get().conversationId,
          file_id: fileId,
          thinking: draft.thinking !== false,
        },
        aborter.signal,
        {
          onEvent(ev) {
            if (ev.event === "delta") {
              fullText += String(ev.data.content || "");
              const parsed = splitThink(fullText);
              patchAi({ content: parsed.reply, thinking: mergeThink(streamThink, parsed.thinking) });
            } else if (ev.event === "think") {
              // 上游独立字段的思考增量：直接进思考面板，不混进正文
              streamThink += String(ev.data.content || "");
              patchAi({ thinking: streamThink });
            } else if (ev.event === "notice") {
              // 降级/中断提示：只挂提示条，流继续（state 仍为 streaming，等 done 收尾）
              patchAi({ notice: String(ev.data.message || "") });
            } else if (ev.event === "error") {
              patchAi({ state: "error", error: String(ev.data.message || "未知错误") });
            } else if (ev.event === "done") {
              const full = typeof ev.data.full === "string" ? ev.data.full : "";
              // done 里的 thinking 与流式 think 事件同源，取其一避免重复拼接
              const doneThink = typeof ev.data.thinking === "string" ? ev.data.thinking : "";
              const thinkBase = streamThink || doneThink;
              if (full && !fullText) fullText = full;
              const parsed = splitThink(fullText);
              if (thinkBase || parsed.thinking) {
                patchAi({ thinking: mergeThink(thinkBase, parsed.thinking) });
              }
              if (parsed.reply) patchAi({ content: parsed.reply });
              patchAi({
                state: "done",
                source: typeof ev.data.source === "string" ? ev.data.source : undefined,
                draft: { ...draft, text: draft.text || text },
              });
            }
          },
        },
      );
      // 流正常结束但没收到 done 事件（连接中断兜底）
      const cur = get().messages.find((m) => m.id === aiMsg.id);
      if (cur && cur.state === "streaming") {
        patchAi(cur.content ? { state: "stopped" } : { state: "error", error: "连接中断" });
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        patchAi({ state: "stopped" });
      } else {
        patchAi({ state: "error", error: e instanceof Error ? e.message : String(e) });
      }
    } finally {
      (get as unknown as { _aborter?: AbortController | null })._aborter = null;
      set({ streaming: false });
      get().refreshConversations(); // 后端已落库，刷新 message_count/排序
    }
  },

  stop() {
    const aborter = (get as unknown as { _aborter?: AbortController | null })._aborter;
    if (aborter) aborter.abort();
  },

  async confirmCreateTask(msgId) {
    const { useTaskStore } = await import("./taskStore");
    const msg = get().messages.find((m) => m.id === msgId);
    if (!msg) return;
    const draft = msg.draft;
    if (!draft) {
      toast("没有待生成的需求，请重新输入");
      return;
    }
    // 平移旧版 confirmAndCreateTask：把完整对话作为任务文本，上下文更充分
    const conversationText = get()
      .messages.filter((m) => m.role === "user")
      .map((m) => `用户：${m.content}`)
      .join("\n\n");
    const text = (draft.text || conversationText).trim();
    if (!text && !draft.file) {
      toast("请上传文件或粘贴规格文本");
      return;
    }

    const fd = new FormData();
    if (draft.file) fd.append("file", draft.file);
    fd.append("text", text);
    fd.append("kind", draft.kind || "business");
    fd.append("formats", (draft.formats.length ? draft.formats : ["xlsx"]).join(","));
    fd.append("name", text.slice(0, 40) || "未命名任务");
    fd.append("conversation_id", get().conversationId || "");

    try {
      const r = await api(API + "/tasks", { method: "POST", body: fd });
      if (!r.ok) {
        const e = await r.text();
        toast("提交失败：" + e.slice(0, 200));
        return;
      }
      const task = (await r.json()) as Task;
      // 消息升级：草稿已消费 + 挂载任务（步骤卡轮询渲染）
      const msgs = get().messages.slice();
      const i = msgs.findIndex((m) => m.id === msgId);
      if (i !== -1) {
        msgs[i] = { ...msgs[i], draft: null, task };
        set({ messages: msgs });
      }
      toast("任务已提交，正在编排生成");
      useTaskStore.getState().startPolling(task.id);
      get().refreshConversations();
      useTaskStore.getState().refresh();
    } catch (e) {
      toast("网络错误：" + (e instanceof Error ? e.message : String(e)));
    }
  },

  updateMsgTask(taskId, task) {
    const msgs = get().messages;
    const i = msgs.findIndex((m) => m.task && m.task.id === taskId);
    if (i === -1) return;
    const next = msgs.slice();
    next[i] = { ...next[i], task };
    set({ messages: next });
  },

  focusTask(taskId) {
    set({ focusTaskId: taskId, focusSeq: get().focusSeq + 1 });
  },
}));
