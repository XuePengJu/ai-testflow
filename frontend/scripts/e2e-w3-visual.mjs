/**
 * W3 波次目检截图（Playwright）：
 *   ① 新 rail 结构（工作区 4 项 + 底部设置组）
 *   ② 首页驾驶舱（主线文案 + 示例 chips + 最近用例/最近测试运行摘要卡）
 *   ③ 测试中心页（指标卡 + 全链路测试 Tab）
 *   ④ 测试中心 · 质量报告 Tab（QualityBoard 并入）
 * 截图落档 /tmp/e2e-w3/
 *
 * 运行：node scripts/e2e-w3-visual.mjs（W3_URL 默认 http://127.0.0.1:8000）
 */
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = process.env.W3_URL || "http://127.0.0.1:8000";
const OUT = "/tmp/e2e-w3";
fs.mkdirSync(OUT, { recursive: true });

const results = [];
const ok = (name, cond) => { results.push(`${cond ? "✅" : "❌"} ${name}`); if (!cond) process.exitCode = 1; };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);

  // 访客自动登录 → 退出 → admin 登录（admin 才能看到用户管理入口 + 质量报告运行按钮）
  await page.click('button[aria-label="退出登录"]');
  await page.waitForTimeout(800);
  await page.fill('.auth-modal input[type="text"], .auth-modal input:not([type="password"])', "admin");
  await page.fill('.auth-modal input[type="password"]', "Admin@123");
  await page.click(".auth-btn");
  await page.waitForTimeout(2500);
  ok("admin 登录", await page.locator(".rail-user .rl-user-name").textContent().then((t) => (t || "").includes("admin")).catch(() => false));

  // ① 新 rail 结构断言：工作区 4 项 + 设置组 5 项
  for (const label of ["AI 会话", "用例库", "测试中心", "知识库"]) {
    ok(`rail 工作区含「${label}」`, (await page.locator(`.rail-btn:has-text("${label}")`).count()) > 0);
  }
  for (const label of ["模型配置", "用户管理", "被测系统", "源码", "系统自检"]) {
    ok(`rail 设置组含「${label}」`, (await page.locator(`.rail-btn:has-text("${label}")`).count()) > 0);
  }
  // 注：不能用 :has-text 粗匹配——「测试中心」的 rl-tip 含「全链路测试/质量报告」字样，会误报；
  // 改为精确匹配按钮文字列（.rl-txt）
  const railTexts = await page.locator(".rail .rail-btn .rl-txt").allTextContents();
  ok("rail 无旧「全链路测试」入口", !railTexts.includes("全链路测试"));
  ok("rail 无旧「质量报告」入口", !railTexts.includes("质量报告"));
  ok("被测系统显示名 = 被测系统 · DBERP", (await page.locator('.rail-subitem:has-text("被测系统 · DBERP")').count()) > 0);
  await page.screenshot({ path: `${OUT}/w3-1-rail.png`, fullPage: false });

  // ② 首页驾驶舱
  await page.click('.rail-btn:has-text("AI 会话")');
  await page.waitForTimeout(1200);
  ok("首页主线文案", (await page.locator('.welcome h2:has-text("输入需求，生成用例，一键全链路测试")').count()) > 0);
  ok("示例 chips", (await page.locator(".welcome .sample-chip").count()) >= 3);
  ok("摘要卡：最近用例", (await page.locator('[data-testid="home-recent-cases"]').count()) > 0);
  ok("摘要卡：最近测试运行", (await page.locator('[data-testid="home-recent-runs"]').count()) > 0);
  ok("旧 4 张欢迎卡已移除", (await page.locator(".welcome .quick-card").count()) === 0);
  await page.screenshot({ path: `${OUT}/w3-2-home-cockpit.png`, fullPage: false });

  // ③ 测试中心：指标卡 + 全链路测试 Tab
  await page.click('.rail-btn:has-text("测试中心")');
  await page.waitForTimeout(1500);
  ok("测试中心页头", (await page.locator('h2:has-text("测试中心")').count()) > 0);
  ok("指标卡 3 张", (await page.locator('[data-testid="tc-metrics"] .stat-card').count()) === 3);
  ok("全链路测试 Tab", (await page.locator('[data-testid="tc-tab-exec"]').count()) > 0);
  await page.screenshot({ path: `${OUT}/w3-3-test-center.png`, fullPage: false });

  // ④ 测试中心 · 质量报告 Tab（QualityBoard 并入）
  await page.click('[data-testid="tc-tab-quality"]');
  await page.waitForTimeout(1500);
  ok("质量报告 Tab 内容（QualityBoard）", (await page.locator('[data-testid="quality-board"]').count()) > 0);
  ok("运行测试按钮（admin 可见）", (await page.locator('[data-testid="run-quality-test"], .btn-primary:has-text("运行测试")').count()) > 0);
  await page.screenshot({ path: `${OUT}/w3-4-test-center-quality.png`, fullPage: false });

  // ⑤ 兼容重定向：nav-to quality → 测试中心
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("nav-to", { detail: "quality" })));
  await page.waitForTimeout(800);
  ok("旧 quality 视图重定向到测试中心", (await page.locator('h2:has-text("测试中心")').count()) > 0);

  ok("截图落盘", true);
} catch (e) {
  ok(`异常中断: ${e.message.slice(0, 120)}`, false);
  await page.screenshot({ path: `${OUT}/w3-0-error.png` }).catch(() => {});
}

console.log(results.join("\n"));
await browser.close();
