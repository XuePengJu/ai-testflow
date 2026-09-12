/**
 * M3 / M5-fix：思维导图 Tab（mind-elixir 4.6.2 npm 版）。
 * 数据来源：前端从 task.cases 现场构建树（后端无 mind_map 字段）：
 *   root(任务名) → 模块 → 用例（case_id + 标题）
 * M5-fix：
 *   - 容器显式宽高 + ResizeObserver：容器尺寸变化时 refresh() + toCenter()，
 *     修复窄容器初始化后节点布局错位（堆角落）的问题
 *   - 去掉节点点击跳转（selectNode → 切 Tab），点击只选中
 * 样式由 mind-elixir 构建产物内嵌（vite-plugin-css-injected-by-js），无需 import css。
 */
import { useEffect, useRef } from "react";
import MindElixir from "mind-elixir";
import type { MindElixirInstance, MindElixirData } from "mind-elixir";
import type { CaseItem, Task } from "../../types";

interface Props {
  task: Task;
}

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
          id: `case:${c.case_id}`,
          topic: `${c.case_id} ${c.title}`,
          tags: c.priority ? [c.priority] : [],
        })),
      })),
    },
  };
}

export default function MindMapTab({ task }: Props) {
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
    mindRef.current = mind;

    // 画布适配：先 toCenter 用 1e4 坐标滚到画布中心，再 scaleFit() 设置 .map-canvas transform。
    // scaleFit 不改 scroll，画布视觉中心=容器中心（已被 toCenter 校准过），子节点应全在可视区。
    const fit = () => {
      try {
        mind.toCenter();
        mind.scaleFit();
      } catch {
        /* 兜底 */
      }
    };
    const raf = requestAnimationFrame(fit);

    // 容器尺寸变化（弹窗弹出动画 / 窗口缩放）→ 重排 + 自适应 + 居中
    const ro = new ResizeObserver(() => fit());
    ro.observe(elRef.current);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
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
      <div className="mindmap-hint">拖拽画布可平移，滚轮可缩放</div>
      <div ref={elRef} className="map-container" />
    </div>
  );
}
