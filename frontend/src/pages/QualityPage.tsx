/**
 * 质量报告页（方案 A，2026-09-23）：从管理后台独立出来的展示面。
 * 所有登录角色可见（含访客）——质量数据是平台对外展示窗口；
 * 「运行测试」按钮仅 admin 显示（canRun 下传 QualityBoard，后端双重把关）。
 */
import { FlaskConical } from "lucide-react";
import { useAuth } from "../hooks/useAuth";
import QualityBoard from "../components/admin/QualityBoard";

export default function QualityPage() {
  const { role, ready } = useAuth();

  if (ready && !role) {
    return <div className="page-empty">请先登录</div>;
  }

  return (
    <div className="page-wrap" data-testid="quality-page">
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <FlaskConical size={20} aria-hidden="true" />
        <h2 style={{ margin: 0 }}>质量报告</h2>
      </div>
      <div className="sub" style={{ marginBottom: 14 }}>
        平台自身测试的量化结果（pytest 单测 + e2e 实测），每次运行真实采集，无人工修饰。
      </div>
      <QualityBoard canRun={role === "admin"} />
    </div>
  );
}
