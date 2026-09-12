/**
 * 设置页（M4）：个人中心 + LLM 模型配置 + 生效模型。
 * - user/admin：完整配置能力
 * - guest：LLM 配置接口 403 → 只显示个人信息与生效模型（只读）
 */
import { useEffect } from "react";
import { useAuth } from "../hooks/useAuth";
import { useSettingsStore } from "../store/settingsStore";
import ProfileCard from "../components/settings/ProfileCard";
import LLMConfigCard from "../components/settings/LLMConfigCard";
import EffectiveBar from "../components/settings/EffectiveBar";

export default function SettingsPage() {
  const { me, role } = useAuth();
  const loadMyConfigs = useSettingsStore((s) => s.loadMyConfigs);
  const loadEffective = useSettingsStore((s) => s.loadEffective);
  const myConfigs = useSettingsStore((s) => s.myConfigs);

  useEffect(() => {
    void loadEffective();
    if (role && role !== "guest") void loadMyConfigs();
  }, [role, loadMyConfigs, loadEffective]);

  if (!me) return <div className="page-empty">请先登录</div>;

  return (
    <div className="page-wrap settings-page" data-testid="settings-page">
      <ProfileCard />
      <EffectiveBar />
      {role !== "guest" ? (
        <section className="set-card">
          <h3>我的模型配置</h3>
          <div className="sub">
            个人配置优先于平台默认；文本槽为必配项（免费厂商可不填 Key，由平台提供）。
          </div>
          <div className="llm-grid">
            <LLMConfigCard slot="text" mode="personal" saved={myConfigs.find((c) => c.slot === "text")} />
            <LLMConfigCard slot="vision" mode="personal" saved={myConfigs.find((c) => c.slot === "vision")} />
          </div>
        </section>
      ) : (
        <section className="set-card">
          <h3>我的模型配置</h3>
          <div className="hint-line">
            访客模式使用平台默认模型。注册账号后可自定义模型配置（登录框 → 注册保留访客数据）。
          </div>
        </section>
      )}
    </div>
  );
}
