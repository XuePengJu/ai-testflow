/**
 * M3：自动化执行面板（TaskDetailDrawer 第 4 个 Tab「自动化」，kind=e2e 或 has_auto 时显示）。
 *
 * - 执行按钮 → POST run-auto（409/400 已由 apiJson toast）→ 对活跃 run 2s 轮询 GET /api/executions/{run_id}
 *   （running 时 progress/total 进度条，终态停止轮询并刷新列表）
 * - 执行列表（新→旧）：状态徽标 / 通过率 passed-total / 耗时 / 触发方式；点击行内展开报告视图
 * - 报告视图：汇总条（total/passed/failed/skipped/耗时/环境）+ 用例明细（outcome 图标 ✓✗⊘、
 *   编号、标题、耗时；failed 行点击展开 error 文案 + 失败截图缩略，点击 lightbox 放大）
 * - 重试按钮（仅终态 run）→ POST retry
 *
 * 截图鉴权：<img> 无法带 Bearer 头，统一走 fetchExecutionImage（fetch blob → objectURL）。
 * 纯函数（getReport/runBadgeInfo/fmtDuration/rateText/isTerminal）导出供 dev 自测脚本使用。
 */
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { Loader2, Play, RotateCcw } from "lucide-react";
import {
  fetchExecutionImage,
  fetchPageScreenshot,
  fetchTaskPages,
  fetchTaskVideo,
  getExecution,
  getTaskExecutions,
  retryExecution,
  runTaskAuto,
  toast,
} from "../../api/client";
import type { ExecutionCase, ExecutionReport, ExecutionRunOut, PageDescInfo, Task } from "../../types";
import HealSection from "./HealSection";

/* ===== 纯函数（无副作用，dev 自测脚本 scripts/exec-selftest.mjs 直接调用） ===== */

/**
 * 兼容取报告对象：契约 3 约定后端返回解析后的 report 对象；
 * 防御性兜底：若后端漏解析直接回传 report_json 字符串，前端就地 JSON.parse，避免整块报告渲染空白。
 */
export function getReport(run: ExecutionRunOut | null): ExecutionReport | null {
  if (!run) return null;
  if (run.report && typeof run.report === "object" && Array.isArray((run.report as ExecutionReport).cases ?? [])) {
    return run.report;
  }
  const raw = (run as unknown as { report_json?: unknown }).report_json;
  if (typeof raw === "string" && raw.trim()) {
    try {
      return JSON.parse(raw) as ExecutionReport;
    } catch {
      return null;
    }
  }
  return null;
}

/** 状态徽标：pending 灰 / running 蓝 / completed 绿（含失败→黄）/ failed 红 */
export function runBadgeInfo(run: Pick<ExecutionRunOut, "status" | "failed">): {
  text: string;
  cls: string;
  style?: CSSProperties;
} {
  switch (run.status) {
    case "pending":
      return { text: "排队中", cls: "pill pill-sub" };
    case "running":
      return { text: "执行中", cls: "pill", style: { background: "#e8f3ff", color: "#165dff" } };
    case "completed":
      return run.failed > 0
        ? { text: `完成 · 失败 ${run.failed}`, cls: "pill pill-run" }
        : { text: "全部通过", cls: "pill pill-ok" };
    default:
      return { text: "执行失败", cls: "pill pill-fail" };
  }
}

