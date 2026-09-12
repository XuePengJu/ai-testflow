/**
 * 设置页 store（zustand）：LLM 配置 + 生效模型（M4）。
 * 无轮询——进入页面拉一次，操作后定向刷新。
 * platform 模式数据由 AdminPage 自行拉取（同接口，user_id=0 视角）。
 */
import { create } from "zustand";
import { api, apiJson, API, toast } from "../api/client";
import { getAuthSnapshot } from "../contexts/authState";
import type { LLMConfigRow, LLMEffective, ProviderMap } from "../types";

interface SettingsState {
  providers: ProviderMap;
  providersLoaded: boolean;
  myConfigs: LLMConfigRow[];
  configsLoaded: boolean;
  effective: LLMEffective | null;

  loadProviders: () => Promise<void>;
  loadMyConfigs: () => Promise<void>;
  loadEffective: () => Promise<void>;
  /** 保存配置（mode=personal|platform），成功后刷新对应数据 */
  saveConfig: (
    mode: "personal" | "platform",
    body: { slot: string; provider: string; base_url: string; model: string; api_key: string | null },
  ) => Promise<boolean>;
  /** 删除配置槽 */
  deleteConfig: (mode: "personal" | "platform", slot: string) => Promise<boolean>;
  /** 连通测试（表单值，不落库） */
  testConfig: (body: { provider: string; base_url: string; model: string; api_key: string }) => Promise<{
    ok: boolean;
    error?: string;
    latency_ms?: number;
  } | null>;
  /** 测当前生效模型（服务端解析，无需 Key） */
  testDefault: (slot: string) => Promise<{
    ok: boolean;
    error_label?: string;
    model?: string | null;
    latency_ms?: number;
  } | null>;
  reset: () => void;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  providers: {},
  providersLoaded: false,
  myConfigs: [],
  configsLoaded: false,
  effective: null,

  async loadProviders() {
    if (get().providersLoaded) return;
    const p = await apiJson<ProviderMap>(API + "/llm/providers");
    if (p) set({ providers: p, providersLoaded: true });
  },

  async loadMyConfigs() {
    const snap = getAuthSnapshot();
    if (!snap.token) return;
    const rows = await apiJson<LLMConfigRow[]>(API + "/llm/config");
    if (rows) set({ myConfigs: Array.isArray(rows) ? rows : [], configsLoaded: true });
  },

  async loadEffective() {
    const eff = await apiJson<LLMEffective>(API + "/llm/effective");
    if (eff) set({ effective: eff });
  },

  async saveConfig(mode, body) {
    const path = mode === "platform" ? "/llm/platform-config" : "/llm/config";
    const row = await apiJson<LLMConfigRow>(API + path, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    if (!row) return false;
    if (mode === "personal") {
      const rest = get().myConfigs.filter((c) => c.slot !== row.slot);
      set({ myConfigs: [...rest, row] });
    }
    await get().loadEffective();
    return true;
  },

  async deleteConfig(mode, slot) {
    const path = (mode === "platform" ? "/llm/platform-config/" : "/llm/config/") + slot;
    let r: Response;
    try {
      r = await api(API + path, { method: "DELETE" });
    } catch {
      toast("删除失败");
      return false;
    }
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        const d = (await r.json()) as { detail?: string };
        if (d?.detail) detail = d.detail;
      } catch { /* ignore */ }
      toast(detail);
      return false;
    }
    if (mode === "personal") {
      set({ myConfigs: get().myConfigs.filter((c) => c.slot !== slot) });
    }
    await get().loadEffective();
    return true;
  },

  async testConfig(body) {
    return apiJson<{ ok: boolean; error?: string; latency_ms?: number }>(API + "/llm/test", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async testDefault(slot) {
    return apiJson<{ ok: boolean; error_label?: string; model?: string | null; latency_ms?: number }>(
      API + `/llm/test-default/${slot}`,
      { method: "POST" },
    );
  },

  reset() {
    set({ providers: {}, providersLoaded: false, myConfigs: [], configsLoaded: false, effective: null });
  },
}));
