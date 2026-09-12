/**
 * M3：用例列表 Tab。
 * - 统计条：总数 / 优先级分布 / 类型分布（从 cases 现算，后端 TaskOut 不含 report）
 * - 搜索：按 case_id / 标题 / 模块过滤
 * - 用例卡片：前置条件 / 步骤+预期成对展示 / 预期结果 / 测试数据
 * - focusCaseId（导图节点点击跳转）：滚动定位 + 高亮
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { Task } from "../../types";

interface Props {
  task: Task;
  focusCaseId: string | null;
  focusSeq: number;
}

const PRIORITY_CLS: Record<string, string> = { P0: "fail", P1: "run", P2: "sub" };

export default function CaseListTab({ task, focusCaseId, focusSeq }: Props) {
  const cases = task.cases || [];
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const listRef = useRef<HTMLDivElement | null>(null);

  const stats = useMemo(() => {
    const pri: Record<string, number> = {};
    const typ: Record<string, number> = {};
    const mod: Record<string, number> = {};
    for (const c of cases) {
      if (c.priority) pri[c.priority] = (pri[c.priority] || 0) + 1;
      if (c.case_type) typ[c.case_type] = (typ[c.case_type] || 0) + 1;
      const m = (c.module || "").trim() || "未分组";
      mod[m] = (mod[m] || 0) + 1;
    }
    return { pri, typ, mod };
  }, [cases]);

  const filtered = useMemo(() => {
    const kw = q.trim().toLowerCase();
    if (!kw) return cases;
    return cases.filter(
      (c) =>
        c.case_id.toLowerCase().includes(kw) ||
        (c.title || "").toLowerCase().includes(kw) ||
        (c.module || "").toLowerCase().includes(kw),
    );
  }, [cases, q]);

  // 导图跳转：展开 + 滚动 + 高亮
  useEffect(() => {
    if (!focusCaseId || !listRef.current) return;
    setExpanded((prev) => {
      const n = new Set(prev);
      n.add(focusCaseId);
      return n;
    });
    const t = window.setTimeout(() => {
      const el = listRef.current?.querySelector(`[data-case-id="${focusCaseId}"]`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("flash");
        window.setTimeout(() => el.classList.remove("flash"), 1600);
      }
    }, 80);
    return () => window.clearTimeout(t);
  }, [focusCaseId, focusSeq]);

  if (cases.length === 0) {
    return <div className="drawer-empty">该任务暂无用例数据</div>;
  }

  return (
    <div className="case-tab">
      <div className="case-stats">
        <span className="cs-total">共 {cases.length} 条</span>
        {Object.entries(stats.pri)
          .sort()
          .map(([p, n]) => (
            <span key={p} className={`pill pill-${PRIORITY_CLS[p] || "sub"}`}>
              {p}×{n}
            </span>
          ))}
        {Object.entries(stats.typ).map(([t, n]) => (
          <span key={t} className="pill pill-sub">
            {t}×{n}
          </span>
        ))}
      </div>
      <div className="case-search">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜索用例编号 / 标题 / 模块…"
        />
      </div>
      <div className="case-list" ref={listRef}>
        {filtered.length === 0 ? (
          <div className="drawer-empty">无匹配用例</div>
        ) : (
          filtered.map((c) => {
            const open = expanded.has(c.case_id);
            const steps = c.steps || [];
            const exps = c.step_expectations || [];
            return (
              <div key={c.case_id} className={`case-card ${open ? "open" : ""}`} data-case-id={c.case_id}>
                <button
                  type="button"
                  className="case-head"
                  onClick={() =>
                    setExpanded((prev) => {
                      const n = new Set(prev);
                      if (n.has(c.case_id)) n.delete(c.case_id);
                      else n.add(c.case_id);
                      return n;
                    })
                  }
                >
                  <span className="case-id">{c.case_id}</span>
                  <span className="case-title">{c.title}</span>
                  {c.priority && (
                    <span className={`pill pill-${PRIORITY_CLS[c.priority] || "sub"}`}>{c.priority}</span>
                  )}
                  {c.case_type && <span className="pill pill-sub">{c.case_type}</span>}
                  <span className="case-arrow">{open ? "▾" : "▸"}</span>
                </button>
                {open && (
                  <div className="case-body">
                    {c.module && (
                      <div className="case-row">
                        <span className="case-k">模块</span>
                        <span>{c.module}</span>
                      </div>
                    )}
                    {c.pre_condition && (
                      <div className="case-row">
                        <span className="case-k">前置</span>
                        <span>{c.pre_condition}</span>
                      </div>
                    )}
                    {steps.length > 0 && (
                      <div className="case-steps">
                        {steps.map((s, i) => (
                          <div key={i} className="case-step">
                            <span className="case-step-n">{i + 1}</span>
                            <div className="case-step-main">
                              <div>{s}</div>
                              {exps[i] && <div className="case-step-exp">预期：{exps[i]}</div>}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    {c.expected && (
                      <div className="case-row">
                        <span className="case-k">预期</span>
                        <span>{c.expected}</span>
                      </div>
                    )}
                    {c.test_data && (
                      <div className="case-row">
                        <span className="case-k">数据</span>
                        <span>{c.test_data}</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
