/**
 * M5 真实 ModelScope Key 冒烟验证（Playwright）：
 *   准备：注册临时用户（走 UI 配 LLM：ModelScope + 真实 Key，来自 .env）
 *   ① UI 登录（访客态 → 登出 → 登录框）
 *   ② 设置页：个人 LLM 配置保存 → 生效来源=我的配置 → 测连通 ✓（真实外呼）
 *   ③ 发送消息 → SSE 真实流式回复（非 mock 模板文案）
 *   ④ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m5/
 *
 * 运行：node scripts/e2e-m5-smoke.mjs
 * 前置：后端 8000 启动（.env 含真实 MODELSCOPE_API_KEY），AITF_FRONTEND=react
 *
 * 设计依据（llm_service.chat_stream）：
 *   对话默认走 mock 流式（demo 体验优先），仅 source=user 且 text 槽有 Key 时真实调用。
 *   因此冒烟必须以「用户自配真实 Key」路径验证。
 * mock 判定：模板文案「好的，关于「<用户原文>」，我先理一下：…」
 */
import { chromium } from "playwright";
import { execSync } from "node:child_process";
import fs from "node:fs";

const URL = process.env.M5_URL || "http://localhost:8000";
const API = URL + "/api";
const SHOT_DIR = "/tmp/e2e-m5";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const ROOT = "/Users/xp/Documents/软件测试示例项目/ai-testflow";
const DB = `${ROOT}/app.db`;
const USER = "e2e_m5_smoke";
const EMAIL = "e2e-m5-smoke@e2e-testmail.com";
const PWD = "M5Smoke!2026";

// 从 .env 读真实 ModelScope Key（冒烟用完即随用户清理）
const ENV_KEY = (fs.readFileSync(`${ROOT}/.env`, "utf-8").match(/^MODELSCOPE_API_KEY=(.+)$/m) || [])[1]?.trim();
if (!ENV_KEY) {
  console.error("❌ .env 未找到 MODELSCOPE_API_KEY");
  process.exit(1);
}

const results = [];
function ok(name) { results.push(`✅ ${name}`); console.log(`✅ ${name}`); }
function fail(name, detail) { results.push(`❌ ${name}: ${detail}`); console.error(`❌ ${name}: ${detail}`); }

async function apiJson(path, opts = {}) {
  const r = await fetch(API + path, opts);
  const d = await r.json().catch(() => ({}));
  return { status: r.status, ok: r.ok, data: d };
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => {
  const t = m.text();
  if (m.type() === "error" && !/Failed to load resource.*40[04]/.test(t)) errors.push(t);
});

