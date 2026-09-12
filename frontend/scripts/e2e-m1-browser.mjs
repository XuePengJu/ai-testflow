/**
 * M1 浏览器端到端验证（Playwright）：
 *   ① 首开静默进访客 → ② 加密自检（guest 加密链路）→ ③ 退出弹登录框
 *   ④ admin 登录（明文直通）→ ⑤ 注册 user（加密链路）
 * 截图落档 /tmp/e2e-m1/（对齐 Push 三步门槛：UI 改动截图 ≥3 张）
 *
 * 运行：node scripts/e2e-m1-browser.mjs（需 vite dev 5173 + 后端 8000 已启动）
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M1_URL || "http://localhost:5173";
const SHOT_DIR = "/tmp/e2e-m1";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
function ok(name) {
  results.push(`✅ ${name}`);
  console.log(`✅ ${name}`);
}
function fail(name, detail) {
  results.push(`❌ ${name}: ${detail}`);
  console.error(`❌ ${name}: ${detail}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

/** 等自检结果落定（pre 内容含 HTTP 或 ✗，排除"请求中…"占位） */
async function waitCheckDone(page, timeout = 15000) {
  await page.waitForFunction(
    () => {
      const el = document.querySelector(".m1-pre");
      const t = el ? el.textContent || "" : "";
      return t.includes("HTTP") || t.includes("✗");
    },
    { timeout },
  );
  return page.textContent(".m1-pre");
}

try {
  // ① 首开 → 静默访客
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".uc.guest", { timeout: 8000 });
  const guestText = await page.textContent(".uc.guest");
  if (/访客 · 剩 \d+ 小时/.test(guestText || "")) ok("① 首开静默进访客（" + guestText.trim() + "）");
  else fail("① 访客标识异常", guestText || "empty");
  await page.screenshot({ path: `${SHOT_DIR}/1-guest-auto.png`, fullPage: true });

  // ② 加密自检（guest：响应加密 → 前端透明解密）
  await page.click("button.primary");
  const pre1 = await waitCheckDone(page);
  if (/HTTP 200/.test(pre1 || "") && /透明解密/.test(pre1 || "") && /"role":\s*"guest"/.test(pre1 || ""))
    ok("② guest 加密链路自检（HTTP 200 + 透明解密 + role=guest）");
  else fail("② guest 自检异常", (pre1 || "").slice(0, 200));
  await page.screenshot({ path: `${SHOT_DIR}/2-guest-crypto-check.png`, fullPage: true });

  // ③ 退出 → 弹登录框（manual_logout 标记，不再自动进访客）
  await page.click("button.out");
  await page.waitForSelector(".auth-modal", { timeout: 5000 });
  ok("③ 退出后弹登录框");
  await page.screenshot({ path: `${SHOT_DIR}/3-login-modal.png`, fullPage: false });

  // ④ admin 登录（明文直通）
  await page.fill(".auth-modal input >> nth=0", "admin");
  await page.fill(".auth-modal input >> nth=1", "Admin@123");
  await page.click(".auth-btn");
  await page.waitForSelector(".uc.admin", { timeout: 8000 });
  const adminText = await page.textContent(".uc.admin");
  if (/管理员 · admin/.test(adminText || "")) ok("④ admin 登录成功（明文直通角色）");
  else fail("④ admin 标识异常", adminText || "empty");
  await page.click("button.primary"); // admin 自检
  const pre2 = await waitCheckDone(page);
  if (/admin 明文直通/.test(pre2 || "")) ok("④ admin 自检显示明文直通");
  else fail("④ admin 自检异常", (pre2 || "").slice(0, 200));
  await page.screenshot({ path: `${SHOT_DIR}/4-admin-plain.png`, fullPage: true });

  // ⑤ 退出 → 注册新 user（加密链路 + 三级角色最后一环）
  await page.click("button.out");
  await page.waitForSelector(".auth-modal", { timeout: 5000 });
  await page.click(".auth-tabs button >> nth=1"); // 注册 tab
  const uname = "m1verify" + Date.now().toString(36);
  await page.fill(".auth-modal input >> nth=0", uname);
  await page.fill(".auth-modal input >> nth=1", `${uname}@163.com`);
  await page.fill(".auth-modal input >> nth=2", "Passw0rd123");
  await page.click(".auth-btn");
  try {
    await page.waitForSelector(".uc:not(.admin):not(.guest)", { timeout: 10000 });
  } catch {
    const err = await page.textContent(".auth-error").catch(() => "(无错误提示)");
    fail("⑤ 注册失败", err);
    await page.screenshot({ path: `${SHOT_DIR}/5-register-fail.png`, fullPage: true });
    throw new Error("register failed: " + err);
  }
  const userText = await page.textContent(".uc:not(.admin):not(.guest)");
  if (new RegExp("用户 · " + uname).test(userText || "")) ok("⑤ 注册 user 登录成功（" + userText.trim() + "）");
  else fail("⑤ user 标识异常", userText || "empty");
  await page.click("button.primary"); // user 自检（加密）
  const pre3 = await waitCheckDone(page);
  if (/透明解密/.test(pre3 || "") && new RegExp(uname).test(pre3 || ""))
    ok("⑤ user 加密链路自检（透明解密 + 用户名回显）");
  else fail("⑤ user 自检异常", (pre3 || "").slice(0, 200));
  await page.screenshot({ path: `${SHOT_DIR}/5-user-crypto-check.png`, fullPage: true });

  if (errors.length) fail("JS 错误", errors.slice(0, 3).join(" | "));
  else ok("全程 0 JS 错误");
} catch (e) {
  fail("运行异常", String(e));
  await page.screenshot({ path: `${SHOT_DIR}/0-error.png`, fullPage: true }).catch(() => {});
} finally {
  await browser.close();
  fs.writeFileSync(
    `${SHOT_DIR}/result.txt`,
    results.join("\n") + `\n\n截图目录: ${SHOT_DIR}\n时间: ${new Date().toISOString()}\n`,
  );
  const failed = results.some((r) => r.startsWith("❌"));
  console.log(failed ? "\n存在失败项，见 /tmp/e2e-m1/result.txt" : "\n全部通过");
  process.exit(failed ? 1 : 0);
}