/** 耗时格式化：0/null → —，<1s 显示 ms */
export function fmtDuration(ms: number | null | undefined): string {
  if (!ms || ms <= 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** 通过率文案：pending 无数据 → —，其余 passed/total */
export function rateText(run: Pick<ExecutionRunOut, "status" | "passed" | "total">): string {
  if (run.status === "pending") return "—";
  return `${run.passed}/${run.total}`;
}

/** 终态判定（仅终态可展开报告/重试） */
export function isTerminal(run: Pick<ExecutionRunOut, "status">): boolean {
  return run.status === "completed" || run.status === "failed";
}

/* ===== 子组件 ===== */

/** 失败截图缩略图：fetch blob（Bearer）→ objectURL，卸载时 revoke；点击回调放大 */
function ShotThumb({ runId, path, onZoom }: { runId: string; path: string; onZoom: (url: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    let made: string | null = null;
    void fetchExecutionImage(runId, path).then((u) => {
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
  }, [runId, path]);

  if (failed) return <div className="hint-line">截图加载失败（{path}）</div>;
  if (!url) return <div className="hint-line">截图加载中…</div>;
  return (
    <img
      src={url}
      alt="失败截图"
      title="点击放大"
      onClick={() => onZoom(url)}
      style={{
        display: "block",
        marginTop: 8,
        maxHeight: 96,
        borderRadius: 6,
        border: "1px solid #e5e6eb",
        cursor: "zoom-in",
      }}
    />
  );
}

/** 用例明细行：outcome 图标 + 编号 + 标题 + 耗时；failed 行点击展开 error + 截图 */
function CaseRow({ c, runId, onZoom }: { c: ExecutionCase; runId: string; onZoom: (url: string) => void }) {
  const [open, setOpen] = useState(false);
  const isFailed = c.outcome === "failed";
  const icon =
    c.outcome === "passed" ? (
      <span style={{ color: "#00b42a" }}>✓</span>
    ) : isFailed ? (
      <span style={{ color: "#d83931" }}>✗</span>
    ) : (
      <span style={{ color: "#8f959e" }}>⊘</span>
    );
  return (
    <div style={{ borderBottom: "1px dashed #f0f1f3" }}>
      <div
        onClick={() => isFailed && setOpen((o) => !o)}
        title={isFailed ? "点击查看错误详情与截图" : undefined}
        style={{
          display: "flex",
          gap: 10,
          alignItems: "center",
          padding: "6px 2px",
          fontSize: 12.5,
          cursor: isFailed ? "pointer" : "default",
        }}
      >
        <span style={{ width: 16, textAlign: "center", fontWeight: 600 }}>{icon}</span>
        <span style={{ color: "#165dff", fontWeight: 600, minWidth: 56 }}>{c.case_id || "—"}</span>
        <span style={{ flex: 1, color: "#1f2329", wordBreak: "break-word" }}>{c.title || c.node_id || "—"}</span>
        <span style={{ color: "#8f959e", whiteSpace: "nowrap" }}>{fmtDuration(c.duration_ms)}</span>
      </div>
      {open && isFailed && (
        <div style={{ padding: "0 2px 10px 26px" }}>
          {c.error ? (
            <pre
              style={{
                margin: 0,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
                fontSize: 12,
                lineHeight: 1.6,
                color: "#d83931",
                background: "#fff7f6",
                borderRadius: 6,
                padding: "8px 10px",
              }}
            >
              {c.error}
            </pre>
          ) : (
            <div className="hint-line">无错误详情</div>
          )}
          {/* screenshot 为 run 内相对路径（shots/xxx.png），可能为 null（进程级失败未触发 hook） */}
          {c.screenshot ? (
            <ShotThumb runId={runId} path={c.screenshot} onZoom={onZoom} />
          ) : (
            <div className="hint-line">无失败截图</div>
          )}
        </div>
      )}
    </div>
  );
}

/** 内嵌报告视图：汇总条 + 用例明细；running/无 report 时给占位说明 */
function ReportView({ run, onZoom }: { run: ExecutionRunOut; onZoom: (url: string) => void }) {
  const rep = getReport(run);
  if (!rep) {
    return (
      <div className="hint-line" style={{ padding: "8px 2px" }}>
        {run.status === "running" || run.status === "pending"
          ? `报告生成中…（${run.progress}/${run.total}）`
          : `暂无报告${run.error ? `：${run.error}` : "（进程级失败，未产出报告）"}`}
      </div>
    );
  }
  // 汇总条字段防御：summary 缺失时补零，避免 undefined 上模板
  const s = rep.summary ?? { total: 0, passed: 0, failed: 0, skipped: 0, duration_ms: 0 };
  const cases = Array.isArray(rep.cases) ? rep.cases : [];
  const env = rep.environment;
  return (
    <div style={{ padding: "10px 2px 4px" }}>
      {/* 汇总条 */}
      <div
        style={{
          display: "flex",
          gap: 16,
          flexWrap: "wrap",
          alignItems: "center",
          fontSize: 12.5,
          color: "#4e5969",
          marginBottom: 10,
          background: "#f7f8fa",
          borderRadius: 8,
          padding: "8px 12px",
        }}
      >
        <span>总计 <b>{s.total}</b></span>
        <span style={{ color: "#00b42a" }}>通过 <b>{s.passed}</b></span>
        <span style={{ color: "#d83931" }}>失败 <b>{s.failed}</b></span>
        <span>跳过 <b>{s.skipped}</b></span>
        <span>耗时 <b>{fmtDuration(s.duration_ms)}</b></span>
        {env?.browser && <span>浏览器 {env.browser}</span>}
        {env?.base_url && (
          <span className="dash" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={env.base_url}>
            {env.base_url}
          </span>
        )}
      </div>
      {/* M4 自愈：触发过自愈循环（heal 存在）→ 报告尾部展示自愈过程与疑似缺陷 */}
      {rep.heal && <HealSection heal={rep.heal} />}
      {/* 用例明细 */}
      {cases.length === 0 ? (
        <div className="hint-line">报告内无用例明细</div>
      ) : (
        cases.map((c, i) => <CaseRow key={`${c.case_id || "case"}-${c.node_id || i}`} c={c} runId={run.id} onZoom={onZoom} />)
      )}
    </div>
  );
}

/* ===== 页面探索区块（e2e crawler 产物可视化） ===== */

/** 从 crawler 步骤日志的 details（input_summary JSON）提取 login/mode；拿不到返回 null */
export function getCrawlMeta(task: Task): { login: string; mode: string } | null {
  const step = task.steps?.find((s) => s.name === "crawler");
  const raw = step?.input_summary;
  if (!raw) return null;
  try {
    const obj = JSON.parse(raw) as { login?: unknown; mode?: unknown };
    if (typeof obj.login === "string" && typeof obj.mode === "string") {
      return { login: obj.login, mode: obj.mode };
    }
  } catch {
    /* 非 JSON details（旧任务），无元信息 */
  }
  return null;
}

/** 页面截图缩略图：fetch blob（Bearer）→ objectURL；无截图渲染占位，点击放大。
 *  导出供聊天流 TaskStepsCard 的 crawler 步骤可视化复用。 */
export function PageShot({ taskId, name, onZoom }: { taskId: string; name: string; onZoom: (url: string) => void }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
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
    height: 96,
    borderRadius: 6,
    border: "1px solid #e5e6eb",
    background: "#f7f8fa",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden",
  };
  if (failed) return <div style={boxStyle} className="hint-line">无截图</div>;
  if (!url) return <div style={boxStyle} className="hint-line">截图加载中…</div>;
  return (
    <div style={boxStyle}>
      <img
        src={url}
        alt={name}
        title="点击放大"
        onClick={() => onZoom(url)}
        style={{ display: "block", width: "100%", height: "100%", objectFit: "cover", objectPosition: "top", cursor: "zoom-in" }}
      />
    </div>
  );
}

/** 页面探索区块：小结行（抓取 N 页 + 登录/模式徽标 + 录屏入口）+ 页面卡片网格；空数据不渲染 */
function PagesSection({ task, onZoom }: { task: Task; onZoom: (url: string) => void }) {
  const [pages, setPages] = useState<PageDescInfo[] | null>(null); // null = 加载中
  const [videoAvailable, setVideoAvailable] = useState(false);
  const [videoUrl, setVideoUrl] = useState<string | null>(null); // 录屏 lightbox（blob objectURL）

  useEffect(() => {
    let live = true;
    void fetchTaskPages(task.id).then((resp) => {
      if (live) {
        setPages(resp?.pages ?? []);
        setVideoAvailable(!!resp?.video_available);
      }
    });
    return () => {
      live = false;
    };
  }, [task.id]);

  // 关闭录屏 lightbox 时释放 blob URL
  useEffect(() => {
    if (!videoUrl) return;
    return () => URL.revokeObjectURL(videoUrl);
  }, [videoUrl]);

  if (pages === null || pages.length === 0) return null; // 空数据不渲染

  const meta = getCrawlMeta(task);
  const loginBadge =
    meta?.login === "success" ? { text: "登录成功", style: { background: "#e8ffea", color: "#00b42a" } }
    : meta?.login === "failed" ? { text: "登录未生效", style: { background: "#fff7e8", color: "#d25f00" } }
    : null;
  const modeBadge =
    meta?.mode === "playwright" ? { text: "浏览器探索" }
    : meta?.mode === "static" ? { text: "静态抓取" }
    : null;

  return (
    <div className="set-card" style={{ marginBottom: 12 }}>
      <h3 style={{ margin: 0 }}>页面探索</h3>
      <div className="sub" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span>共抓取 <b>{pages.length}</b> 个页面</span>
        {loginBadge && <span className="pill" style={loginBadge.style}>{loginBadge.text}</span>}
        {modeBadge && <span className="pill pill-sub">{modeBadge.text}</span>}
        {videoAvailable && (
          <button
            type="button"
            className="btn-ghost"
            style={{ height: 24, padding: "0 10px", fontSize: 12 }}
            title="回放 crawler 浏览器探索全过程（webm 录屏）"
            onClick={() => {
              void fetchTaskVideo(task.id).then((u) => {
                if (u) setVideoUrl(u);
              });
            }}
          >
            ▶ 观看探索录屏
          </button>
        )}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 10 }}>
        {pages.map((p, i) => (
          <div
            key={`${p.url}-${i}`}
            title={p.title || p.url}
            style={{ border: "1px solid #f0f1f3", borderRadius: 8, padding: 8, background: "#fff" }}
          >
            {p.screenshot ? (
              <PageShot taskId={task.id} name={p.screenshot.split("/").pop() || ""} onZoom={onZoom} />
            ) : (
              <div style={{ height: 96, borderRadius: 6, border: "1px dashed #e5e6eb", display: "flex", alignItems: "center", justifyContent: "center" }} className="hint-line">
                无截图
              </div>
            )}
            <div style={{ marginTop: 6, fontSize: 12.5, fontWeight: 600, color: "#1f2329", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {p.title || "(无标题)"}
            </div>
            <div className="dash" style={{ fontSize: 12, color: "#8f959e", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }} title={p.url}>
              {p.url}
            </div>
          </div>
        ))}
      </div>

      {/* 探索录屏播放 lightbox（点击遮罩关闭；点击视频本体不关闭，保证 controls 可用） */}
      {videoUrl && (
        <div
          onClick={() => setVideoUrl(null)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 3000,
            background: "rgba(15,18,25,.72)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
          }}
        >
          <video
            src={videoUrl}
            controls
            autoPlay
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: "92vw", maxHeight: "92vh", borderRadius: 8, boxShadow: "0 8px 40px rgba(0,0,0,.4)", background: "#000" }}
          />
        </div>
      )}
    </div>
  );
}

