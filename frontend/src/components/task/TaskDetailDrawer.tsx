/**
 * M3：任务详情抽屉（720px 右滑 + 遮罩）。
 * - 3 Tab：用例列表 / 思维导图 / 导出&迭代
 * - running 状态：显示步骤进度卡（复用 TaskStepsCard）+ 2s 详情轮询（taskStore.pollDrawer）
 * - 导图节点点击 → 切 Tab1 + focusCase 定位高亮
 */
import { useEffect, useState } from "react";
import { useTaskStore } from "../../store/taskStore";
import { statusBadge } from "../chat/TaskStepsCard";
import TaskStepsCard from "../chat/TaskStepsCard";
import CaseListTab from "./CaseListTab";
import MindMapTab from "./MindMapTab";
import ExportTab from "./ExportTab";

type TabKey = "cases" | "mindmap" | "export";

export default function TaskDetailDrawer() {
  const drawerTaskId = useTaskStore((s) => s.drawerTaskId);
  const detail = useTaskStore((s) => s.detail);
  const detailLoading = useTaskStore((s) => s.detailLoading);
  const closeDetail = useTaskStore((s) => s.closeDetail);
  const focusCase = useTaskStore((s) => s.focusCase);
  const focusCaseId = useTaskStore((s) => s.focusCaseId);
  const focusCaseSeq = useTaskStore((s) => s.focusCaseSeq);
  const [tab, setTab] = useState<TabKey>("cases");

  // Esc 关闭
  useEffect(() => {
    if (!drawerTaskId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDetail();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerTaskId, closeDetail]);

  // 导图跳用例：切 Tab + 定位（focusCase 内部 +1 触发）
  function handleSelectCase(caseId: string) {
    setTab("cases");
    focusCase(caseId);
  }

  if (!drawerTaskId) return null;

  const open = !!drawerTaskId;
  const t = detail;
  const casesCount = t?.cases?.length || t?.cases_count || 0;

  return (
    <div className={`drawer-mask ${open ? "show" : ""}`} onClick={closeDetail}>
      <div className={`task-drawer ${open ? "show" : ""}`} data-task-id={t?.id || ""} onClick={(e) => e.stopPropagation()}>
        {detailLoading || !t ? (
          <div className="drawer-empty" style={{ margin: "auto" }}>加载任务详情…</div>
        ) : (
          <>
            <div className="drawer-head">
              <div className="drawer-title-row">
                <span className="drawer-title">{t.name}</span>
                <span className={`pill pill-${statusBadge(t.status).cls}`}>{statusBadge(t.status).text}</span>
                {t.parent_task_id && <span className="pill pill-sub" title={`迭代自 ${t.parent_task_id}`}>迭代</span>}
                <button type="button" className="drawer-close" onClick={closeDetail} title="关闭（Esc）">
                  ×
                </button>
              </div>
              <div className="drawer-meta">
                {t.status === "completed" && <span>{casesCount} 个用例</span>}
                {t.duration_ms > 0 && <span>耗时 {(t.duration_ms / 1000).toFixed(1)}s</span>}
                <span>{t.kind === "business" ? "业务用例" : "接口用例"}</span>
                <span>{t.source_type === "iterate" ? "迭代生成" : t.source_type === "chat" ? "对话生成" : t.source_type}</span>
              </div>
            </div>

            {(t.status === "running" || t.status === "pending") && (
              <div className="drawer-running">
                <TaskStepsCard task={t} />
              </div>
            )}

            <div className="drawer-tabs">
              <button
                type="button"
                className={`dtab ${tab === "cases" ? "active" : ""}`}
                onClick={() => setTab("cases")}
              >
                用例列表{t.cases?.length ? `（${t.cases.length}）` : ""}
              </button>
              <button
                type="button"
                className={`dtab ${tab === "mindmap" ? "active" : ""}`}
                onClick={() => setTab("mindmap")}
              >
                思维导图
              </button>
              <button
                type="button"
                className={`dtab ${tab === "export" ? "active" : ""}`}
                onClick={() => setTab("export")}
              >
                导出 / 迭代
              </button>
            </div>

            <div className="drawer-body">
              {tab === "cases" && <CaseListTab task={t} focusCaseId={focusCaseId} focusSeq={focusCaseSeq} />}
              {tab === "mindmap" && <MindMapTab task={t} onSelectCase={handleSelectCase} />}
              {tab === "export" && <ExportTab task={t} />}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
