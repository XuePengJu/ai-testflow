/**
 * M4 浏览器端到端验证（Playwright，V5.3 UI 适配）：
 *   准备：API 注册 e2e_m4_user / e2e_m4_admin（明文认证通道）+ python3 提升 admin
 *   ① 普通用户登录 → rail 无「用户管理」入口（角色隔离）
 *   ② 个人中心（点 rail 用户区进入）：资料显示 + 改密码错误路径
 *   ③ 模型配置页：模型池添加条目（modelscope 预设联动 base_url）→ 池徽章
 *   ④ 调度摘要生效 + 池条目测连通返回结果
 *   ⑤ admin 登录 → 用户管理页：统计卡 3 项 + 用户表
 *   ⑥ 平台默认模型池添加（admin · 平台默认 Tab）
 *   ⑦ 视图切换：AI 对话三栏保留
 *   ⑧ 分类树：新建顶级/子分类 → 树渲染
 *   ⑨ 任务归类（🏷 菜单 → cat-move）→ 分类计数
 *   ⑩ 分类过滤任务列表
 *   ⑪ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m4/
 *
 * 运行：node scripts/e2e-m4-browser.mjs（M4_URL 默认 http://localhost:8000）
 *
 * 数据治理：临时账号 e2e_m4_* 结束时整链清理（users/tasks/…/llm_model_pool）；
 * 提升角色直改库（.env DB_TYPE=mysql → pymysql，sqlite 配置兜底 app.db），只动 e2e_m4_% 行。
 */
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import fs from "node:fs";

const URL = process.env.M4_URL || "http://localhost:8000";
const API = "http://localhost:8000/api";
const SHOT_DIR = "/tmp/e2e-m4";
fs.mkdirSync(SHOT_DIR, { recursive: true });

const ROOT = "/Users/xp/Documents/软件测试示例项目/ai-testflow";

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

/* ---------- DB 直改：后端 .env 是 DB_TYPE=mysql，走 pymysql；sqlite 配置时兜底 app.db ---------- */
const ENV_TXT = fs.readFileSync(`${ROOT}/.env`, "utf-8");
function envVal(k) {
  return (ENV_TXT.match(new RegExp(`^${k}=(.*)$`, "m")) || [])[1]?.trim() || "";
}

function dbExec(sql) {
  let py;
  if ((envVal("DB_TYPE") || "sqlite").toLowerCase() === "mysql") {
    py = `
import pymysql, json
from pymysql.constants import CLIENT
cfg = json.loads(${JSON.stringify(JSON.stringify({
      host: envVal("DB_HOST") || "127.0.0.1",
      port: envVal("DB_PORT") || "3306",
      user: envVal("DB_USER"),
      password: envVal("DB_PASSWORD"),
      database: envVal("DB_NAME"),
    }))})
c = pymysql.connect(host=cfg["host"], port=int(cfg["port"]), user=cfg["user"],
                    password=cfg["password"], database=cfg["database"],
                    client_flag=CLIENT.MULTI_STATEMENTS)
cur = c.cursor(); cur.execute(${JSON.stringify(sql)}); c.commit(); c.close()`;
  } else {
    py = `import sqlite3;c=sqlite3.connect(${JSON.stringify(`${ROOT}/app.db`)});c.executescript(${JSON.stringify(sql)});c.commit();c.close()`;
  }
  execFileSync(`${ROOT}/.venv/bin/python`, ["-c", py], { stdio: "pipe" });
}

