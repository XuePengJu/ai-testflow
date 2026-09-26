/**
 * 质量看板（M0）：平台自身 pytest + Node e2e 实测结果可视化。
 *
 * - 顶部 4 枚汇总卡：pytest 用例数 / 通过率 / 覆盖率 / e2e 套件通过率
 * - 功能模块 × 用例明细（按测试文件映射中文模块名，可展开逐条用例 + 关键字搜索）+ 历史趋势折线（纯 SVG/div，不引图表库）
 * - 「▶ 运行测试」→ POST /quality/run（scope 范围复选：单测/接口/e2e 套件子集，默认全部）
 *   → 2s 轮询 status → 阶段 stepper + pytest 进度条 + e2e 套件状态 + 日志尾部（仅 canRun，admin）
 * - 方案 A（2026-09-23）：看板对所有登录角色只读展示（访客可看），运行按钮 admin 专属
 * 铁律：所有数字来自后端实测聚合 JSON（/api/quality/*），禁止写死展示值。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { FlaskConical, CheckCircle2, ShieldCheck, Globe, Play, Search, ChevronDown, ChevronRight } from "lucide-react";
import {
  getQualityHistory,
  getQualityRunStatus,
  getQualitySummary,
  startQualityRun,
  toast,
} from "../../api/client";
import type { QualityHistoryPoint, QualityRunStatus, QualitySummary } from "../../types";
import { CASE_ZH } from "./qualityCaseZh";

const PCT_KEYS = ["pass_rate", "coverage_pct"] as const;

/** 测试文件 → 功能模块中文名（key = 去掉 tests/test_ 前缀与 .py 后缀）；未收录的文件走通用兜底 */
const MODULE_META: Record<string, { name: string; desc: string }> = {
  auth: { name: "用户认证", desc: "注册/登录校验、Token 签发与越权防护" },
  admin: { name: "管理员能力", desc: "用户管理、任务数据隔离、角色权限矩阵" },
  guest: { name: "访客模式", desc: "访客 Token、配额与共享数据" },
  llm_config: { name: "模型配置", desc: "LLM 端点接入、连通性与校验" },
  llm_pool: { name: "模型池", desc: "多模型调度与池化管理" },
  embedding_config: { name: "向量配置", desc: "Embedding 模型接入与校验" },
  chat_thinking: { name: "深度思考", desc: "思考链路流式输出、面板展示与落库" },
  deep_think: { name: "思考开关", desc: "深度思考开/关行为与参数兼容" },
  chat_fallback: { name: "对话降级", desc: "模型异常时的兜底回复" },
  chat_rag: { name: "对话 RAG", desc: "对话挂载知识库的检索问答" },
  e2e_conversation: { name: "对话 e2e", desc: "对话链路端到端验证" },
  doc_extract: { name: "文档抽取", desc: "docx/pdf/md/txt 附件文本提取" },
  task_naming: { name: "任务命名", desc: "需求总结生成任务名/会话名" },
  task_pages: { name: "任务页面", desc: "任务列表/详情页权限与渲染" },
  case_parse: { name: "用例解析", desc: "需求文本解析为测试用例" },
  compound_title: { name: "用例标题", desc: "动作→预期 复合标题生成与拆分" },
  step_expected: { name: "步骤预期", desc: "测试步骤与预期结果的对应" },
  case_id: { name: "用例编号", desc: "用例 ID 生成与唯一性" },
  categories: { name: "用例分类", desc: "用例类目归属与筛选" },
  iterate: { name: "用例迭代", desc: "多轮迭代优化与去重" },
  explorer_agent: { name: "页面探索", desc: "Explorer Agent 页面元素探索" },
  web_crawler: { name: "网页抓取", desc: "URL 抓取与内容提取" },
  auto_exec: { name: "自动执行", desc: "脚本自动执行编排" },
  auto_heal: { name: "自动修复", desc: "失败用例自动修复" },
  knowledge_api: { name: "知识库", desc: "知识库管理与检索 API" },
  v41_kb_qa: { name: "知识库问答", desc: "V4.1 知识库问答链路" },
  markdown_parser: { name: "Markdown 解析", desc: "MD 渲染与容错解析" },
  jsonx: { name: "JSON 容错", desc: "非标 JSON 解析兜底" },
  crypto: { name: "数据安全", desc: "AES 加解密与密文存储" },
  demo_mode_off: { name: "演示模式", desc: "mock 数据关闭与真实链路" },
};

