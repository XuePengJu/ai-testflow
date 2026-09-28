/**
 * W3 M9 测试中心 · 顶部指标卡（3 张）：本周通过率 / 本周运行次数 / 未修复失败数。
 *
 * 数据口径（无新后端，全部来自质量报告现有 API 聚合）：
 * - 最新通过率：/api/quality/summary 的展示口径通过率（核心接口 + e2e，平台自测单测已过滤）；
 * - 本周运行次数：history 中落在本周的数据点条数（每次聚合 = 一次运行）；
 * - 未修复失败数：summary 展示口径内 pytest 失败数 + e2e 未通过套件数。
 * 所有登录角色可见（含访客）；「运行测试」按钮的 admin-only 规则在 QualityBoard 内保持不变。
 * 铁律不变：数字全部来自后端实测聚合，禁止写死展示值。
 */
import { useEffect, useState } from "react";
import { CheckCircle2, CalendarRange, Bug } from "lucide-react";
import { getQualityHistory, getQualitySummary } from "../../api/client";
import type { QualityHistoryPoint, QualitySummary } from "../../types";

/** 本周一 00:00（本地时区） */
function weekStart(): Date {
  const d = new Date();
  const day = d.getDay() === 0 ? 7 : d.getDay(); // 周日=7
  d.setDate(d.getDate() - (day - 1));
  d.setHours(0, 0, 0, 0);
  return d;
}

export default function TestCenterMetrics() {
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [history, setHistory] = useState<QualityHistoryPoint[]>([]);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const [s, h] = await Promise.all([getQualitySummary(), getQualityHistory()]);
      if (!alive) return;
      if (s) setSummary(s.summary);
      if (h) setHistory(h);
    })();
    return () => { alive = false; };
  }, []);

  const start = weekStart().getTime();
  const weekPts = history.filter((p) => {
    const t = new Date(p.ts).getTime();
    return Number.isFinite(t) && t >= start;
  });
  // 通过率用展示口径（后端 /quality/summary 已过滤为核心接口 + e2e，平台自测单测不入展示面）
  const passRate = summary ? `${summary.pytest.pass_rate}%` : "—";
  const runCount = String(weekPts.length);
  // 未修复失败 = 展示口径内 pytest 失败用例 + e2e 未通过套件（无数据时显示 —）
  const broken = summary
    ? String(summary.pytest.failed + Math.max(0, summary.e2e.total - summary.e2e.passed))
    : "—";

  return (
    <section className="stats-row" style={{ width: "100%" }} data-testid="tc-metrics">
      <div className="stat-card">
        <div className="stat-icon green"><CheckCircle2 size={22} /></div>
        <div className="stat-body">
          <div className="s-num">{passRate}</div>
          <div className="s-label">最新通过率（核心接口 + e2e）</div>
        </div>
      </div>
      <div className="stat-card">
        <div className="stat-icon blue"><CalendarRange size={22} /></div>
        <div className="stat-body">
          <div className="s-num">{runCount}</div>
          <div className="s-label">本周运行次数</div>
        </div>
      </div>
      <div className="stat-card">
        <div className={"stat-icon " + (broken !== "—" && broken !== "0" ? "red" : "orange")}>
          <Bug size={22} />
        </div>
        <div className="stat-body">
          <div className="s-num">{broken}</div>
          <div className="s-label">未修复失败数（pytest 用例 + e2e 套件）</div>
        </div>
      </div>
    </section>
  );
}
