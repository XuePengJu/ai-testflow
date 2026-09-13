/**
 * LLM 模型配置卡（M4）：双槽（text/vision）× 双模式（personal/platform）复用。
 *
 * 语义对齐后端 LLMConfigIn：
 * - api_key 留空提交 = 保留已存 Key（脱敏回显 ****xxxx 提示）
 * - 厂商预设选中 → base_url 自动带出（可改），model 用 datalist 建议 + 自由输入
 * - 免费厂商（modelscope/zhipu*）不填 Key → 服务端环境变量兜底
 * - 「测试」走 POST /llm/test（表单值，不落库；Key 留空复用已保存）
 * V4：按钮规范化（保存 primary / 测试 secondary / 删除 outline-danger）；select 加自定义箭头。
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronDown } from "lucide-react";
import { toast } from "../../api/client";
import { useSettingsStore } from "../../store/settingsStore";
import type { LLMConfigRow } from "../../types";

const SLOT_TITLE: Record<string, { title: string; sub: string }> = {
  text: { title: "默认文本模型", sub: "需求拆解 / 用例生成 / 对话" },
  vision: { title: "图像识别模型", sub: "原型图 / 截图转用例（可选）" },
};

interface Props {
  slot: "text" | "vision";
  mode: "personal" | "platform";
  /** 该槽当前已保存配置（personal 用 store；platform 由 AdminPage 传入） */
  saved: LLMConfigRow | undefined;
  /** platform 模式：保存/删除后刷新 AdminPage 数据 */
  onSaved?: () => void;
}

