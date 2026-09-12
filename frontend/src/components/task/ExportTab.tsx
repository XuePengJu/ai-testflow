/**
 * M3：导出 & 迭代 Tab。
 * - 下载：GET /tasks/{id}/download?fmt=（FileResponse 二进制，中间件只加密 JSON → 透传）
 * - 迭代补充：POST /tasks/{id}/iterate（FormData：instruction 必填 / file 可选 xmind·xlsx·json）
 *   → 201 返回新子任务（parent_task_id 链）→ 启动轮询 + 刷新列表
 */
import { useRef, useState } from "react";
import { API, api, downloadTaskFile, toast } from "../../api/client";
import { useTaskStore } from "../../store/taskStore";
import type { Task } from "../../types";

const FMT_META: Record<string, { icon: string; label: string }> = {
  xlsx: { icon: "📊", label: "Excel 用例表" },
  json: { icon: "🧾", label: "JSON 结构化" },
  xmind: { icon: "🗺️", label: "XMind 思维导图" },
};

export default function ExportTab({ task }: { task: Task }) {
  const [iterating, setIterating] = useState(false);
  const [showIterForm, setShowIterForm] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const startPolling = useTaskStore((s) => s.startPolling);
  const refresh = useTaskStore((s) => s.refresh);

  const formats = (task.formats || "xlsx,json,xmind")
    .split(",")
    .map((f) => f.trim())
    .filter(Boolean);
  const downloadable = task.status === "completed";
  const iterable = task.status === "completed" || task.status === "failed";

  async function submitIterate() {
    if (!instruction.trim() && !file) {
      toast("请填写补充要求或选择用例文件");
      return;
    }
    setIterating(true);
    try {
      const fd = new FormData();
      fd.append("instruction", instruction);
      if (file) fd.append("file", file);
      const r = await api(API + "/tasks/" + task.id + "/iterate", { method: "POST", body: fd });
      if (!r.ok) {
        let detail = `迭代失败（HTTP ${r.status}）`;
        try {
          const d = await r.json();
          if (d && typeof d.detail === "string") detail = d.detail;
        } catch { /* 非 JSON */ }
        toast(detail);
        return;
      }
      const child = (await r.json()) as Task;
      toast(`迭代任务已创建：${child.name}`);
      setShowIterForm(false);
      setInstruction("");
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      startPolling(child.id);
      void refresh();
    } catch {
      toast("网络异常，迭代失败");
    } finally {
      setIterating(false);
    }
  }

  return (
    <div className="export-tab">
      <div className="export-sec-title">导出文件</div>
      {!downloadable ? (
        <div className="drawer-empty">任务完成后可下载导出文件</div>
      ) : (
        <div className="export-list">
          {formats.map((f) => {
            const meta = FMT_META[f] || { icon: "📄", label: f };
            return (
              <button
                key={f}
                type="button"
                className="export-item"
                onClick={() => void downloadTaskFile(task.id, f, task.name)}
              >
                <span className="ex-icon">{meta.icon}</span>
                <span className="ex-main">
                  <span className="ex-name">{task.name}.{f}</span>
                  <span className="ex-label">{meta.label}</span>
                </span>
                <span className="ex-dl">下载</span>
              </button>
            );
          })}
        </div>
      )}

      {iterable && (
        <>
          <div className="export-sec-title">迭代补充</div>
          <div className="iter-desc">
            基于当前任务用例补充新要求（或上传 xmind/xlsx/json 用例文件合并），生成新版本子任务。
          </div>
          {!showIterForm ? (
            <button type="button" className="qtag confirm-btn" onClick={() => setShowIterForm(true)}>
              💬 发起迭代补充
            </button>
          ) : (
            <div className="iter-form">
              <textarea
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="补充要求，例如：增加扫码登录的异常场景；密码错误 5 次锁定的边界用例…"
                rows={4}
                disabled={iterating}
              />
              <div className="iter-file-row">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".xmind,.xlsx,.json"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                  disabled={iterating}
                />
                {file && <span className="iter-file-name">{file.name}</span>}
              </div>
              <div className="iter-btns">
                <button type="button" className="qtag" disabled={iterating} onClick={() => setShowIterForm(false)}>
                  取消
                </button>
                <button type="button" className="qtag confirm-btn" disabled={iterating} onClick={() => void submitIterate()}>
                  {iterating ? "提交中…" : "确认迭代"}
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