/** tests/test_xxx.py → "xxx" */
const fileModuleKey = (f: string) =>
  f.split("/").pop()!.replace(/\.py$/, "").replace(/^test_/, "");
/** 用例显示名：优先中文映射（说清在验证什么），未登记的走下划线转空格兜底 */
const caseLabel = (n: string) => CASE_ZH[n] ?? n.replace(/^test_/, "").replace(/_/g, " ");

/** e2e 套件 → 中文名（真实操作场景） */
const E2E_SUITE_META: Record<string, string> = {
  "e2e-m1-browser.mjs": "登录注册与角色鉴权（访客 / admin / user + AES 加密链路）",
  "e2e-m2-browser.mjs": "对话生成测试用例全流程（发消息 → 流式回复 → 生成 → 任务完成 → 会话回放）",
  "e2e-m3-browser.mjs": "任务详情 · 思维导图 · 用例列表 · 导出 · 迭代补充",
  "e2e-m4-browser.mjs": "设置与模型配置 · 用户管理 · 分类管理",
  "e2e-m5-smoke.mjs": "真实大模型冒烟（真实 Key 外呼：配置模型 → 真实流式回复）",
};

/** e2e 套件复选项（key 与后端 _E2E_SLOTS 对齐） */
const E2E_SLOT_META: { key: string; short: string }[] = [
  { key: "m1", short: "M1 登录注册与鉴权" },
  { key: "m2", short: "M2 对话生成用例" },
  { key: "m3", short: "M3 导图·导出·迭代" },
  { key: "m4", short: "M4 设置·管理·分类" },
  { key: "m5", short: "M5 真实模型冒烟" },
];

/** e2e 套件实时状态图标 */
const suiteStateIcon = (s: string) =>
  s === "passed" ? "✓" : s === "failed" || s === "error" ? "✗" : s === "running" ? "●" : "○";
const suiteStateColor = (s: string) =>
  s === "passed" ? "#00b42a" : s === "failed" || s === "error" ? "#d83931" : s === "running" ? "#165dff" : "#c9cdd4";

/** 分段采集时间显示：ISO → MM-DD HH:MM */
const segTime = (ts?: string) => (ts ? ts.slice(5, 16).replace("T", " ") : "—");

