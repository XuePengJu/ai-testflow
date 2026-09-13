/**
 * 生效模型条（M4）：当前实际使用的模型（我的配置 > 平台默认 > 环境变量 > mock）
 * + 一键测通（POST /llm/test-default/{slot}，服务端解析配置，无需暴露 Key）。
 * V4：来源改 badge；测连通按钮 secondary。
 */
import { useState } from "react";
import { useSettingsStore } from "../../store/settingsStore";

const SOURCE_LABEL: Record<string, string> = {
  user: "我的配置",
  platform: "平台默认",
  env: "服务器环境变量",
  mock: "Mock 演示模式",
};

export default function EffectiveBar() {
  const effective = useSettingsStore((s) => s.effective);
  const testDefault = useSettingsStore((s) => s.testDefault);
  const [testing, setTesting] = useState<string>("");
  const [results, setResults] = useState<Record<string, { ok: boolean; text: string }>>({});

  const runTest = async (slot: "text" | "vision") => {
    setTesting(slot);
    const r = await testDefault(slot);
    setTesting("");
    if (!r) {
      setResults((m) => ({ ...m, [slot]: { ok: false, text: "请求失败" } }));
      return;
    }
    setResults((m) => ({
      ...m,
      [slot]: r.ok
        ? { ok: true, text: `✓ 可用${r.latency_ms != null ? ` · ${r.latency_ms}ms` : ""}（${r.model || ""}）` }
        : { ok: false, text: `✗ ${r.error_label || "不可用"}` },
    }));
  };

  if (!effective) return null;

  return (
    <section className="set-card effective-bar" data-testid="effective-bar">
      <h3>当前生效模型</h3>
      <div className="eff-source">
        <span className="role-badge user">来源：{SOURCE_LABEL[effective.source] || effective.source}</span>
      </div>
      <div className="eff-row">
        <div>
          <div className="eff-name">文本模型</div>
          <div className="eff-model">
            {effective.text ? `${effective.text.provider_label} · ${effective.text.model}` : "未配置"}
          </div>
        </div>
        <button
          className="btn-secondary btn-sm"
          disabled={!!testing}
          onClick={() => void runTest("text")}
          data-testid="test-effective-text"
        >
          {testing === "text" ? "测试中…" : "测连通"}
        </button>
        {results.text && (
          <span className={"test-msg " + (results.text.ok ? "test-ok" : "test-err")}>{results.text.text}</span>
        )}
      </div>
      <div className="eff-row">
        <div>
          <div className="eff-name">图像模型</div>
          <div className="eff-model">
            {effective.vision ? `${effective.vision.provider_label} · ${effective.vision.model}` : "未配置（可选）"}
          </div>
        </div>
        <button
          className="btn-secondary btn-sm"
          disabled={!!testing}
          onClick={() => void runTest("vision")}
          data-testid="test-effective-vision"
        >
          {testing === "vision" ? "测试中…" : "测连通"}
        </button>
        {results.vision && (
          <span className={"test-msg " + (results.vision.ok ? "test-ok" : "test-err")}>{results.vision.text}</span>
        )}
      </div>
    </section>
  );
}
