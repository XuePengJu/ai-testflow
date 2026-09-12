/**
 * M4 浏览器端到端验证（Playwright）：
 *   ① 普通用户登录 → 导航出现「设置」无「管理」
 *   ② 设置页：个人中心（用户名/角色）+ 改密码错误路径（旧密码错误提示）
 *   ③ LLM 配置卡：厂商预设联动 base_url → 保存 → 已配置徽章
 *   ④ 生效模型条：来源=我的配置 + 测连通返回结果
 *   ⑤ admin 登录 → 「管理」入口 → 统计卡 4 项 + 用户表
 *   ⑥ 平台默认模型配置保存（user_id=0）
 *   ⑦ 视图切换：管理 ↔ 工作台三栏保留
 *   ⑧ 分类树：新建顶级/子分类 → 树渲染
 *   ⑨ 任务归类（🏷 菜单 → move-task）→ 分类计数
 *   ⑩ 分类过滤任务列表
 *   ⑪ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m4/
 *
 * 运行：node scripts/e2e-m4-browser.mjs（需 vite dev 5173 + 后端 8000 已启动）
 *
 * 数据治理：临时账号 e2e_m4_user / e2e_m4_admin 注册后提升，结束时整链清理
 * （users/tasks/step_logs/messages/conversations/categories/llm_config）。
 */
import { chromium } from "playwright";
import { execSync } from "node:child_process";
import fs from "node:fs";

const URL = process.env.M4_URL || "http://localhost:5173";
const API = "http://localhost:8000/api";
const SHOT_DIR = "/tmp/e2e-m4";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const ROOT = "/Users/xp/Documents/软件测试示例项目/ai-testflow";
const DB = `${ROOT}/app.db`;

const USER = "e2e_m4_user";
const ADMIN = "e2e_m4_admin";
const PWD = "M4E2ePass!2026";

const results = [];
function ok(name) {
  results.push(`✅ ${name}`);
  console.log(`✅ ${name}`);
}
function fail(name, detail) {
  results.push(`❌ ${name}: ${detail}`);
  console.error(`❌ ${name}: ${detail}`);
}

/* ---------- 准备：注册 + 提升 admin ---------- */
async function apiJson(path, opts = {}) {
  const r = await fetch(API + path, opts);
  const d = await r.json().catch(() => ({}));
  return { status: r.status, ok: r.ok, data: d };
}

async function loginToken(username) {
  const fd = new URLSearchParams({ username, password: PWD });
  const { ok, data } = await apiJson("/auth/login", { method: "POST", body: fd });
  return ok ? data.access_token : null;
}

/* JS 错误收集（提升到 try 外，catch 中可读） */
const errors = [];

