/**
 * V5.4 全局 UI 状态：跨组件的浮层开关。
 * 首个用途：全链路测试弹窗（E2ETaskModal）—— 入口从聊天输入框小图标提升到
 * 左侧 rail「工作区」组（App.tsx），与 ChatPanel 共享同一个开合状态。
 */
import { create } from "zustand";

interface UiState {
  e2eModalOpen: boolean;
  openE2eModal: () => void;
  closeE2eModal: () => void;
}

export const useUiStore = create<UiState>((set) => ({
  e2eModalOpen: false,
  openE2eModal: () => set({ e2eModalOpen: true }),
  closeE2eModal: () => set({ e2eModalOpen: false }),
}));
