/**
 * 管理页（M4，仅 admin）：统计卡 + 用户管理 + 平台默认模型 + 访客清理。
 * 平台配置数据本地拉取（GET /llm/platform-config，admin-only）。
 */
import { useCallback, useEffect, useState } from "react";
import { apiJson, API, toast } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useSettingsStore } from "../store/settingsStore";
import UserTable from "../components/admin/UserTable";
import LLMConfigCard from "../components/settings/LLMConfigCard";
import type { AdminStats, AdminUserRow, LLMConfigRow } from "../types";

export default function AdminPage() {
  const { me, role, ready } = useAuth();
  const loadEffective = useSettingsStore((s) => s.loadEffective);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUserRow[]>([]);
  const [platform, setPlatform] = useState<LLMConfigRow[]>([]);
  const [cleaning, setCleaning] = useState("");

  const loadAll = useCallback(async () => {
    const [st, us, pf] = await Promise.all([
      apiJson<AdminStats>(API + "/admin/stats"),
      apiJson<AdminUserRow[]>(API + "/users"),
      apiJson<LLMConfigRow[]>(API + "/llm/platform-config"),
    ]);
    if (st) setStats(st);
    if (us) setUsers(us);
    if (pf) setPlatform(Array.isArray(pf) ? pf : []);
  }, []);

  useEffect(() => {
    if (ready && role === "admin") {
      void loadAll();
      void loadEffective();
    }
  }, [ready, role, loadAll, loadEffective]);

  const cleanGuests = async (mode: "expired" | "all") => {
    const msg =
      mode === "all"
        ? "⚠️ 强制清理全部活跃访客（无论是否到期，数据不可恢复），确定？"
        : "清理全部已到期访客数据，确定？";
    if (!window.confirm(msg)) return;
    setCleaning(mode);
    try {
      const path = mode === "all" ? "/admin/guests/clean-all" : "/admin/guests/clean";
      const r = await apiJson<{ cleaned: number }>(API + path, { method: "POST" });
      if (r) {
        toast(`已清理 ${r.cleaned} 个访客`);
        void loadAll();
      }
    } finally {
      setCleaning("");
    }
  };

  if (ready && role !== "admin") {
    return <div className="page-empty">仅管理员可访问</div>;
  }
  if (!me) return <div className="page-empty">请先登录</div>;

  return (
    <div className="page-wrap admin-page" data-testid="admin-page">
      <section className="stats-row" data-testid="stats-cards">
        <div className="stat-card">
          <div className="s-num">{stats?.registered_users ?? "—"}</div>
          <div className="s-label">注册用户</div>
        </div>
        <div className="stat-card">
          <div className="s-num">{stats?.active_guests ?? "—"}</div>
          <div className="s-label">活跃访客</div>
        </div>
        <div className="stat-card">
          <div className="s-num">{stats?.cleaned_24h ?? "—"}</div>
          <div className="s-label">24h 内清理</div>
        </div>
        <div className="stat-card">
          <div className="s-num">{stats?.total_tasks ?? "—"}</div>
          <div className="s-label">总任务数</div>
        </div>
      </section>

      <section className="set-card guest-clean-bar">
        <h3>访客治理</h3>
        <div className="llm-btnrow">
          <button
            className="btn ghost"
            disabled={!!cleaning}
            onClick={() => void cleanGuests("expired")}
            data-testid="clean-expired"
          >
            {cleaning === "expired" ? "清理中…" : "清理已到期访客"}
          </button>
          <button
            className="btn danger-ghost"
            disabled={!!cleaning}
            onClick={() => void cleanGuests("all")}
            data-testid="clean-all"
          >
            {cleaning === "all" ? "清理中…" : "强制清理全部访客"}
          </button>
        </div>
      </section>

      <UserTable users={users} myId={me.id > 0 ? me.id : -1} onChanged={() => void loadAll()} />

      <section className="set-card">
        <h3>平台默认模型</h3>
        <div className="sub">
          未配置个人模型的用户（含访客）将使用此默认；免费厂商不填 Key 时由服务器环境变量提供。
        </div>
        <div className="llm-grid">
          <LLMConfigCard
            slot="text"
            mode="platform"
            saved={platform.find((c) => c.slot === "text")}
            onSaved={() => void loadAll()}
          />
          <LLMConfigCard
            slot="vision"
            mode="platform"
            saved={platform.find((c) => c.slot === "vision")}
            onSaved={() => void loadAll()}
          />
        </div>
      </section>
    </div>
  );
}