export default function QualityBoard({ canRun = false }: { canRun?: boolean }) {
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [emptyMsg, setEmptyMsg] = useState("");
  const [history, setHistory] = useState<QualityHistoryPoint[]>([]);
  const [status, setStatus] = useState<QualityRunStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [query, setQuery] = useState("");
  const [trendHover, setTrendHover] = useState<{ i: number; k: (typeof PCT_KEYS)[number] } | null>(null);   // 趋势图悬浮（点下标 + 线）
  const [kindFilter, setKindFilter] = useState<"" | "ui" | "api" | "unit">("");
  const [openOverrides, setOpenOverrides] = useState<Record<string, boolean>>({});
  // 运行范围复选（默认全选 = 全量运行）
  const [scopeOpen, setScopeOpen] = useState(false);
  const [selUnit, setSelUnit] = useState(true);
  const [selApi, setSelApi] = useState(true);
  const [selE2E, setSelE2E] = useState<string[]>(E2E_SLOT_META.map((s) => s.key));
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const scopeAll = selUnit && selApi && selE2E.length === E2E_SLOT_META.length;
  const scopeNone = !selUnit && !selApi && selE2E.length === 0;
  const scopeLabel = scopeAll
    ? "全部"
    : scopeNone
      ? "未选择"
      : [
          selUnit ? "单测" : "",
          selApi ? "接口" : "",
          selE2E.length > 0 ? `e2e×${selE2E.length}` : "",
        ].filter(Boolean).join(" + ");

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
    // 全部勾选 = 全量（body 仍显式传全量语义）；部分勾选按 scope 传子集
    const scope = {
      unit: selUnit,
      api: selApi,
      e2e: selE2E.length === E2E_SLOT_META.length ? true : selE2E,
    };
    const r = await startQualityRun(scope);
    if (!r) return; // 运行中 409 / 范围为空 400 等错误已由 apiJson toast
    setScopeOpen(false);
    setRunning(true);
    setStatus({
      run_id: r.run_id, status: "running", stage: "prepare",
      message: "正在准备测试环境…", error: null, started_at: null, finished_at: null,
      plan: (r.plan as QualityRunStatus["plan"]) ?? null,
      pytest_percent: null, pytest_done: null, suites: [], log_tail: [],
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
  // 用例按功能模块分组（源自测试文件映射中文名）；搜索/类型筛选时只留命中模块并强制展开
  const cases = p.cases ?? [];
  const q = query.trim().toLowerCase();
  const modules = Object.entries(
    cases.reduce<Record<string, typeof cases>>((acc, c) => {
      (acc[c.file] ||= []).push(c);
      return acc;
    }, {})
  )
    .map(([file, list]) => {
      const key = fileModuleKey(file);
      const meta = MODULE_META[key];
      return {
        file,
        key,
        name: meta?.name ?? (fileModuleKey(key) || file),
        desc: meta?.desc ?? file,
        list: (q
          ? list.filter((c) =>
              `${caseLabel(c.name)} ${c.name} ${c.param} ${c.kind === "api" ? "接口测试 api" : "单元测试 unit"}`
                .toLowerCase()
                .includes(q),
            )
          : list
        ).filter((c) => !kindFilter || kindFilter === "ui" || c.kind === kindFilter),
        total: list.length,
        apiN: list.filter((c) => c.kind === "api").length,
        unitN: list.filter((c) => c.kind === "unit").length,
        passed: list.filter((c) => c.outcome === "passed").length,
        failed: list.filter((c) => c.outcome !== "passed" && c.outcome !== "skipped").length,
      };
    })
    .filter((m) => (!q && !kindFilter) || m.list.length > 0)
    .sort((a, b) => b.failed - a.failed || b.total - a.total);
  const isModuleOpen = (key: string, failed: number) =>
    q !== "" || kindFilter !== "" || (openOverrides[key] ?? failed > 0);

  // 趋势折线坐标（0~100% 纵轴，等距横轴）
  const W = 640, H = 170, PL = 34, PR = 12, PT = 12, PB = 24;
  const n = history.length;
  const x = (i: number) => PL + (n <= 1 ? (W - PL - PR) / 2 : ((W - PL - PR) * i) / (n - 1));
  const y = (v: number) => PT + (H - PT - PB) * (1 - v / 100);
  // 趋势折线：全量点实线实心、部分运行点虚线段+空心圆（区分展示，见下方 SVG 段）

  // ---- 运行进度（V2）：阶段 stepper / pytest 进度 / e2e 套件状态 / 日志尾部 ----
  const plan = status?.plan;
  const stepDefs: { key: string; label: string }[] = [{ key: "prepare", label: "准备" }];
  if (plan?.unit || plan?.api) {
    stepDefs.push({ key: "pytest", label: plan.unit && plan.api ? "pytest 全量" : plan.unit ? "单元测试" : "接口测试" });
  }
  if (plan?.e2e && plan.e2e.length > 0) {
    stepDefs.push({ key: "e2e", label: `浏览器 e2e ×${plan.e2e.length}` });
  }
  stepDefs.push({ key: "aggregate", label: "聚合" }, { key: "done", label: "完成" });
  const stageKey = status?.stage || "prepare";
  const idxOf = (key: string) => stepDefs.findIndex((s) => s.key === key);
  const stepIndex =
    stageKey === "prepare" ? 0
      : stageKey === "scope-collect" || stageKey === "pytest" ? Math.max(1, idxOf("pytest"))
        : stageKey === "e2e" ? Math.max(1, idxOf("e2e"))
          : stageKey === "aggregate" ? Math.max(1, idxOf("aggregate"))
            : stepDefs.length - 1;
  const elapsedSec = status?.started_at
    ? Math.max(0, Math.floor((Date.now() - new Date(status.started_at).getTime()) / 1000))
    : 0;
  const elapsedText = `${Math.floor(elapsedSec / 60)}:${String(elapsedSec % 60).padStart(2, "0")}`;
  const e2eLastCollected = e.suites.map((s) => s.collected_at).filter(Boolean).sort().pop();

  return (
    <div data-testid="quality-board">
      <section className="stats-row" style={{ width: "100%" }}>
        <div className="stat-card">
          <div className="stat-icon blue"><FlaskConical size={22} /></div>
          <div className="stat-body">
            <div className="s-num">{p.total}</div>
            <div className="s-label">
              pytest 用例 · 通过 {p.passed} / 失败 {p.failed}
              {p.api_cases != null && ` · 接口 ${p.api_cases} / 单测 ${p.unit_cases}`}
            </div>
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
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              {/* 运行范围复选（默认全部）：单测 / 接口 / UI e2e（可展开 m1~m5 子复选） */}
              <div style={{ position: "relative" }}>
                <button
                  onClick={() => setScopeOpen((v) => !v)}
                  disabled={running}
                  data-testid="quality-scope-toggle"
                  style={{ padding: "7px 12px", fontSize: 12.5, borderRadius: 8, cursor: running ? "not-allowed" : "pointer", border: "1px solid #e5e6eb", background: "#fff", color: "#4e5969" }}
                >
                  运行范围：{scopeLabel} ▾
                </button>
                {scopeOpen && (
                  <div
                    style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 30, background: "#fff", border: "1px solid #e5e6eb", borderRadius: 10, boxShadow: "0 8px 24px rgba(31,35,41,.12)", padding: "12px 14px", minWidth: 280 }}
                    data-testid="quality-scope-popover"
                  >
                    <div style={{ fontSize: 12, fontWeight: 600, color: "#1f2329", marginBottom: 8 }}>选择要执行的范围</div>
                    {(
                      [
                        { checked: selUnit, set: setSelUnit, label: "单元测试", desc: "pytest 函数 / 服务层" },
                        { checked: selApi, set: setSelApi, label: "接口测试", desc: "pytest HTTP 接口" },
                      ] as const
                    ).map((it) => (
                      <label key={it.label} style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 0", fontSize: 12.5, color: "#1f2329", cursor: "pointer" }}>
                        <input type="checkbox" checked={it.checked} onChange={(ev) => it.set(ev.target.checked)} />
                        <span>{it.label}</span>
                        <span style={{ color: "#86909c", fontSize: 11.5 }}>{it.desc}</span>
                      </label>
                    ))}
                    <label style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 0", fontSize: 12.5, color: "#1f2329", cursor: "pointer" }}>
                      <input
                        type="checkbox"
                        checked={selE2E.length === E2E_SLOT_META.length}
                        ref={(el) => { if (el) el.indeterminate = selE2E.length > 0 && selE2E.length < E2E_SLOT_META.length; }}
                        onChange={(ev) => setSelE2E(ev.target.checked ? E2E_SLOT_META.map((s) => s.key) : [])}
                      />
                      <span>UI 测试</span>
                      <span style={{ color: "#86909c", fontSize: 11.5 }}>Playwright 真实浏览器</span>
                    </label>
                    <div style={{ paddingLeft: 24 }}>
                      {E2E_SLOT_META.map((s) => (
                        <label key={s.key} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: 12, color: "#4e5969", cursor: "pointer" }}>
                          <input
                            type="checkbox"
                            checked={selE2E.includes(s.key)}
                            onChange={() =>
                              setSelE2E((prev) => prev.includes(s.key) ? prev.filter((k) => k !== s.key) : [...prev, s.key])
                            }
                          />
                          {s.short}
                        </label>
                      ))}
                    </div>
                    <div style={{ marginTop: 10, display: "flex", gap: 12, fontSize: 12 }}>
                      <button onClick={() => { setSelUnit(true); setSelApi(true); setSelE2E(E2E_SLOT_META.map((s) => s.key)); }} style={{ border: "none", background: "none", color: "#165dff", cursor: "pointer", padding: 0 }}>全选</button>
                      <button onClick={() => { setSelUnit(false); setSelApi(false); setSelE2E([]); }} style={{ border: "none", background: "none", color: "#86909c", cursor: "pointer", padding: 0 }}>清空</button>
                    </div>
                  </div>
                )}
              </div>
              <button className="btn-primary btn-md" disabled={running || scopeNone} onClick={() => void runTest()} data-testid="run-quality-test">
                <Play size={14} />{running ? "运行中…" : "▶ 运行测试"}
              </button>
            </div>
          )}
        </div>
        <div className="sub" style={{ marginTop: 6, marginBottom: 0 }}>
          {running
            ? `后台执行中（${status?.message || status?.stage || "准备"}）…完成后自动刷新`
            : (
              <>
                {`上次聚合：${summary.generated_at.slice(0, 19).replace("T", " ")} · pytest 耗时 ${(p.duration_ms / 1000).toFixed(1)}s`}
                {p.segments && (
                  <span style={{ color: "#86909c" }}>
                    {" · 分段采集：单测 "}{segTime(p.segments.unit)}
                    {" · 接口 "}{segTime(p.segments.api)}
                    {" · e2e "}{segTime(e2eLastCollected)}
                  </span>
                )}
              </>
            )}
        </div>

        {/* 运行中实时进度：阶段 stepper + pytest 进度条 + e2e 套件状态 + 日志尾部 */}
        {running && status && (
          <div style={{ marginTop: 12 }} data-testid="quality-run-progress">
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              {stepDefs.map((st, i) => {
                const done = i < stepIndex;
                const active = i === stepIndex && status.status === "running";
                return (
                  <span key={st.key} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                    {i > 0 && <span style={{ color: "#e5e6eb" }}>→</span>}
                    <span
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12,
                        padding: "3px 10px", borderRadius: 999,
                        background: active ? "#e8f3ff" : done ? "#e8ffea" : "#f7f8fa",
                        color: active ? "#165dff" : done ? "#00b42a" : "#86909c",
                        fontWeight: active || done ? 600 : 400,
                      }}
                    >
                      {done ? "✓" : active ? "●" : "○"} {st.label}
                    </span>
                  </span>
                );
              })}
              <span style={{ marginLeft: "auto", fontSize: 12, color: "#86909c" }}>已用 {elapsedText}</span>
            </div>

            {status.pytest_percent != null && (
              <div style={{ marginTop: 10 }}>
                <div style={{ height: 6, borderRadius: 3, background: "#f0f1f3", overflow: "hidden" }}>
                  <div style={{ width: `${status.pytest_percent}%`, height: "100%", background: "#165dff", borderRadius: 3, transition: "width .5s" }} />
                </div>
                <div style={{ fontSize: 11.5, color: "#86909c", marginTop: 4 }}>
                  pytest 进度 {status.pytest_percent}%
                  {status.pytest_done != null && status.pytest_total_hint ? `（约 ${status.pytest_done}/${status.pytest_total_hint} 条）` : ""}
                </div>
              </div>
            )}

            {(status.suites?.length ?? 0) > 0 && (
              <div style={{ marginTop: 10 }}>
                {status.suites!.map((s) => (
                  <div key={s.name} style={{ display: "flex", gap: 8, alignItems: "baseline", padding: "3px 0", fontSize: 12.5 }}>
                    <span style={{ color: suiteStateColor(s.status), fontWeight: 700, flexShrink: 0 }}>{suiteStateIcon(s.status)}</span>
                    <span style={{ color: "#1f2329", flexShrink: 0 }}>{E2E_SUITE_META[s.name] ?? s.name}</span>
                    {s.steps_total > 0 && (
                      <span style={{ color: "#86909c", fontSize: 11.5, flexShrink: 0 }}>步骤 {s.steps_ok}/{s.steps_total}</span>
                    )}
                    <span style={{ color: "#8f959e", fontSize: 11.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.status === "running" ? s.last_step : ""}
                    </span>
                    {s.duration_ms != null && (
                      <span style={{ marginLeft: "auto", flexShrink: 0, color: "#8f959e", fontSize: 11 }}>{(s.duration_ms / 1000).toFixed(1)}s</span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {(status.log_tail?.length ?? 0) > 0 && (
              <pre
                style={{
                  marginTop: 10, marginBottom: 0, padding: "8px 10px", borderRadius: 8, background: "#f7f8fa",
                  fontSize: 11, lineHeight: 1.6, color: "#4e5969", maxHeight: 150, overflow: "auto",
                  whiteSpace: "pre-wrap", wordBreak: "break-all",
                }}
              >
                {status.log_tail!.join("\n")}
              </pre>
            )}
          </div>
        )}
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

      {/* 测试类型筛选（全局）：UI = 浏览器 e2e；接口 = 触达 HTTP 接口；单测 = 函数/服务层单元测试 */}
      <div
        style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12, flexWrap: "wrap" }}
        data-testid="quality-kind-filter"
      >
        <span style={{ fontSize: 12, color: "#86909c" }}>测试类型：</span>
        {(
          [
            ["", "全部"],
            ["ui", "UI 测试（浏览器 e2e）"],
            ["api", `接口测试${p.api_cases != null ? ` ${p.api_cases}` : ""}`],
            ["unit", `单元测试${p.unit_cases != null ? ` ${p.unit_cases}` : ""}`],
          ] as const
        ).map(([v, label]) => {
          const active = kindFilter === v;
          return (
            <button
              key={v}
              onClick={() => setKindFilter(v)}
              style={{
                padding: "4px 12px", fontSize: 12, borderRadius: 999, cursor: "pointer",
                border: `1px solid ${active ? "#165dff" : "#e5e6eb"}`,
                background: active ? "#165dff" : "#fff",
                color: active ? "#fff" : "#4e5969",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      {/* 真实浏览器操作验证（e2e）——置顶展示，老板关注的是真实操作链路 */}
      <section
        className="set-card"
        style={{ marginTop: 14, display: kindFilter === "api" || kindFilter === "unit" ? "none" : undefined }}
        data-testid="quality-e2e"
      >
        <h3 style={{ margin: 0 }}>真实操作验证（浏览器 e2e）</h3>
        <div className="sub" style={{ marginTop: 6 }}>
          Playwright 驱动真实浏览器逐步操作：登录鉴权、对话生成用例、思维导图、导出、真实模型外呼。
        </div>
        {e.suites.length === 0 ? (
          <div className="hint-line" style={{ marginTop: 10 }}>
            尚无浏览器 e2e 数据，点「运行测试」采集。
          </div>
        ) : (
          e.suites.map((s) => {
            const passed = s.outcome === "passed";
            return (
              <div key={s.name} style={{ borderTop: "1px solid #f0f1f3", marginTop: 10, paddingTop: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: passed ? "#00b42a" : "#d83931" }}>
                    {passed ? "✓" : "✗"}
                  </span>
                  <span style={{ fontSize: 13.5, fontWeight: 600, color: "#1f2329" }}>
                    {E2E_SUITE_META[s.name] ?? s.name}
                  </span>
                  <span
                    style={{ flexShrink: 0, fontSize: 10.5, padding: "1px 7px", borderRadius: 4, background: "#fff3e8", color: "#d25f00", fontWeight: 600 }}
                    title="Playwright 驱动真实浏览器的 UI 测试"
                  >
                    UI
                  </span>
                  <span style={{ marginLeft: "auto", fontSize: 11.5, color: "#8f959e", flexShrink: 0 }}>
                    {(s.duration_ms / 1000).toFixed(1)}s
                    {s.collected_at && <span style={{ marginLeft: 8, color: "#c9cdd4" }} title="该套件最近一次真实执行时间">采集 {segTime(s.collected_at)}</span>}
                  </span>
                </div>
                {s.steps && s.steps.length > 0 ? (
                  <div style={{ padding: "6px 0 2px 22px" }}>
                    {s.steps.map((st, i) => (
                      <div key={i} style={{ display: "flex", gap: 8, padding: "3px 0", fontSize: 12.5, alignItems: "baseline" }}>
                        <span style={{ flexShrink: 0, fontWeight: 700, color: st.ok ? "#00b42a" : "#d83931" }}>{st.ok ? "✓" : "✗"}</span>
                        <span style={{ color: st.ok ? "#4e5969" : "#d83931", wordBreak: "break-all" }}>{st.name}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  s.error && (
                    <pre style={{ margin: "6px 0 0", fontSize: 11.5, color: "#d83931", whiteSpace: "pre-wrap", wordBreak: "break-all", paddingLeft: 22 }}>
                      {s.error}
                    </pre>
                  )
                )}
              </div>
            );
          })
        )}
      </section>

      <section className="set-card" style={{ marginTop: 14, display: kindFilter === "ui" ? "none" : undefined }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <h3 style={{ margin: 0 }}>功能模块 × 用例明细</h3>
          {cases.length > 0 && (
            <div style={{ position: "relative", minWidth: 220 }}>
              <Search size={14} style={{ position: "absolute", left: 9, top: "50%", transform: "translateY(-50%)", color: "#8f959e" }} />
              <input
                value={query}
                onChange={(e2) => setQuery(e2.target.value)}
                placeholder="搜索用例关键字…"
                data-testid="quality-case-search"
                style={{ width: "100%", boxSizing: "border-box", padding: "6px 10px 6px 28px", fontSize: 12.5, borderRadius: 8, border: "1px solid #e5e6eb", outline: "none" }}
              />
            </div>
          )}
        </div>
        <div className="sub" style={{ marginTop: 6 }}>
          全部 {p.total} 条 pytest 用例按功能模块分组，点模块展开看每条用例在测什么{q ? "（搜索中，自动展开命中项）" : ""}。
          <span style={{ marginLeft: 6, color: "#165dff" }}>接口</span> = 走 HTTP 接口的测试，
          <span style={{ color: "#86909c" }}>单测</span> = 函数 / 服务层单元测试。
        </div>
        {cases.length === 0 ? (
          <div className="hint-line" style={{ marginTop: 10 }}>
            当前聚合为旧版数据（未含用例明细），点「运行测试」重新采集后即可查看。
          </div>
        ) : (
          modules.map((m) => {
            const open = isModuleOpen(m.key, m.failed);
            return (
              <div key={m.file} style={{ borderBottom: "1px solid #f0f1f3" }}>
                <div
                  onClick={() => setOpenOverrides((prev) => ({ ...prev, [m.key]: !open }))}
                  data-testid={`quality-module-${m.key}`}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 2px", cursor: "pointer", userSelect: "none" }}
                >
                  {open ? <ChevronDown size={15} color="#86909c" /> : <ChevronRight size={15} color="#86909c" />}
                  <div style={{ minWidth: 0 }}>
                    <span style={{ fontSize: 13.5, fontWeight: 600, color: "#1f2329" }}>{m.name}</span>
                    <span style={{ fontSize: 12, color: "#86909c", marginLeft: 8, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.desc}</span>
                    {m.apiN > 0 && (
                      <span style={{ marginLeft: 6, fontSize: 10.5, padding: "1px 6px", borderRadius: 4, background: "#e8f3ff", color: "#165dff" }} title="接口测试条数">
                        接口 {m.apiN}
                      </span>
                    )}
                    {m.unitN > 0 && (
                      <span style={{ marginLeft: 4, fontSize: 10.5, padding: "1px 6px", borderRadius: 4, background: "#f2f3f5", color: "#86909c" }} title="单元测试条数">
                        单测 {m.unitN}
                      </span>
                    )}
                  </div>
                  <div style={{ marginLeft: "auto", flexShrink: 0, fontSize: 12, display: "flex", gap: 8, alignItems: "center" }}>
                    {m.failed > 0 && <span style={{ color: "#d83931", fontWeight: 600 }}>失败 {m.failed}</span>}
                    <span style={{ color: m.passed === m.total ? "#00b42a" : "#4e5969", fontWeight: 600 }}>{m.passed}/{m.total}</span>
                  </div>
                </div>
                {open && (
                  <div style={{ padding: "0 2px 10px 25px" }}>
                    {m.list.map((c) => {
                      const ok = c.outcome === "passed";
                      const skip = c.outcome === "skipped";
                      return (
                        <div key={`${c.file}::${c.name}[${c.param}]`} style={{ display: "flex", alignItems: "baseline", gap: 8, padding: "4px 0", borderBottom: "1px dashed #f5f6f7" }}>
                          <span style={{ flexShrink: 0, fontSize: 12, fontWeight: 700, color: ok ? "#00b42a" : skip ? "#86909c" : "#d83931" }}>
                            {ok ? "✓" : skip ? "⊘" : "✗"}
                          </span>
                          <span style={{ fontSize: 12.5, color: "#1f2329", wordBreak: "break-all" }} title={`${c.file}::${c.name}${c.param ? `[${c.param}]` : ""}`}>
                            {caseLabel(c.name)}
                            {c.param && <span style={{ color: "#86909c", fontSize: 11.5 }}> · {c.param}</span>}
                          </span>
                          {c.kind && (
                            <span
                              style={{ flexShrink: 0, fontSize: 10.5, padding: "1px 6px", borderRadius: 4, background: c.kind === "api" ? "#e8f3ff" : "#f2f3f5", color: c.kind === "api" ? "#165dff" : "#86909c" }}
                              title={c.kind === "api" ? "接口测试：通过 HTTP 客户端请求后端接口验证" : "单元测试：直接验证函数 / 服务层逻辑"}
                            >
                              {c.kind === "api" ? "接口" : "单测"}
                            </span>
                          )}
                          <span style={{ marginLeft: "auto", flexShrink: 0, fontSize: 11, color: "#c9cdd4" }}>{c.duration_ms > 0 ? `${c.duration_ms}ms` : "<1ms"}</span>
                        </div>
                      );
                    })}
                    {m.list.length === 0 && <div className="hint-line">无命中用例</div>}
                  </div>
                )}
              </div>
            );
          })
        )}
      </section>

      <section className="set-card" style={{ marginTop: 14 }}>
        <h3>历史趋势</h3>
        <div className="sub">
          近 {history.length} 次聚合 · <span style={{ color: "#165dff" }}>━ 通过率</span> · <span style={{ color: "#00b42a" }}>━ 覆盖率</span> · <span style={{ color: "#8f959e" }}>○ 虚线 = 部分运行（悬停数据点看数值）</span>
        </div>
        {n >= 1 ? (
          <div style={{ position: "relative", maxWidth: 720 }} data-testid="quality-trend-wrap">
            <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", display: "block" }} data-testid="quality-trend">
              {[0, 50, 100].map((v) => (
                <g key={v}>
                  <line x1={PL} x2={W - PR} y1={y(v)} y2={y(v)} stroke="#f0f1f3" strokeWidth={1} />
                  <text x={4} y={y(v) + 4} fontSize={10} fill="#8f959e">{v}</text>
                </g>
              ))}
              {n >= 2 && PCT_KEYS.map((k, idx) => {
                const color = idx === 0 ? "#165dff" : "#00b42a";
                // 逐段画线：任一端为部分运行点 → 该段虚线（方案 B：部分运行点可视化区分）
                return history.slice(1).map((pt, i) => {
                  const dashed = Boolean(pt.partial || history[i].partial);
                  return (
                    <line key={`${k}-${i}`} x1={x(i)} y1={y(history[i][k])} x2={x(i + 1)} y2={y(pt[k])}
                          stroke={color} strokeWidth={2} strokeDasharray={dashed ? "4 3" : undefined} />
                  );
                });
              })}
              {history.map((pt, i) => PCT_KEYS.map((k, idx) => {
                const color = idx === 0 ? "#165dff" : "#00b42a";
                const partial = Boolean(pt.partial);
                const hot = trendHover?.i === i && trendHover?.k === k;
                return (
                  <circle key={`${k}-${pt.ts}`} cx={x(i)} cy={y(pt[k])} r={hot ? 4.5 : partial ? 3.5 : 3}
                          fill={partial ? "#fff" : color} stroke={color} strokeWidth={partial ? 1.5 : 0} />
                );
              }))}
              {/* 悬浮命中层：命中圆直接套在每个真实点上（蓝点/绿点各自可悬浮），即时触发自定义 tooltip */}
              {history.map((pt, i) => PCT_KEYS.map((k) => (
                <circle key={`hit-${k}-${pt.ts}`} cx={x(i)} cy={y(pt[k])} r={11}
                        fill="transparent" pointerEvents="all" style={{ cursor: "pointer" }}
                        onMouseEnter={() => setTrendHover({ i, k })}
                        onMouseLeave={() => setTrendHover((v) => v?.i === i && v?.k === k ? null : v)} />
              )))}
              {history.map((pt, i) => (
                <text key={pt.ts} x={x(i)} y={H - 6} fontSize={9} fill="#8f959e" textAnchor="middle">
                  {pt.ts.slice(5, 16).replace("T", " ")}
                </text>
              ))}
            </svg>
            {trendHover && history[trendHover.i] && (() => {
              const { i, k } = trendHover;
              const pt = history[i];
              const partial = Boolean(pt.partial);
              // 百分比定位（SVG 实际渲染宽高随容器缩放，按 viewBox 比例换算）
              const cxPct = (x(i) / W) * 100;
              const cyPct = (y(pt[k]) / H) * 100;
              return (
                <div style={{
                  position: "absolute",
                  left: `${cxPct}%`,
                  top: `${cyPct}%`,
                  transform: "translateX(-50%) translateY(calc(-100% - 14px))",   // 卡片中心对准数据点，底边距点 14px
                  background: "#1d2129", color: "#fff", borderRadius: 8, padding: "8px 12px",
                  fontSize: 12, lineHeight: 1.7, whiteSpace: "nowrap", pointerEvents: "none",
                  boxShadow: "0 4px 12px rgba(0,0,0,.16)", zIndex: 10,
                }} data-testid="quality-trend-tip">
                  <div style={{ fontWeight: 600 }}>
                    {partial ? `部分运行（${(pt.scopes || []).join(" + ") || "范围未知"}）` : "全量运行"}
                    <span style={{ opacity: .7, fontWeight: 400, marginLeft: 6 }}>{pt.ts.slice(5, 16).replace("T", " ")}</span>
                  </div>
                  <div>通过率 <b style={{ color: "#4cceff" }}>{pt.pass_rate}%</b> · 覆盖率 <b style={{ color: "#7be188" }}>{pt.coverage_pct}%</b></div>
                  <div style={{ opacity: .85 }}>e2e {pt.e2e_passed}/{pt.e2e_total} 套件通过</div>
                </div>
              );
            })()}
          </div>
        ) : (
          <div className="hint-line">暂无历史数据，运行一次测试后生成趋势。</div>
        )}
      </section>
    </div>
  );
}