export default function LLMConfigCard({ slot, mode, saved, onSaved }: Props) {
  const providers = useSettingsStore((s) => s.providers);
  const loadProviders = useSettingsStore((s) => s.loadProviders);
  const saveConfig = useSettingsStore((s) => s.saveConfig);
  const deleteConfig = useSettingsStore((s) => s.deleteConfig);
  const testConfig = useSettingsStore((s) => s.testConfig);

  const [provider, setProvider] = useState(saved?.provider || "bailian");
  const [baseUrl, setBaseUrl] = useState(saved?.base_url || "");
  const [model, setModel] = useState(saved?.model || "");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // providers 懒加载（进入设置页首个配置卡触发，store 内有 loaded 去重）
  useEffect(() => {
    void loadProviders();
  }, [loadProviders]);
  // saved 数据晚于组件挂载到达（如 personal 从 store 拉取）→ 未被用户编辑过时回填一次
  const [touched, setTouched] = useState(false);
  const [prevSaved, setPrevSaved] = useState<LLMConfigRow | undefined>(undefined);
  if (saved !== prevSaved) {
    setPrevSaved(saved);
    if (!touched) {
      if (saved) {
        setProvider(saved.provider);
        setBaseUrl(saved.base_url);
        setModel(saved.model);
      }
    }
  }

  const preset = providers[provider];
  const models = useMemo(() => preset?.models || [], [preset]);

  const onProviderChange = (p: string) => {
    setProvider(p);
    const pre = providers[p];
    if (pre) setBaseUrl(pre.base_url);
    setTestMsg(null);
    setTouched(true);
  };

  const save = async () => {
    if (!baseUrl.trim() || !model.trim()) {
      toast("Base URL 与模型不能为空");
      return;
    }
    setBusy(true);
    const ok = await saveConfig(mode, {
      slot,
      provider,
      base_url: baseUrl.trim(),
      model: model.trim(),
      api_key: apiKey.trim() ? apiKey.trim() : null, // null = 保留原 Key
    });
    setBusy(false);
    if (ok) {
      toast("配置已保存");
      setApiKey("");
      setTouched(false);
      onSaved?.();
    }
  };

  const del = async () => {
    if (!window.confirm(`确定删除${mode === "platform" ? "平台" : "我的"}「${SLOT_TITLE[slot].title}」配置？`)) return;
    setBusy(true);
    const ok = await deleteConfig(mode, slot);
    setBusy(false);
    if (ok) {
      toast("配置已删除");
      setTouched(false);
      onSaved?.();
    }
  };

  const test = async () => {
    if (!baseUrl.trim() || !model.trim()) {
      toast("请先填写 Base URL 与模型");
      return;
    }
    setTestMsg({ ok: true, text: "测试中…" });
    const r = await testConfig({
      provider,
      base_url: baseUrl.trim(),
      model: model.trim(),
      api_key: apiKey.trim(), // 留空 → 服务端复用已保存 Key
    });
    if (!r) {
      setTestMsg({ ok: false, text: "请求失败" });
      return;
    }
    setTestMsg(
      r.ok
        ? { ok: true, text: `✓ 连通正常${r.latency_ms != null ? ` · ${r.latency_ms}ms` : ""}` }
        : { ok: false, text: `✗ ${r.error || "调用失败"}` },
    );
  };

  const meta = SLOT_TITLE[slot];

  return (
    <section
      className={`llm-slot-card${mode === "platform" ? " platform" : ""}`}
      data-testid={`llm-card-${mode}-${slot}`}
    >
      <h4>
        {meta.title}
        {saved && <span className="slot-badge on">已配置 · {saved.api_key_masked || "平台 Key"}</span>}
        {!saved && <span className="slot-badge">未配置</span>}
      </h4>
      <div className="sub">{meta.sub}{mode === "platform" ? "（平台默认，未配置个人模型的所有用户生效）" : ""}</div>

      <label className="f-label">厂商预设</label>
      <div className="select-wrap">
        <select value={provider} onChange={(e) => onProviderChange(e.target.value)} data-testid={`provider-${mode}-${slot}`}>
          {Object.entries(providers).map(([id, p]) => (
            <option key={id} value={id}>{p.label}</option>
          ))}
        </select>
        <ChevronDown className="chevron" size={16} />
      </div>

      <label className="f-label">Base URL</label>
      <input
        value={baseUrl}
        onChange={(e) => { setBaseUrl(e.target.value); setTouched(true); }}
        placeholder="https://…（选预设自动带出，可改）"
        data-testid={`baseurl-${mode}-${slot}`}
      />

      <label className="f-label">模型</label>
      <input
        value={model}
        onChange={(e) => { setModel(e.target.value); setTouched(true); }}
        placeholder={slot === "vision" ? "如 qwen-vl-plus（需支持图像）" : "如 qwen-plus / ep-xxx"}
        list={`model-suggest-${mode}-${slot}`}
        data-testid={`model-${mode}-${slot}`}
      />
      <datalist id={`model-suggest-${mode}-${slot}`}>
        {models.map((m) => (
          <option key={m.id} value={m.id}>{m.label}</option>
        ))}
      </datalist>

      <label className="f-label">
        API Key
        {saved?.api_key_masked && <span className="key-saved">已保存 {saved.api_key_masked} · 留空=保留</span>}
      </label>
      <input
        type="password"
        value={apiKey}
        onChange={(e) => { setApiKey(e.target.value); setTouched(true); }}
        placeholder={saved?.api_key_masked ? "留空保留已存 Key，输入新值覆盖" : "sk-…（免费厂商可留空，由平台提供）"}
        data-testid={`apikey-${mode}-${slot}`}
      />
      {preset?.note && <div className="hint-line">{preset.note}</div>}

      <div className="llm-btnrow">
        <button className="btn-primary btn-md" disabled={busy} onClick={save} data-testid={`save-${mode}-${slot}`}>
          {busy ? "处理中…" : "保存配置"}
        </button>
        <button className="btn-secondary btn-md" onClick={test} data-testid={`test-${mode}-${slot}`}>测试连通</button>
        {saved && <button className="btn-outline-danger btn-md" disabled={busy} onClick={del}>删除</button>}
      </div>
      {testMsg && <div className={"test-msg " + (testMsg.ok ? "test-ok" : "test-err")}>{testMsg.text}</div>}
    </section>
  );
}
