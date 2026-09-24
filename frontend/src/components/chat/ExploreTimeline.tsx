/**
 * M5 探索式测试时间线（TaskStepsCard 的 explore 步骤可视化，与 CrawlerPagesView 同位）。
 *
 * - 数据源：explore 步骤 StepLog.input_summary 的 JSON（explorer_agent.run_explore 落库的
 *   details：{url, login, stop_reason, steps: [{n,url,action,args,reason,result,screenshot}], ...}）
 * - 每步一张卡：步骤序号 / 动作 action / 目标 url / LLM 理由 reason / 结果 result（截断）+ 截图缩略
 * - 截图走既有端点 /api/tasks/{id}/pages/screenshot/step-NNN.png（fetchPageScreenshot，blob 鉴权）
 *   点击缩略图 lightbox 放大（复用 CrawlerPagesView 的遮罩模式）
 * - 解析失败 / steps 为空 → 静默回退原 JSON 文本（不能因数据异常破坏步骤卡展开区）
 */
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { fetchPageScreenshot } from "../../api/client";

/** input_summary JSON 里 steps[] 的单步结构（与 explorer_agent._add_step 落库字段对齐） */
interface ExploreStep {
  n: number;
  url: string;
  action: string;
  args?: unknown;
  reason: string;
  result: string;
  /** 相对任务目录路径（explore/step-NNN.png），空串 = 该步无截图 */
  screenshot: string;
}

/** 从 input_summary 解析 steps 数组；解析失败/非数组返回 null（调用方回退原文） */
export function parseExploreSteps(json: string): ExploreStep[] | null {
  if (!json || !json.trim()) return null;
  try {
    const obj = JSON.parse(json) as { steps?: unknown };
    if (Array.isArray(obj.steps) && obj.steps.length > 0) {
      return obj.steps.filter(
        (s): s is ExploreStep => !!s && typeof s === "object" && typeof (s as ExploreStep).action === "string",
      );
    }
  } catch {
    /* 非 JSON（旧数据/异常数据）→ 回退原文 */
  }
  return null;
}

/** 结果文案截断（ok 之外展示关键信息，超长省略） */
function clip(text: string, max = 120): string {
  const t = (text || "").trim();
  return t.length > max ? `${t.slice(0, max)}…` : t || "—";
}

/** 步骤截图缩略：fetchPageScreenshot（Bearer blob）→ objectURL，卸载时 revoke；点击放大 */
function ExploreShot({ taskId, step, onZoom }: { taskId: string; step: ExploreStep; onZoom: (url: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  // screenshot 落库形如 explore/step-001.png → 端点只取文件名 step-001.png
  const name = (step.screenshot || "").split("/").pop() || "";

  useEffect(() => {
    if (!name) {
      setFailed(true);
      return;
    }
    let live = true;
    let made: string | null = null;
    void fetchPageScreenshot(taskId, name).then((u) => {
      if (!live) {
        if (u) URL.revokeObjectURL(u);
        return;
      }
      if (u) {
        made = u;
        setUrl(u);
      } else {
        setFailed(true);
      }
    });
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [taskId, name]);

  const boxStyle: CSSProperties = {
    width: 120,
    height: 72,
    flexShrink: 0,
    borderRadius: 6,
    border: "1px solid #e5e6eb",
    background: "#f7f8fa",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
  };
  if (failed) return <div style={boxStyle} className="hint-line" title="该步无截图">无截图</div>;
  if (!url) return <div style={boxStyle} className="hint-line">加载中…</div>;
  return (
    <div style={boxStyle}>
      <img
        src={url}
        alt={`第 ${step.n} 步截图`}
        title="点击放大"
        onClick={() => onZoom(url)}
        style={{ display: "block", width: "100%", height: "100%", objectFit: "cover", objectPosition: "top", cursor: "zoom-in" }}
      />
    </div>
  );
}

/** 探索时间线：每步一卡（序号/动作/URL/理由/结果 + 截图缩略）；steps 拿不到回退原 JSON 文本 */
export default function ExploreTimeline({ taskId, json }: { taskId: string; json: string }) {
  const [zoom, setZoom] = useState<string | null>(null); // 截图放大 lightbox
  const steps = parseExploreSteps(json);

  // 回退：解析失败 / 无 steps 数据 → 原 JSON 文本照旧（与 crawler 步骤回退模式一致）
  if (!steps) {
    return json ? (
      <div className="tsc-io">
        <span className="tsc-io-label">📥 输入</span>
        <div className="tsc-io-text">{json}</div>
      </div>
    ) : (
      <div className="tsc-io-text tsc-muted">（暂无探索过程数据）</div>
    );
  }

  return (
    <div className="tsc-io">
      <span className="tsc-io-label">🧭 探索时间线（{steps.length} 步）</span>
      <div style={{ marginTop: 6 }}>
        {steps.map((s, i) => (
          <div
            key={`${s.n ?? i}`}
            style={{
              display: "flex",
              gap: 10,
              alignItems: "flex-start",
              padding: "8px 0",
              borderBottom: i < steps.length - 1 ? "1px dashed #f0f1f3" : "none",
            }}
          >
            {/* 步骤序号圆点 */}
            <span
              style={{
                flexShrink: 0,
                width: 22,
                height: 22,
                borderRadius: "50%",
                background: "#e8f3ff",
                color: "#165dff",
                fontSize: 11,
                fontWeight: 600,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                marginTop: 2,
              }}
            >
              {s.n ?? i + 1}
            </span>
            {/* 文本区：动作 / URL / 理由 / 结果 */}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: "#1f2329" }}>
                {s.action}
                <span style={{ fontWeight: 400, color: "#8f959e", marginLeft: 8 }}>
                  {s.result && s.result !== "ok" ? clip(s.result, 60) : "执行成功"}
                </span>
              </div>
              {s.url && (
                <div className="dash" title={s.url} style={{ fontSize: 11.5, color: "#8f959e", marginTop: 2, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {s.url}
                </div>
              )}
              {s.reason && (
                <div style={{ fontSize: 12, color: "#4e5969", marginTop: 2 }} title={s.reason}>
                  💡 {clip(s.reason, 80)}
                </div>
              )}
            </div>
            {/* 截图缩略（无截图渲染占位） */}
            {s.screenshot ? (
              <ExploreShot taskId={taskId} step={s} onZoom={setZoom} />
            ) : (
              <div
                className="hint-line"
                style={{
                  width: 120, height: 72, flexShrink: 0, borderRadius: 6, border: "1px dashed #e5e6eb",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}
              >
                无截图
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 截图放大 lightbox（点击遮罩关闭） */}
      {zoom && (
        <div
          onClick={() => setZoom(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 3000,
            background: "rgba(15,18,25,.72)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "zoom-out",
          }}
        >
          <img
            src={zoom}
            alt="探索步骤截图（放大）"
            style={{ maxWidth: "92vw", maxHeight: "92vh", borderRadius: 8, boxShadow: "0 8px 40px rgba(0,0,0,.4)" }}
          />
        </div>
      )}
    </div>
  );
}
