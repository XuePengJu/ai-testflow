/**
 * 用户管理表（M4 admin）：启停 / 删除 / 访客清理。
 * - guest 禁用 = 立即清理其数据（后端语义，前端 confirm 提示）
 * - admin 不能操作自己（后端 400，前端按钮禁用）
 */
import { useState } from "react";
import { api, API, toast } from "../../api/client";
import type { AdminUserRow } from "../../types";

interface Props {
  users: AdminUserRow[];
  myId: number;
  onChanged: () => void;
}

const ROLE_LABEL: Record<string, string> = { guest: "访客", user: "用户", admin: "管理员" };

function fmtDate(s?: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default function UserTable({ users, myId, onChanged }: Props) {
  const [busyId, setBusyId] = useState<number | null>(null);

  const patchUser = async (u: AdminUserRow, is_active: boolean) => {
    const msg = u.role === "guest"
      ? `禁用访客「${u.username}」将立即清理其全部数据（${u.tasks} 个任务），确定？`
      : `确定${is_active ? "启用" : "禁用"}用户「${u.username}」？`;
    if (!window.confirm(msg)) return;
    setBusyId(u.id);
    try {
      const r = await api(API + "/users/" + u.id, {
        method: "PATCH",
        body: JSON.stringify({ is_active }),
      });
      if (r.ok) {
        toast(u.role === "guest" && !is_active ? "访客已清理" : "已更新");
        onChanged();
      } else {
        const d = (await r.json().catch(() => null)) as { detail?: string } | null;
        toast(d?.detail || `HTTP ${r.status}`);
      }
    } catch {
      toast("网络错误");
    } finally {
      setBusyId(null);
    }
  };

  const deleteUser = async (u: AdminUserRow) => {
    if (
      !window.confirm(
        `⚠️ 删除用户「${u.username}」将级联删除其 ${u.tasks} 个任务与全部文件，不可恢复，确定？`,
      )
    )
      return;
    setBusyId(u.id);
    try {
      const r = await api(API + "/users/" + u.id, { method: "DELETE" });
      if (r.ok) {
        toast("用户已删除");
        onChanged();
      } else {
        const d = (await r.json().catch(() => null)) as { detail?: string } | null;
        toast(d?.detail || `HTTP ${r.status}`);
      }
    } catch {
      toast("网络错误");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="set-card" data-testid="user-table">
      <h3>用户管理（{users.length}）</h3>
      <div className="tbl-wrap">
        <table className="admin-tbl">
          <thead>
            <tr>
              <th>ID</th><th>用户名</th><th>角色</th><th>状态</th><th>任务</th><th>注册/到期</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} data-user-id={u.id}>
                <td>{u.id}</td>
                <td className="t-username">
                  {u.username}
                  {u.id === myId && <span className="me-tag">（我）</span>}
                </td>
                <td><span className={"uc " + (u.role === "admin" ? "admin" : u.role === "guest" ? "guest" : "")}>{ROLE_LABEL[u.role]}</span></td>
                <td>{u.is_active ? <span className="pill pill-ok">活跃</span> : <span className="pill pill-wait">已禁用</span>}</td>
                <td>{u.tasks}</td>
                <td className="t-date">
                  {u.role === "guest" ? `到期 ${fmtDate(u.expires_at)}` : fmtDate(u.created_at)}
                </td>
                <td className="t-actions">
                  {u.id !== myId && (
                    <>
                      <button
                        className="btn ghost sm"
                        disabled={busyId === u.id}
                        onClick={() => void patchUser(u, !u.is_active)}
                      >
                        {u.role === "guest" ? "清理" : u.is_active ? "禁用" : "启用"}
                      </button>
                      <button
                        className="btn danger-ghost sm"
                        disabled={busyId === u.id}
                        onClick={() => void deleteUser(u)}
                      >
                        删除
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