/* ===== 主面板 ===== */

interface Props {
  task: Task;
}

export default function ExecutionPanel({ task }: Props) {
  const [runs, setRuns] = useState<ExecutionRunOut[] | null>(null); // null = 列表加载中
  const [starting, setStarting] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null); // 展开报告的 run
  const [detail, setDetail] = useState<ExecutionRunOut | null>(null); // 展开 run 的实时详情（轮询刷新）
  const [zoomUrl, setZoomUrl] = useState<string | null>(null); // lightbox
  const openIdRef = useRef<string | null>(null);
  openIdRef.current = openId;

  const loadList = useCallback(async () => {
    const list = await getTaskExecutions(task.id);
    setRuns(Array.isArray(list) ? list : []);
  }, [task.id]);

  // 挂载拉列表；activeRun 由列表推导，存在即触发轮询 effect
  useEffect(() => {
    void loadList();
  }, [loadList]);

  /** 当前活跃 run（pending/running，取最新一条） */
  const activeRun = useMemo(
    () => runs?.find((r) => r.status === "pending" || r.status === "running") ?? null,
    [runs],
  );

  // 2s 轮询活跃 run：更新列表行 + 展开中的详情；终态停表、toast、刷列表
  useEffect(() => {
    if (!activeRun) return;
    const runId = activeRun.id;
    let stopped = false;
    const timer = window.setInterval(() => {
      void (async () => {
        if (stopped) return;
        const d = await getExecution(runId);
        if (!d || stopped) return;
        setRuns((prev) => (prev ? prev.map((r) => (r.id === d.id ? d : r)) : prev));
        if (openIdRef.current === d.id) setDetail(d);
        if (d.status === "completed" || d.status === "failed") {
          stopped = true;
          window.clearInterval(timer);
          toast(
            d.status === "completed"
              ? `自动化执行完成：通过 ${d.passed}/${d.total}`
              : `自动化执行失败${d.error ? `：${d.error}` : ""}`,
          );
          void loadList();
        }
      })();
    }, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeRun?.id, loadList]);

  // 点击行：展开/收起；展开时先用列表数据即时渲染，再拉最新详情
  function toggleRun(r: ExecutionRunOut) {
    if (openId === r.id) {
      setOpenId(null);
      setDetail(null);
      return;
    }
    setOpenId(r.id);
    setDetail(runs?.find((x) => x.id === r.id) ?? r);
    void getExecution(r.id).then((d) => {
      if (d) setDetail(d);
    });
  }

  async function onStart() {
    setStarting(true);
    try {
      const r = await runTaskAuto(task.id);
      if (!r) return; // 400（无用例）/409（进行中）已 toast
      toast("自动化执行已排队");
      await loadList();
    } finally {
      setStarting(false);
    }
  }

  async function onRetry(run: ExecutionRunOut) {
    const r = await retryExecution(run.id);
    if (!r) return; // 409（原 run 运行中）已 toast
    toast("已重新发起执行");
    await loadList();
  }

  if (runs === null) return <div className="drawer-empty">加载执行记录…</div>;

  return (
    <div className="exec-tab">
      {/* 页面探索：crawler 产物可视化（空数据时不渲染） */}
      <PagesSection task={task} onZoom={setZoomUrl} />

      {/* 顶部：执行按钮 + 进度 */}
      <div className="set-card" style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <div>
            <h3 style={{ margin: 0 }}>自动化执行</h3>
            <div className="sub" style={{ marginBottom: 0 }}>
              {activeRun
                ? `正在执行（${activeRun.progress}/${activeRun.total || "?"}）…完成后自动刷新`
                : "基于已生成的 Playwright 脚本，在浏览器中自动执行全部用例并产出报告"}
            </div>
          </div>
          <button
            type="button"
            className="btn-primary btn-md"
            disabled={starting || !!activeRun}
            title={activeRun ? "已有执行进行中" : undefined}
            onClick={() => void onStart()}
          >
            {activeRun ? <Loader2 size={14} className="spin" /> : <Play size={14} />}
            {activeRun ? "执行中…" : starting ? "提交中…" : "▶ 执行"}
          </button>
        </div>
        {activeRun && activeRun.total > 0 && (
          <div style={{ marginTop: 10, height: 8, background: "#f2f3f5", borderRadius: 4, overflow: "hidden" }}>
            <div
              style={{
                width: `${Math.min(100, Math.max(0, (activeRun.progress / activeRun.total) * 100))}%`,
                height: "100%",
                background: "#165dff",
                borderRadius: 4,
                transition: "width .6s ease",
              }}
            />
          </div>
        )}
      </div>

      {/* 执行列表（新→旧，后端契约保证顺序） */}
      {runs.length === 0 ? (
        <div className="drawer-empty">尚未执行过自动化，点击上方「执行」开始</div>
      ) : (
        <div className="case-table-wrap">
          <table className="case-table">
            <colgroup>
              <col style={{ width: "16%" }} />
              <col style={{ width: "12%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "18%" }} />
              <col style={{ width: "34%" }} />
            </colgroup>
            <thead>
              <tr>
                <th>状态</th>
                <th>通过率</th>
                <th>耗时</th>
                <th>触发</th>
                <th>发起时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const badge = runBadgeInfo(r);
                return (
                  <Fragment key={r.id}>
                    <tr
                      onClick={() => toggleRun(r)}
                      style={{ cursor: "pointer" }}
                      title="点击展开/收起报告"
                    >
                      <td>
                        <span className={badge.cls} style={badge.style}>{badge.text}</span>
                        {/* M4 自愈：heal_round>0 显示轮次徽标（running 期间轮询可见递增） */}
                        {!!r.heal_round && r.heal_round > 0 && (
                          <span className="pill" style={{ background: "#fff7e8", color: "#d25f00", marginLeft: 6 }}>
                            自愈 {r.heal_round} 轮
                          </span>
                        )}
                      </td>
                      <td>{rateText(r)}</td>
                      <td>{fmtDuration(r.duration_ms)}</td>
                      <td>{r.trigger === "retry" ? "重试" : "手动"}</td>
                      <td>{(r.created_at || "").slice(5, 16).replace("T", " ") || "—"}</td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <span className="pill pill-sub">{openId === r.id ? "收起报告" : "查看报告"}</span>
                        {isTerminal(r) && (
                          <button
                            type="button"
                            className="btn-ghost"
                            style={{ height: 24, padding: "0 8px", fontSize: 12, marginLeft: 8 }}
                            title="复制配置重新执行"
                            onClick={() => void onRetry(r)}
                          >
                            <RotateCcw size={12} /> 重试
                          </button>
                        )}
                      </td>
                    </tr>
                    {openId === r.id && (
                      <tr>
                        <td colSpan={6} style={{ background: "#fafbfc" }}>
                          <ReportView run={detail && detail.id === r.id ? detail : r} onZoom={setZoomUrl} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 截图 lightbox（点击遮罩关闭） */}
      {zoomUrl && (
        <div
          onClick={() => setZoomUrl(null)}
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
            src={zoomUrl}
            alt="失败截图（放大）"
            style={{ maxWidth: "92vw", maxHeight: "92vh", borderRadius: 8, boxShadow: "0 8px 40px rgba(0,0,0,.4)" }}
          />
        </div>
      )}
    </div>
  );
}
