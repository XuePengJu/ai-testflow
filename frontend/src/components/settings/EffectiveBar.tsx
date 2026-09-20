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
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [results, setResults] = useState<Record<string, { ok: boolean; text: string }>>({});

  const runTest = async (slot: "text" | "vision" | "embedding") => {
    setTesting((m) => ({ ...m, [slot]: true })); // 按槽位独立：互不置灰，可并发测
    try {
      const r = await testDefault(slot);
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
    } finally {
      setTesting((m) => ({ ...m, [slot]: false }));
    }
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
          disabled={!!testing.text}
          onClick={() => void runTest("text")}
          data-testid="test-effective-text"
        >
          {testing.text ? "测试中…" : "测连通"}
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
          disabled={!!testing.vision}
          onClick={() => void runTest("vision")}
          data-testid="test-effective-vision"
        >
          {testing.vision ? "测试中…" : "测连通"}
        </button>
        {results.vision && (
          <span className={"test-msg " + (results.vision.ok ? "test-ok" : "test-err")}>{results.vision.text}</span>
        )}
      </div>
      {/* V4.4.1 向量模型回显：所有角色可见（用户自配/平台默认/env 均回显；mock 显示未配置提示） */}
      <div className="eff-row">
        <div>
          <div className="eff-name">向量模型（Embedding）</div>
          <div className="eff-model">
            {effective.embedding
              ? `${effective.embedding.provider_label} · ${effective.embedding.model}`
              : "未配置（知识库入库与检索不可用）"}
          </div>
          {effective.embedding_source === "mock" && (
            <div className="eff-model" style={{ fontSize: 12, color: "#d97706" }}>
              ⚠️ 当前为 mock 向量，请到「模型配置 → Embedding」配置后重建索引
            </div>
          )}
        </div>
        <button
          className="btn-secondary btn-sm"
          disabled={!!testing.embedding}
          onClick={() => void runTest("embedding")}
          data-testid="test-effective-embedding"
        >
          {testing.embedding ? "测试中…" : "测连通"}
        </button>
        {results.embedding && (
          <span className={"test-msg " + (results.embedding.ok ? "test-ok" : "test-err")}>{results.embedding.text}</span>
        )}
      </div>
    </section>
  );
}
