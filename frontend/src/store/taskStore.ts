/**
 * 任务列表 store（zustand）。
 * - 列表：5s 轮询 GET /api/tasks（走 api() 加密透明解密）
 * - 活跃任务：创建后 2s 轮询 GET /api/tasks/{id} 直到终态，回写 chatStore 消息步骤卡
 *   （平移旧版 startMsgPoll，TTL 3 分钟）
 */
import { create } from "zustand";
import { api, API, toast } from "../api/client";
import { getAuthSnapshot } from "../contexts/authState";
import type { Task } from "../types";

const LIST_INTERVAL_MS = 5000;
const ACTIVE_INTERVAL_MS = 2000;
const ACTIVE_TTL_ROUNDS = 90; // 2s × 90 = 3 分钟，与旧版一致

interface TaskState {
  tasks: Task[];
  listLoaded: boolean;
  /** 详情轮询中的任务（chat 步骤卡实时刷新） */
  activeIds: Set<string>;
  refreshing: boolean;

  refresh: () => Promise<void>;
  startPolling: (taskId: string) => void;
  stopPolling: (taskId: string) => void;
  deleteTask: (taskId: string) => Promise<void>;
}

/** 单个活跃任务的轮询循环 */
async function pollTask(taskId: string, onEnd: () => void): Promise<void> {
  for (let round = 0; round < ACTIVE_TTL_ROUNDS; round++) {
    await new Promise((r) => setTimeout(r, ACTIVE_INTERVAL_MS));
    try {
      const r = await api(API + "/tasks/" + taskId);
      if (!r.ok) continue;
      const t = (await r.json()) as Task;
      const { useChatStore } = await import("./chatStore");
      useChatStore.getState().updateMsgTask(taskId, t);
      if (t.status === "completed" || t.status === "failed") {
        toast(t.status === "completed" ? `任务完成：${t.name}（${t.cases_count} 个用例）` : `任务失败：${t.name}`);
        onEnd();
        return;
      }
    } catch {
      /* 单次失败继续轮询 */
    }
  }
  onEnd(); // TTL 到期停止
}

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],
  listLoaded: false,
  activeIds: new Set<string>(),
  refreshing: false,

  async refresh() {
    const snap = getAuthSnapshot();
    if (!snap.token) {
      set({ tasks: [], listLoaded: false });
      return;
    }
    if (get().refreshing) return;
    set({ refreshing: true });
    try {
      const r = await api(API + "/tasks");
      if (r.ok) {
        const list = (await r.json()) as Task[];
        set({ tasks: Array.isArray(list) ? list : [], listLoaded: true });
      }
    } catch {
      /* 静默：轮询下轮再试 */
    } finally {
      set({ refreshing: false });
    }
  },

  startPolling(taskId) {
    const active = new Set(get().activeIds);
    if (active.has(taskId)) return;
    active.add(taskId);
    set({ activeIds: active });
    void pollTask(taskId, () => {
      const cur = new Set(useTaskStore.getState().activeIds);
      cur.delete(taskId);
      set({ activeIds: cur });
      // 终态后刷一次列表
      void get().refresh();
    });
  },

  stopPolling(taskId) {
    const cur = new Set(get().activeIds);
    cur.delete(taskId);
    set({ activeIds: cur });
  },

  async deleteTask(taskId) {
    const r = await api(API + "/tasks/" + taskId, { method: "DELETE" }).catch(() => null);
    if (!r || !r.ok) {
      toast("删除任务失败");
      return;
    }
    toast("任务已删除");
    get().stopPolling(taskId);
    await get().refresh();
    const { useChatStore } = await import("./chatStore");
    useChatStore.getState().refreshConversations();
  },
}));

/** 列表轮询的启动/停止由 ChatLayout 组件管理（随登录态生命周期） */
export function startListPolling(): () => void {
  void useTaskStore.getState().refresh();
  const timer = window.setInterval(() => void useTaskStore.getState().refresh(), LIST_INTERVAL_MS);
  return () => window.clearInterval(timer);
}
