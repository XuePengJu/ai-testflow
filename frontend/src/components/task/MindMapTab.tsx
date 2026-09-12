/**
 * M3：思维导图 Tab（mind-elixir 4.6.2 npm 版）。
 * 数据来源：前端从 task.cases 现场构建树（后端无 mind_map 字段）：
 *   root(任务名) → 模块 → 用例（case_id + 标题）
 * 节点点击（用例节点）→ 跳「用例列表」Tab 并定位高亮。
 * 样式由 mind-elixir 构建产物内嵌（vite-plugin-css-injected-by-js），无需 import css。
 */
import { useEffect, useRef } from "react";
import MindElixir from "mind-elixir";
import type { MindElixirInstance, MindElixirData } from "mind-elixir";
import type { CaseItem, Task } from "../../types";

interface Props {
  task: Task;
  /** 用例节点被点击（case_id 不带前缀） */
  onSelectCase: (caseId: string) => void;
}

const CASE_PREFIX = "case:";

/** cases → mind-elixir 树（root → module → case） */
function buildMapData(task: Task, cases: CaseItem[]): MindElixirData {
  const byModule = new Map<string, CaseItem[]>();
  for (const c of cases) {
    const mod = (c.module || "").trim() || "未分组";
    const list = byModule.get(mod) || [];
    list.push(c);
    byModule.set(mod, list);
  }
  return {
    nodeData: {
      id: "root",
      topic: task.name,
      children: [...byModule.entries()].map(([mod, list], i) => ({
        id: `mod-${i}`,
        topic: `${mod}（${list.length}）`,
        children: list.map((c) => ({
          id: CASE_PREFIX + c.case_id,
          topic: `${c.case_id} ${c.title}`,
          tags: c.priority ? [c.priority] : [],
        })),
      })),
    },
  };
}

export default function MindMapTab({ task, onSelectCase }: Props) {
  const elRef = useRef<HTMLDivElement | null>(null);
  const mindRef = useRef<MindElixirInstance | null>(null);
  const cases = task.cases || [];
  const canRender = cases.length > 0;

  useEffect(() => {
    if (!canRender || !elRef.current) return;
    const mind = new MindElixir({
      el: elRef.current,
      direction: MindElixir.SIDE,
      locale: "zh_CN",
      draggable: false,
      editable: false,
      contextMenu: false,
      toolBar: false,
      keypress: false,
    }) as unknown as MindElixirInstance;
    mind.init(buildMapData(task, cases));
    mind.bus.addListener("selectNode", (obj: unknown) => {
      // mind-elixir 4.x：selectNode 第一个参数就是 NodeObj（含 id），非 DOM 元素
      const id = (obj as { id?: string } | null)?.id || "";
      if (id.startsWith(CASE_PREFIX)) onSelectCase(id.slice(CASE_PREFIX.length));
    });
    mindRef.current = mind;
    return () => {
      try {
        mind.destroy();
      } catch {
        /* 卸载兜底 */
      }
      mindRef.current = null;
    };
    // task.id + cases 数变化才重建（导图渲染重，不随其他字段抖动）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id, cases.length]);

  if (!canRender) {
    return <div className="drawer-empty">暂无用例，无法生成思维导图</div>;
  }

  return (
    <div className="mindmap-wrap">
      <div className="mindmap-hint">点击用例节点可跳转到用例列表</div>
      <div ref={elRef} className="map-container" style={{ flex: 1, minHeight: 0 }} />
    </div>
  );
}