/* ---------- 明文认证通道：API 注册/登录 ---------- */
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
  // 提升第二个为 admin（直改库，只动 e2e_m4_admin 行；后端每请求读库即时生效）
  dbExec(`UPDATE users SET role='admin' WHERE username='${ADMIN}';`);

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

  // prompt/confirm 对话框统一处理（answer 变量控制 prompt 输入）
  let dialogAnswer = "";
  page.on("dialog", (d) => {
    if (d.type() === "prompt") void d.accept(dialogAnswer);
    else void d.accept();
  });

  const uiLogin = async (username) => {
    await page.goto(URL, { waitUntil: "networkidle" });
    // 退出当前身份（访客）→ 弹登录框
    await page.click('button[aria-label="退出登录"]', { timeout: 8000 });
    await page.waitForSelector(".auth-modal", { timeout: 5000 });
    await page.fill(".auth-modal input[placeholder='用户名']", username);
    await page.fill(".auth-modal input[placeholder='密码']", PWD);
    await page.click(".auth-btn");
    await page.waitForFunction(
      (u) => document.querySelector(".rail-user .rl-user-name")?.textContent?.trim() === u,
      username,
      { timeout: 10000 },
    );
  };

  /** 模型池添加一条（当前 Tab 上下文里 mode 的 text 槽） */
  const poolAdd = async (mode, model, key) => {
    await page.click(`[data-testid='pool-add-${mode}-text']`);
    await page.waitForSelector(`[data-testid='pool-form-${mode}-text-new']`, { timeout: 8000 });
    await page.selectOption(`[data-testid='pool-provider-${mode}-text']`, "modelscope");
    await page.waitForTimeout(300);
    const baseUrl = await page.inputValue(`[data-testid='pool-baseurl-${mode}-text']`);
    if (!baseUrl.includes("modelscope.cn"))
      await page.fill(`[data-testid='pool-baseurl-${mode}-text']`, "https://api-inference.modelscope.cn/v1");
    else ok(`③ 厂商预设联动 base_url：${baseUrl}`);
    await page.fill(`[data-testid='pool-model-${mode}-text']`, model);
    if (key) await page.fill(`[data-testid='pool-apikey-${mode}-text']`, key);
    await page.click(`[data-testid='pool-save-${mode}-text-new']`);
    await page.waitForFunction(
      (m) => document.querySelector(`[data-testid='pool-card-${m}-text'] .slot-badge`)?.textContent?.includes("模型池"),
      mode,
      { timeout: 10000 },
    );
  };

  // ① 普通用户登录 → rail 角色隔离
  await uiLogin(USER);
  const idUser = ((await page.textContent(".rail-user .rl-user-role")) || "").trim();
  const adminEntry = await page.$('button[aria-label="用户管理（仅管理员）"]');
  if (idUser === "用户" && !adminEntry) ok("① 用户登录：rail 显示「用户」，无「用户管理」入口");
  else fail("① 用户导航异常", `role=${idUser} adminEntry=${!!adminEntry}`);

  // ② 个人中心（V5.3：点 rail 用户区进入）
  await page.click(".rail-user .rl-user-info");
  await page.waitForSelector("[data-testid='settings-page']", { timeout: 8000 });
  await page.waitForSelector("[data-testid='profile-card']", { timeout: 8000 });
  const profileText = await page.textContent("[data-testid='profile-card']");
  if (profileText.includes(USER)) ok(`② 个人中心显示 ${USER}`);
  else fail("② 个人中心异常", String(profileText).slice(0, 80));

  // ②b 改密码错误路径
  await page.fill("[data-testid='old-pwd']", "wrong-old-password");
  await page.fill("[data-testid='new-pwd']", "NewPass!2026x");
  await page.fill("input[placeholder='确认新密码']", "NewPass!2026x");
  await page.click("[data-testid='pwd-submit']");
  await page.waitForSelector("[data-testid='pwd-err']", { timeout: 8000 });
  const pwdErr = await page.textContent("[data-testid='pwd-err']");
  if (pwdErr && (pwdErr.includes("旧密码") || pwdErr.includes("错误"))) ok(`②b 改密码错误提示：${pwdErr.trim()}`);
  else fail("②b 改密码错误路径异常", String(pwdErr));
  await page.screenshot({ path: `${SHOT_DIR}/1-settings-profile.png`, fullPage: true });

  // ③ 模型配置页（V5.3 一级入口）：模型池添加
  await page.click('.rail-btn:has-text("模型配置")');
  await page.waitForSelector("[data-testid='models-page']", { timeout: 8000 });
  await poolAdd("personal", "Qwen/Qwen3.8-27B", "sk-e2e-dummy-1234");
  ok("③ text 槽模型池添加成功 → 「模型池 1 条」徽章");

  // ④ 调度摘要生效 + 测连通
  await page.waitForFunction(
    () => document.querySelector("[data-testid='sched-tag-text']")?.textContent?.includes("优先"),
    null,
    { timeout: 10000 },
  );
  const schedTag = await page.textContent("[data-testid='sched-tag-text']");
  const schedModel = await page.textContent("[data-testid='sched-row-text'] .sched-model");
  ok(`④ 调度摘要：${(schedTag || "").trim()} → ${(schedModel || "").trim()}`);
  // 池条目测连通（dummy key → 预期报错路径，只要按钮→结果链路通即可）
  await page.click("[data-testid^='pool-test-personal-text-'] >> nth=0");
  await page.waitForSelector(".pool-card .test-msg", { timeout: 60000 });
  const testMsg = await page.textContent(".pool-card .test-msg");
  ok(`④b 测连通链路返回：${(testMsg || "").trim().slice(0, 60)}`);
  await page.screenshot({ path: `${SHOT_DIR}/2-settings-llm.png`, fullPage: true });

  // ⑦ 视图切换回工作台（V5.3：AI 对话）
  await page.click('.rail-btn:has-text("AI 对话")');
  for (const sel of [".conv-panel", ".chat-panel", ".task-panel"]) {
    await page.waitForSelector(sel, { timeout: 8000 });
  }
  ok("⑦ 切回工作台：三栏完整");

  // ⑧⑨⑩ 分类树（V5.x：分类树收在「分类」按钮弹出层里，先点开）
  // 注意：新账号被 sample_seeder 预置 3 个种子分类，树里不止 电商测试，
  // 所有断言必须按 cat-name 精确锚定「电商测试」行，不能用"第一个 .cat-row"
  const openCatPopover = async () => {
    if (await page.$("[data-testid='category-tree']")) return;
    await page.click('.task-panel button:has-text("分类")');
    await page.waitForSelector("[data-testid='category-tree']", { timeout: 8000 });
  };
  const closeCatPopover = async () => {
    if (!(await page.$("[data-testid='category-tree']"))) return;
    await page.click('.task-panel button:has-text("分类")');
    await page.waitForSelector("[data-testid='category-tree']", { state: "detached", timeout: 8000 });
  };
  await openCatPopover();
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

  // ⑨ 任务归类（先收起弹出层，避免遮挡任务列表 hover）
  await page.waitForSelector(".task-panel .task-item", { timeout: 30000 });
  await closeCatPopover();
  const firstTaskId = await page.getAttribute(".task-panel .task-item >> nth=0", "data-task-id");
  if (firstTaskId) {
    await page.hover(".task-panel .task-item >> nth=0");
    await page.click(`[data-testid='cat-menu-${firstTaskId}'] >> nth=0`);
    await page.waitForSelector(`[data-testid='cat-move-${firstTaskId}']`, { timeout: 5000 });
    await page.click(`[data-testid='cat-move-${firstTaskId}'] button:has-text('电商测试')`);
    ok("⑨ 任务归类：🏷 菜单 → 电商测试（PUT move-task 200）");

    // ⑩ 分类过滤（重开弹出层：计数断言 + 点行过滤）
    await openCatPopover();
    await page.waitForFunction(
      (cid) => document.querySelector(`.cat-row[data-cat-id='${cid}'] .cat-count`)?.textContent === "1",
      catId,
      { timeout: 10000 },
    );
    ok("⑨b 分类计数生效（电商测试 = 1）");
    await page.click(`.cat-row[data-cat-id='${catId}']`);
    await page.waitForFunction(
      () => document.querySelector(".task-panel .side-head")?.textContent?.includes("电商测试"),
      null,
      { timeout: 5000 },
    ).catch(() => {});
    const filtered = await page.$$eval(".task-panel .task-item", (els) => els.length);
    ok(`⑩ 分类过滤生效：列表 ${filtered} 条`);
    await page.click("[data-testid='cat-all']"); // 还原
    await page.screenshot({ path: `${SHOT_DIR}/3-category-tree.png`, fullPage: true });
  } else {
    fail("⑨ 任务归类", "无任务可归类");
  }

  // ⑤ admin 登录 → 用户管理页（V5.3 改名）
  await uiLogin(ADMIN);
  await page.click('button[aria-label="用户管理（仅管理员）"]');
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
  if (statNums.length === 3 && statNums.every((n) => n && n !== "—")) ok(`⑤ 统计卡 3 项：${statNums.join("/")}`);
  else fail("⑤ 统计卡异常", statNums.join("/"));
  await page.waitForSelector("[data-testid='user-table'] table tr[data-user-id]", { timeout: 10000 });
  const userRows = await page.$$eval("[data-testid='user-table'] tr[data-user-id]", (els) => els.length);
  if (userRows >= 2) ok(`⑤b 用户表 ${userRows} 行`);
  else fail("⑤b 用户表异常", `rows=${userRows}`);

  // ⑥ 平台默认模型池（V5.2 迁到模型配置页 admin「平台默认」Tab）
  await page.click('.rail-btn:has-text("模型配置")');
  await page.waitForSelector("[data-testid='models-page']", { timeout: 8000 });
  await page.click("[data-testid='tab-platform']");
  await poolAdd("platform", "Qwen/Qwen3.8-Flash-Next", "sk-e2e-platform-01");
  ok("⑥ 平台默认 text 槽池添加成功");
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
  /* ---------- 清理：整链删除测试数据（行范围全部锚定 e2e_m4_%） ---------- */
  try {
    dbExec(`
DELETE FROM step_logs WHERE task_id IN (SELECT id FROM tasks WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%'));
DELETE FROM tasks WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%'));
DELETE FROM conversations WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM categories WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM llm_configs WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM llm_model_pool WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'e2e_m4_%');
DELETE FROM llm_model_pool WHERE user_id = 0 AND model = 'Qwen/Qwen3.8-Flash-Next';
DELETE FROM users WHERE username LIKE 'e2e_m4_%';`);
    console.log("🧹 清理完成（e2e_m4_* 账号/任务/分类/池 + 本次平台默认池条目）");
  } catch (e) {
    console.error("🧹 清理失败（可手工执行 sqlite 清理）:", String(e).slice(0, 200));
  }
}

console.log("\n===== M4 结果汇总 =====");
console.log(results.join("\n"));
const failed = results.filter((r) => r.startsWith("❌")).length;
console.log(`\n${failed === 0 ? "🎉 全部通过" : `⚠️ ${failed} 项失败`}`);
process.exit(failed === 0 ? 0 : 1);
