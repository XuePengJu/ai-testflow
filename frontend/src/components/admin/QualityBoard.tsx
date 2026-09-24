/**
 * 质量看板（M0）：平台自身 pytest + Node e2e 实测结果可视化。
 *
 * - 顶部 4 枚汇总卡：pytest 用例数 / 通过率 / 覆盖率 / e2e 套件通过率
 * - 按测试文件的用例分布条形图 + 历史趋势折线（纯 SVG/div，不引图表库）
 * - 「▶ 运行测试」→ POST /quality/run → 2s 轮询 status → 完成后刷新（仅 canRun 时显示，admin）
 * - 方案 A（2026-09-23）：看板对所有登录角色只读展示（访客可看），运行按钮 admin 专属
 * 铁律：所有数字来自后端实测聚合 JSON（/api/quality/*），禁止写死展示值。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { FlaskConical, CheckCircle2, ShieldCheck, Globe, Play } from "lucide-react";
import {
  getQualityHistory,
  getQualityRunStatus,
  getQualitySummary,
  startQualityRun,
  toast,
} from "../../api/client";
import type { QualityHistoryPoint, QualityRunStatus, QualitySummary } from "../../types";

const PCT_KEYS = ["pass_rate", "coverage_pct"] as const;

export default function QualityBoard({ canRun = false }: { canRun?: boolean }) {
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [emptyMsg, setEmptyMsg] = useState("");
  const [history, setHistory] = useState<QualityHistoryPoint[]>([]);
  const [status, setStatus] = useState<QualityRunStatus | null>(null);
  const [running, setRunning] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadAll = useCallback(async () => {
    const [s, h] = await Promise.all([getQualitySummary(), getQualityHistory()]);
    if (s) {
      setSummary(s.summary);
      setEmptyMsg(s.exists ? "" : s.message || "暂无质量数据");
    }
    if (h) setHistory(h);
  }, []);

  const stopPolling = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    void loadAll();
    return stopPolling;
  }, [loadAll, stopPolling]);

  const startPolling = useCallback(() => {
    stopPolling();
    timerRef.current = setInterval(() => {
      void (async () => {
        const st = await getQualityRunStatus();
        if (!st) return;
        setStatus(st);
        if (st.status !== "running") {
          stopPolling();
          setRunning(false);
          void loadAll();
          if (st.status === "completed") toast("测试完成，质量数据已更新");
          else if (st.status === "failed") toast(`测试失败：${st.error || "未知错误"}`);
        }
      })();
    }, 2000);
  }, [loadAll, stopPolling]);

  const runTest = async () => {
    const r = await startQualityRun();
    if (!r) return; // 运行中 409 等错误已由 apiJson toast
    setRunning(true);
    setStatus({
      run_id: r.run_id, status: "running", stage: "prepare",
      message: "正在准备测试环境…", error: null, started_at: null, finished_at: null,
    });
    startPolling();
  };

  if (!summary) {
    return (
      <section className="set-card" data-testid="quality-board">
        <h3>质量看板</h3>
        <div className="sub">{emptyMsg || "加载中…"}</div>
        {canRun && (
          <div className="llm-btnrow">
            <button className="btn-primary btn-md" disabled={running} onClick={() => void runTest()}>
              <Play size={14} />{running ? "运行中…" : "▶ 运行测试"}
            </button>
          </div>
        )}
      </section>
    );
  }

  const p = summary.pytest;
  const e = summary.e2e;
  const fileEntries = Object.entries(p.by_file).sort((a, b) => b[1].total - a[1].total);
  const maxTotal = Math.max(1, ...fileEntries.map(([, v]) => v.total));

  // 趋势折线坐标（0~100% 纵轴，等距横轴）
  const W = 640, H = 170, PL = 34, PR = 12, PT = 12, PB = 24;
  const n = history.length;
  const x = (i: number) => PL + (n <= 1 ? (W - PL - PR) / 2 : ((W - PL - PR) * i) / (n - 1));
  const y = (v: number) => PT + (H - PT - PB) * (1 - v / 100);
  const line = (k: (typeof PCT_KEYS)[number]) => history.map((pt, i) => `${x(i)},${y(pt[k])}`).join(" ");

  return (
    <div data-testid="quality-board">
      <section className="stats-row" style={{ width: "100%" }}>
        <div className="stat-card">
          <div className="stat-icon blue"><FlaskConical size={22} /></div>
          <div className="stat-body">
            <div className="s-num">{p.total}</div>
            <div className="s-label">pytest 用例 · 通过 {p.passed} / 失败 {p.failed}</div>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon green"><CheckCircle2 size={22} /></div>
          <div className="stat-body">
            <div className="s-num">{p.pass_rate}%</div>
            <div className="s-label">pytest 通过率</div>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon purple"><ShieldCheck size={22} /></div>
          <div className="stat-body">
            <div className="s-num">{p.coverage_pct}%</div>
            <div className="s-label">代码覆盖率（app/）</div>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-icon orange"><Globe size={22} /></div>
          <div className="stat-body">
            <div className="s-num">{e.total ? `${e.passed}/${e.total}` : "—"}</div>
            <div className="s-label">e2e 套件通过</div>
          </div>
        </div>
      </section>

      <section className="set-card" style={{ marginTop: 14 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <h3 style={{ margin: 0 }}>测试运行</h3>
          {canRun && (
            <button className="btn-primary btn-md" disabled={running} onClick={() => void runTest()} data-testid="run-quality-test">
              <Play size={14} />{running ? "运行中…" : "▶ 运行测试"}
            </button>
          )}
        </div>
        <div className="sub" style={{ marginTop: 6, marginBottom: 0 }}>
          {running
            ? `后台执行中（阶段：${status?.message || status?.stage || "准备"}）…完成后自动刷新`
            : `上次聚合：${summary.generated_at.slice(0, 19).replace("T", " ")} · pytest 耗时 ${(p.duration_ms / 1000).toFixed(1)}s`}
        </div>
        {p.failed > 0 && p.failures.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#d83931", marginBottom: 6 }}>失败用例（{p.failed}）</div>
            {p.failures.map((f) => (
              <div key={f.test} style={{ fontSize: 12.5, lineHeight: 1.7, color: "#4e5969", borderBottom: "1px dashed #f0f1f3", padding: "6px 0" }}>
                <div style={{ color: "#1f2329" }}>{f.file} → {f.test.split("::").slice(1).join("::")}</div>
                <pre style={{ margin: "2px 0 0", whiteSpace: "pre-wrap", wordBreak: "break-all", color: "#d83931" }}>{f.message}</pre>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="set-card" style={{ marginTop: 14 }}>
        <h3>按测试文件分布</h3>
        <div className="sub">pytest 用例数按文件统计（绿色段为通过）。</div>
        {fileEntries.map(([file, v]) => (
          <div key={file} style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <div style={{ width: 220, fontSize: 12.5, color: "#4e5969", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={file}>{file}</div>
            <div style={{ flex: 1, height: 14, background: "#f2f3f5", borderRadius: 7, overflow: "hidden" }}>
              <div style={{ width: `${(v.total / maxTotal) * 100}%`, height: "100%", background: "#e8f3ff", borderRadius: 7, position: "relative" }}>
                <div style={{ width: `${v.total ? (v.passed / v.total) * 100 : 0}%`, height: "100%", background: "#00b42a", borderRadius: 7 }} />
              </div>
            </div>
            <div style={{ width: 70, fontSize: 12, color: "#8f959e", textAlign: "right" }}>{v.passed}/{v.total}</div>
          </div>
        ))}
      </section>

      <section className="set-card" style={{ marginTop: 14 }}>
        <h3>历史趋势</h3>
        <div className="sub">
          近 {history.length} 次聚合 · <span style={{ color: "#165dff" }}>━ 通过率</span> · <span style={{ color: "#00b42a" }}>━ 覆盖率</span>
        </div>
        {n >= 1 ? (
          <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: 720 }} data-testid="quality-trend">
            {[0, 50, 100].map((v) => (
              <g key={v}>
                <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} stroke="#f0f1f3" strokeWidth={1} />
                <text x={4} y={y(v) + 4} fontSize={10} fill="#8f959e">{v}</text>
              </g>
            ))}
            {n >= 2 && PCT_KEYS.map((k, idx) => (
              <polyline key={k} points={line(k)} fill="none" stroke={idx === 0 ? "#165dff" : "#00b42a"} strokeWidth={2} />
            ))}
            {n === 1 && PCT_KEYS.map((k, idx) => (
              <circle key={k} cx={x(0)} cy={y(history[0][k])} r={3} fill={idx === 0 ? "#165dff" : "#00b42a"} />
            ))}
            {history.map((pt, i) => (
              <text key={pt.ts} x={x(i)} y={H - 6} fontSize={9} fill="#8f959e" textAnchor="middle">
                {pt.ts.slice(5, 16).replace("T", " ")}
              </text>
            ))}
          </svg>
        ) : (
          <div className="hint-line">暂无历史数据，运行一次测试后生成趋势。</div>
        )}
      </section>
    </div>
  );
}