try {
  // 准备：注册临时用户（已存在则忽略 400）
  await apiJson("/auth/register", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: USER, email: EMAIL, password: PWD }),
  });
  ok("准备：临时用户就绪");

  // ① UI 登录（进站即访客态：点登出 → 弹登录框 → 填表提交）
  await page.goto(URL, { waitUntil: "networkidle", timeout: 20000 });
  await page.click(".btn.out", { timeout: 10000 });
  await page.waitForSelector(".auth-modal input[placeholder='用户名']", { timeout: 8000 });
  await page.fill(".auth-modal input[placeholder='用户名']", USER);
  await page.fill(".auth-modal input[placeholder='密码']", PWD);
  await page.click(".auth-modal .auth-btn");
  await page.waitForSelector(".uc:has-text('e2e_m5_smoke')", { timeout: 10000 });
  ok("① UI 登录成功");

  // ② 设置页：个人 LLM 配置（text 槽：ModelScope + 真实 Key）→ 保存 → 生效=我的配置 → 测连通
  await page.click(".app-nav .nav-btn:has-text('设置')");
  await page.waitForSelector("[data-testid='provider-personal-text']", { timeout: 8000 });
  await page.selectOption("[data-testid='provider-personal-text']", "modelscope");
  await page.fill("[data-testid='model-personal-text']", "Qwen/Qwen3.8-27B");
  await page.fill("[data-testid='apikey-personal-text']", ENV_KEY);
  await page.click("[data-testid='save-personal-text']");
  await page.waitForSelector(".eff-source:has-text('我的配置')", { timeout: 15000 });
  const effText = await page.$eval(".eff-source", (e) => e.textContent || "");
  if (/我的配置/.test(effText)) ok(`② 生效模型来源=我的配置（${effText.trim()}）`);
  else fail("② 生效模型来源异常", effText.trim());
  await page.click("[data-testid='test-effective-text']");
  await page.waitForSelector(".test-msg.test-ok, .test-msg.test-err", { timeout: 60000 });
  const testMsg = await page.$eval(".test-msg.test-ok, .test-msg.test-err", (e) => e.textContent || "");
  if (/✓/.test(testMsg)) ok(`② 测连通真实外呼成功（${testMsg.trim()}）`);
  else fail("② 测连通失败", testMsg.trim());
  await page.screenshot({ path: `${SHOT_DIR}/5-real-llm-settings.png` });

  // ③ 回工作台发消息 → 真实 LLM 流式回复
  await page.click(".app-nav .nav-btn:has-text('工作台')");
  await page.fill(".chat-input textarea, textarea", "用一句话说明等价类划分法在测试中的作用");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => {
    const btns = document.querySelectorAll("button");
    for (const b of btns) if (/停止生成/.test(b.textContent || "")) return false;
    return document.querySelectorAll(".msg, .bubble").length >= 2;
  }, null, { timeout: 120000 });
  const reply = await page.$$eval(".msg, .bubble", (els) => (els[els.length - 1] || {}).textContent || "");
  const mockTemplate = /好的，关于「.+?」，我先理一下/.test(reply);
  if (reply.length > 20 && !mockTemplate) ok(`③ 真实 LLM 流式回复完成（${reply.trim().slice(0, 80)}…）`);
  else fail("③ 回复疑似 mock 模板", `mockTemplate=${mockTemplate}，长度=${reply.length}，内容=${reply.trim().slice(0, 100)}`);
  await page.screenshot({ path: `${SHOT_DIR}/6-real-llm-reply.png` });

  if (errors.length === 0) ok("④ 全程 0 JS 错误");
  else fail("④ 存在 JS 错误", errors.slice(0, 3).join(" | "));
} catch (e) {
  fail("脚本异常中断", String(e).slice(0, 300));
  await page.screenshot({ path: `${SHOT_DIR}/0-error.png` }).catch(() => {});
} finally {
  await browser.close();
  // 清理：整链删除临时用户数据（含其 LLM 配置，真实 Key 随之删除）
  try {
    execSync(
      `sqlite3 "${DB}" "DELETE FROM step_logs WHERE task_id IN (SELECT id FROM tasks WHERE user_id=(SELECT id FROM users WHERE username='${USER}')); ` +
      `DELETE FROM tasks WHERE user_id=(SELECT id FROM users WHERE username='${USER}'); ` +
      `DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id=(SELECT id FROM users WHERE username='${USER}')); ` +
      `DELETE FROM conversations WHERE user_id=(SELECT id FROM users WHERE username='${USER}'); ` +
      `DELETE FROM categories WHERE user_id=(SELECT id FROM users WHERE username='${USER}'); ` +
      `DELETE FROM llm_configs WHERE user_id=(SELECT id FROM users WHERE username='${USER}'); ` +
      `DELETE FROM users WHERE username='${USER}';"`,
    );
    console.log("🧹 清理完成（e2e_m5_smoke 含 LLM 配置）");
  } catch (e) {
    console.error("🧹 清理失败:", String(e).slice(0, 150));
  }
}

console.log("\n===== M5 冒烟结果汇总 =====\n" + results.join("\n"));
if (results.some((r) => r.startsWith("❌"))) process.exit(1);