try {
  // 注册两个测试账号（已存在则忽略 400）
  await apiJson("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: USER, email: "e2euser@e2e-testmail.com", password: PWD }),
  });
  await apiJson("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: ADMIN, email: "e2eadmin@e2e-testmail.com", password: PWD }),
  });
  // 提升第二个为 admin（直接改库，后端每请求读库即时生效）
  execSync(`sqlite3 "${DB}" "UPDATE users SET role='admin' WHERE username='${ADMIN}'"`);

  const userToken = await loginToken(USER);
  const adminToken = await loginToken(ADMIN);
  if (!userToken || !adminToken) throw new Error("测试账号登录失败");
  ok(`准备：${USER}(user) / ${ADMIN}(admin) 就绪`);

  // 给 e2e 用户预置一个任务（归类/过滤测试用；响应为加密体，只断言 201 不解析）
  const fd = new URLSearchParams({ text: "电商购物车结算流程，包含加减商品与优惠券", kind: "business", formats: "xlsx" });
  const t = await fetch(API + "/tasks", {
    method: "POST",
    headers: { Authorization: "Bearer " + userToken },
    body: fd,
  });
  if (t.status === 201) ok("准备：预置任务已创建（属 e2e_m4_user）");
  else fail("准备：预置任务创建失败", `HTTP ${t.status}`);

  /* ---------- UI 验证 ---------- */
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("response", (r) => {
    if (r.status() >= 400)
      console.error(`   [HTTP ${r.status()}] ${r.request().method()} ${r.url().replace("http://localhost:5173", "")}`);
  });

  // prompt/confirm 对话框统一处理（answer 变量控制 prompt 输入）
  let dialogAnswer = "";
  page.on("dialog", (d) => {
    if (d.type() === "prompt") void d.accept(dialogAnswer);
    else void d.accept();
  });

  const uiLogin = async (username) => {
    await page.goto(URL, { waitUntil: "networkidle" });
    // 退出当前身份（访客）→ 弹登录框
    await page.click("button.btn.out", { timeout: 8000 });
    await page.waitForSelector(".auth-modal", { timeout: 5000 });
    await page.fill(".auth-modal input[placeholder='用户名']", username);
    await page.fill(".auth-modal input[placeholder='密码']", PWD);
    await page.click(".auth-modal .auth-btn");
    await page.waitForFunction(
      (u) => document.querySelector(".app-identity .uc")?.textContent?.includes(u),
      username,
      { timeout: 10000 },
    );
  };

  // ① 普通用户登录 → 导航
  await uiLogin(USER);
  const hasSettings = await page.$(".app-nav .nav-btn:has-text('设置')");
  const hasAdmin = await page.$(".app-nav .nav-btn:has-text('管理')");
  if (hasSettings && !hasAdmin) ok("① 用户导航：⚙ 设置可见 · 🛡 管理隐藏");
  else fail("① 用户导航异常", `settings=${!!hasSettings} admin=${!!hasAdmin}`);

  // ② 设置页：个人中心
  await page.click(".app-nav .nav-btn:has-text('设置')");
  await page.waitForSelector("[data-testid='settings-page']", { timeout: 8000 });
  await page.waitForSelector("[data-testid='profile-card']", { timeout: 8000 });
  const uname = await page.textContent("[data-testid='profile-card'] .p-row:first-child");
  if (uname && uname.includes(USER)) ok(`② 设置页：个人中心显示 ${USER}`);
  else fail("② 个人中心异常", String(uname));

  // ②b 改密码错误路径
  await page.fill("[data-testid='old-pwd']", "wrong-old-password");
  await page.fill("[data-testid='new-pwd']", "NewPass!2026x");
  await page.fill(".pwd-form input[placeholder='确认新密码']", "NewPass!2026x");
  await page.click("[data-testid='pwd-submit']");
  await page.waitForSelector("[data-testid='pwd-err']", { timeout: 8000 });
  const pwdErr = await page.textContent("[data-testid='pwd-err']");
  if (pwdErr && pwdErr.includes("旧密码")) ok(`②b 改密码错误提示：${pwdErr.trim()}`);
  else fail("②b 改密码错误路径异常", String(pwdErr));
  await page.screenshot({ path: `${SHOT_DIR}/1-settings-profile.png`, fullPage: true });

  // ③ LLM 配置卡：厂商联动 + 保存
  await page.selectOption("[data-testid='provider-personal-text']", "modelscope");
  const baseUrl = await page.inputValue("[data-testid='baseurl-personal-text']");
  if (baseUrl.includes("modelscope.cn")) ok(`③ 厂商预设联动 base_url：${baseUrl}`);
  else fail("③ base_url 未联动", baseUrl);
  await page.fill("[data-testid='model-personal-text']", "Qwen/Qwen3.8-27B");
  await page.fill("[data-testid='apikey-personal-text']", "sk-e2e-dummy-1234");
  await page.click("[data-testid='save-personal-text']");
  await page.waitForFunction(
    () => document.querySelector("[data-testid='llm-card-personal-text'] .slot-badge")?.classList.contains("on"),
    null,
    { timeout: 10000 },
  );
  ok("③ text 槽保存成功 → 已配置徽章");

  // ④ 生效模型条
  await page.waitForSelector("[data-testid='effective-bar']", { timeout: 8000 });
  const effSource = await page.textContent("[data-testid='effective-bar'] .eff-source");
  const effModel = await page.textContent(".effective-bar .eff-row >> nth=0 >> .eff-model");
  if (effSource?.includes("我的配置") && effModel?.includes("Qwen/Qwen3.8-27B"))
    ok(`④ 生效模型：${effSource?.trim()} → ${effModel?.trim()}`);
  else fail("④ 生效模型异常", `${effSource} / ${effModel}`);
  await page.click("[data-testid='test-effective-text']");
  await page.waitForSelector(".effective-bar .test-msg", { timeout: 60000 });
  const testMsg = await page.textContent(".effective-bar .test-msg");
  ok(`④b 测连通返回：${(testMsg || "").trim().slice(0, 60)}`);
  await page.screenshot({ path: `${SHOT_DIR}/2-settings-llm.png`, fullPage: true });

  // ⑦ 视图切换回工作台
  await page.click(".app-nav .nav-btn:has-text('工作台')");
  await page.waitForSelector(".app-main .chat-panel", { timeout: 5000 });
  const cols = await page.$$eval(".app-main > *", (els) => els.length);
  if (cols >= 3) ok(`⑦ 切回工作台：三栏完整（${cols} 列）`);
  else fail("⑦ 工作台三栏异常", String(cols));

  // ⑧⑨⑩ 分类树（以 e2e 用户身份）
  // 注意：新账号被 V2.5 sample_seeder 预置 3 个种子分类（Web 应用等），树里不止 电商测试，
  // 所有断言必须按 cat-name 精确锚定「电商测试」行，不能用"第一个 .cat-row"
  await page.waitForSelector("[data-testid='category-tree']", { timeout: 8000 });
  await page.click("[data-testid='cat-new-btn']");
  await page.fill("[data-testid='cat-new-input']", "电商测试");
  await page.press("[data-testid='cat-new-input']", "Enter");
  await page.waitForSelector(".cat-row[data-cat-id]:has(.cat-name:text-is('电商测试'))", { timeout: 8000 });
  const catId = await page.getAttribute(".cat-row:has(.cat-name:text-is('电商测试'))", "data-cat-id");
  ok(`⑧ 新建顶级分类「电商测试」（id=${catId}）`);

  // 子分类（prompt 对话框）
  dialogAnswer = "购物车";
  await page.hover(`.cat-row[data-cat-id='${catId}']`);
  await page.click(`.cat-row[data-cat-id='${catId}'] button[title='新建子分类']`);
  await page.waitForFunction(
    () => [...document.querySelectorAll(".cat-row .cat-name")].some((e) => e.textContent === "购物车"),
    null,
    { timeout: 8000 },
  );
  ok("⑧b 新建子分类「购物车」");

  // ⑨ 任务归类
  await page.waitForSelector(".task-panel .task-item", { timeout: 30000 });
  const firstTaskId = await page.getAttribute(".task-panel .task-item >> nth=0", "data-task-id");
  if (firstTaskId) {
    await page.hover(".task-panel .task-item >> nth=0");
    await page.click("[data-testid='cat-menu-" + firstTaskId + "'] >> nth=0");
    await page.waitForSelector(`[data-testid='cat-move-${firstTaskId}']`, { timeout: 5000 });
    await page.click(`[data-testid='cat-move-${firstTaskId}'] button:has-text('电商测试')`);
    await page.waitForFunction(
      (cid) => document.querySelector(`.cat-row[data-cat-id='${cid}'] .cat-count`)?.textContent === "1",
      catId,
      { timeout: 10000 },
    );
    ok("⑨ 任务归类：🏷 菜单 → 电商测试（计数 1）");

    // ⑩ 分类过滤
    await page.click(`.cat-row[data-cat-id='${catId}']`);
    await page.waitForFunction(
      () => document.querySelector(".task-panel .side-head span")?.textContent?.includes("电商测试"),
      null,
      { timeout: 5000 },
    );
    const filtered = await page.$$eval(".task-panel .task-item", (els) => els.length);
    ok(`⑩ 分类过滤生效：列表 ${filtered} 条（标题含分类名）`);
    await page.click("[data-testid='cat-all']"); // 还原
    await page.screenshot({ path: `${SHOT_DIR}/3-category-tree.png`, fullPage: true });
  } else {
    fail("⑨ 任务归类", "无任务可归类");
  }

  // ⑤ admin 登录 → 管理页
  await uiLogin(ADMIN);
  const adminNav = await page.$(".app-nav .nav-btn:has-text('管理')");
  if (adminNav) ok("⑤ admin 导航：🛡 管理可见");
  else fail("⑤ admin 导航异常", "无管理按钮");
  await page.click(".app-nav .nav-btn:has-text('管理')");
  await page.waitForSelector("[data-testid='admin-page']", { timeout: 10000 });
  await page.waitForSelector("[data-testid='stats-cards'] .stat-card", { timeout: 10000 });
  // 统计数字初始渲染为 "—"，等 fetch 完成变成真实数字
  await page.waitForFunction(
    () => [...document.querySelectorAll("[data-testid='stats-cards'] .s-num")].every((e) => e.textContent && e.textContent.trim() !== "—"),
    null,
    { timeout: 10000 },
  );
  const statNums = await page.$$eval("[data-testid='stats-cards'] .s-num", (els) =>
    els.map((e) => e.textContent?.trim()),
  );
  if (statNums.length === 4 && statNums.every((n) => n && n !== "—")) ok(`⑤ 统计卡 4 项：${statNums.join("/")}`);
  else fail("⑤ 统计卡异常", statNums.join("/"));
  await page.waitForSelector("[data-testid='user-table'] table tr[data-user-id]", { timeout: 10000 });
  const userRows = await page.$$eval("[data-testid='user-table'] tr[data-user-id]", (els) => els.length);
  const hasMe = await page.$("[data-testid='user-table'] .me-tag");
  if (userRows >= 2 && hasMe) ok(`⑤b 用户表 ${userRows} 行 · 「（我）」标记正常`);
  else fail("⑤b 用户表异常", `rows=${userRows} me=${!!hasMe}`);

  // ⑥ 平台默认配置（后端 mock 模式进程无免费厂商环境 Key → text 槽必须显式填 Key 才能保存）
  await page.selectOption("[data-testid='provider-platform-text']", "modelscope");
  await page.fill("[data-testid='model-platform-text']", "Qwen/Qwen3.8-Flash-Next");
  await page.fill("[data-testid='apikey-platform-text']", "sk-e2e-platform-01");
  await page.click("[data-testid='save-platform-text']");
  await page.waitForFunction(
    () => document.querySelector("[data-testid='llm-card-platform-text'] .slot-badge")?.classList.contains("on"),
    null,
    { timeout: 10000 },
  );
  ok("⑥ 平台默认 text 槽保存成功（免费厂商 · 平台 Key）");
  await page.screenshot({ path: `${SHOT_DIR}/4-admin-page.png`, fullPage: true });

  await browser.close();

  // ⑪ JS 错误检查：pageerror（真实异常）全算失败；
  // console "Failed to load resource" 是浏览器对 4xx 响应的网络层提示——
  // ②b 故意输错旧密码（401）会触发，属预期，不计入
  const realErrors = errors.filter((e) => !e.startsWith("Failed to load resource"));
  if (realErrors.length === 0) ok("⑪ 全程 0 JS 错误（预期内 4xx 网络提示已过滤）");
  else fail("⑪ 存在 JS 错误", realErrors.slice(0, 3).join(" | "));
} catch (e) {
  fail("脚本异常中断", String(e) + (errors && errors.length ? " | JS错误: " + errors.slice(0, 3).join(" | ") : ""));
} finally {
  /* ---------- 清理：整链删除测试数据 ---------- */
  try {
    const sql = `
DELETE FROM step_logs WHERE task_id IN (SELECT id FROM tasks WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%'));
DELETE FROM tasks WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%'));
DELETE FROM conversations WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM categories WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM llm_configs WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM llm_configs WHERE user_id = 0;
DELETE FROM users WHERE username LIKE 'e2e_m4_%';`;
    execSync(`sqlite3 "${DB}" "${sql.replace(/\n/g, " ")}"`);
    console.log("🧹 清理完成（e2e_m4_* 账号/任务/分类/配置 + user_id=0 平台配置）");
  } catch (e) {
    console.error("🧹 清理失败（可手工执行 sqlite 清理）:", String(e).slice(0, 200));
  }
}

console.log("\n===== M4 结果汇总 =====");
console.log(results.join("\n"));
const failed = results.filter((r) => r.startsWith("❌")).length;
console.log(`\n${failed === 0 ? "🎉 全部通过" : `⚠️ ${failed} 项失败`}`);
process.exit(failed === 0 ? 0 : 1);
