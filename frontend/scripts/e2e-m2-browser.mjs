/**
 * M2 浏览器端到端验证（Playwright）：
 *   ① 静默进访客 + 三栏布局渲染
 *   ② 发消息 → SSE 流式回复（mock）→ 出现「生成测试用例」按钮
 *   ③ 点击生成 → 任务创建 → 消息升级步骤卡 → 轮询到完成
 *   ④ 任务列表出现新任务 + 点击定位滚动高亮任务卡
 *   ⑤ 新建会话 → 切回旧会话回放历史消息
 *   ⑥ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m2/
 *
 * 运行：node scripts/e2e-m2-browser.mjs（需 vite dev 5173 + 后端 8000 已启动）
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M2_URL || "http://localhost:5173";
const SHOT_DIR = "/tmp/e2e-m2";
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

try {
  // ① 首开：静默访客 + 三栏布局
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".uc.guest", { timeout: 8000 });
  const guestText = await page.textContent(".uc.guest");
  if (/访客 · 剩 \d+ 小时/.test(guestText || "")) ok("① 静默进访客（" + guestText.trim() + "）");
  else fail("① 访客标识异常", guestText || "empty");
  await page.waitForSelector(".conv-panel", { timeout: 5000 });
  await page.waitForSelector(".chat-panel .chat-stream", { timeout: 5000 });
  await page.waitForSelector(".task-panel", { timeout: 5000 });
  ok("① 三栏布局渲染（会话栏 + 对话流 + 任务栏）");
  await page.screenshot({ path: `${SHOT_DIR}/1-layout.png`, fullPage: true });

  // ② 发消息 → SSE 流式回复
  const req =
    "采购管理 - 采购订单创建。功能点：新增采购单、提交审批、审批通过/驳回。业务规则：金额超 5 万需二级审批。";
  await page.fill(".chat-input-row textarea", req);
  await page.click(".send-btn");
  // 等 AI 回复 done（生成用例按钮出现）
  await page.waitForSelector(".confirm-btn", { timeout: 30000 });
  const replyText = await page.textContent(".msg-ai .reply-body");
  if (replyText && replyText.trim().length > 10) ok("② SSE 流式回复完成（" + replyText.trim().slice(0, 40) + "…）");
  else fail("② 流式回复异常", (replyText || "").slice(0, 100));
  const sendBtnBack = await page.textContent(".send-btn");
  if (!/停止/.test(sendBtnBack || "")) ok("② 流式结束输入区恢复（发送按钮回归）");
  else fail("② 输入区未恢复", sendBtnBack || "");
  await page.screenshot({ path: `${SHOT_DIR}/2-stream-reply.png`, fullPage: true });

  // ③ 点击「生成测试用例」→ 任务创建 → 步骤卡轮询
  await page.click(".confirm-btn");
  // 等任务卡出现
  await page.waitForSelector(".task-steps-card", { timeout: 15000 });
  ok("③ 任务已创建，消息升级为步骤卡");
  // 等轮询到完成（mock 流水线很快，给 60s）
  await page.waitForFunction(
    () => {
      const pills = document.querySelectorAll(".tsc-head .pill");
      for (const p of pills) if (/已完成/.test(p.textContent || "")) return true;
      return false;
    },
    { timeout: 60000, polling: 1000 },
  );
  const cardText = await page.textContent(".task-steps-card");
  if (/已完成/.test(cardText || "") && /用例/.test(cardText || "")) ok("③ 步骤卡轮询到终态（" + (cardText || "").replace(/\s+/g, " ").slice(0, 60) + "…）");
  else fail("③ 步骤卡终态异常", (cardText || "").slice(0, 150));
  await page.screenshot({ path: `${SHOT_DIR}/3-task-card.png`, fullPage: true });

  // ④ 任务列表出现新任务 + 点击定位（消息流仍在含任务卡的会话）
  await page.waitForFunction(
    () => (document.querySelectorAll(".task-panel .task-item") || []).length >= 1,
    null,
    { timeout: 15000 },
  );
  const taskItems = await page.$$eval(".task-panel .task-item", (els) =>
    els.map((e) => (e.textContent || "").replace(/\s+/g, " ").trim()),
  );
  ok("④ 任务列表出现任务（" + taskItems.length + " 条）");
  // 当前会话内点击任务「⌖」定位按钮 → 滚动定位 + 高亮任务卡
  // （M3/M4 交互演进：任务项整行点击=打开详情抽屉，定位收敛到 ⌖ 按钮）
  await page.click(".task-panel .t-locate >> nth=0");
  await page.waitForTimeout(600);
  const flashed = await page.$eval(".task-steps-card", (el) => el.classList.contains("flash")).catch(() => false);
  if (flashed) ok("④ 点击任务列表项 → 定位滚动 + 高亮任务卡");
  else fail("④ 任务定位高亮未触发", "flash class not found");
  // 切新对话后点击 → 应提示「不在当前会话」而非静默
  await page.click(".conv-panel .side-head .qtag"); // ＋ 新对话
  await page.waitForSelector(".welcome", { timeout: 5000 });
  await page.click(".task-panel .t-locate >> nth=0");
  await page.waitForFunction(
    () => {
      const toasts = document.querySelectorAll(".toast, [class*=toast]");
      for (const t of toasts) if (/不在当前会话/.test(t.textContent || "")) return true;
      return false;
    },
    null,
    { timeout: 5000 },
  ).then(() => ok("④ 新会话中点击任务 → 提示「不在当前会话」")).catch(() => fail("④ 新会话点击任务无提示", "toast not found"));
  await page.screenshot({ path: `${SHOT_DIR}/4-task-focus.png`, fullPage: true });

  // ⑤ 切回旧会话回放历史
  const histCount = await page.$$eval(".conv-panel .hist-item", (els) => els.length);
  if (histCount >= 1) {
    await page.click(".conv-panel .hist-item >> nth=0");
    await page.waitForFunction(
      () => (document.querySelectorAll(".chat-stream .msg") || []).length >= 2,
      null,
      { timeout: 10000 },
    );
    const msgs = await page.$$eval(".chat-stream .msg", (els) => els.length);
    ok(`⑤ 会话回放（侧栏 ${histCount} 条会话，回放 ${msgs} 条消息）`);
  } else {
    fail("⑤ 会话侧栏为空", "no hist-item");
  }
  await page.screenshot({ path: `${SHOT_DIR}/5-conv-replay.png`, fullPage: true });

  // ⑥ JS 错误汇总
  if (errors.length === 0) ok("⑥ 全程 0 JS 错误");
  else fail("⑥ JS 错误", errors.join(" | ").slice(0, 400));
} catch (e) {
  fail("流程异常中断", String(e).slice(0, 400));
  await page.screenshot({ path: `${SHOT_DIR}/0-error.png`, fullPage: true }).catch(() => {});
} finally {
  fs.writeFileSync(`${SHOT_DIR}/result.txt`, results.join("\n") + "\n");
  await browser.close();
}
console.log(results.includes(results.find((r) => r.startsWith("❌"))) ? "\n存在失败项" : "\n全部通过");
process.exit(results.some((r) => r.startsWith("❌")) ? 1 : 0);
