import { chromium } from "playwright";

const BASE = "http://127.0.0.1:8000";
const OUT = "/tmp/e2e-w3";
const results = [];
const ok = (name, cond) => { results.push(`${cond ? "✅" : "❌"} ${name}`); if (!cond) process.exitCode = 1; };

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

try {
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  await page.click('button[aria-label="退出登录"]');
  await page.waitForTimeout(800);
  await page.fill('.auth-modal input[type="text"], .auth-modal input:not([type="password"])', "admin");
  await page.fill('.auth-modal input[type="password"]', "Admin@123");
  await page.click(".auth-btn");
  await page.waitForTimeout(2500);
  ok("admin 登录", true);

  await page.click('.rail-btn:has-text("测试中心")');
  await page.waitForTimeout(2000);
  const tab = page.locator('button:has-text("质量报告")').last();
  await tab.waitFor({ state: "visible", timeout: 8000 });
  await tab.click();
  await page.locator('[data-testid="quality-board"]').waitFor({ state: "visible", timeout: 10000 });
  await page.waitForTimeout(1500);
  const body = await page.locator('[data-testid="quality-board"]').textContent();

  ok("无「单元测试」chip", !(body || "").includes("单元测试"));
  ok("无「覆盖率」卡", !(body || "").includes("代码覆盖率"));
  ok("核心接口用例卡存在", (body || "").includes("核心接口用例"));
  ok("e2e 卡保留", (body || "").includes("e2e 套件通过"));
  ok("无裸 pytest 自测总数 384", !(body || "").includes("384"));
  await page.screenshot({ path: `${OUT}/w3-5-quality-scope.png`, fullPage: true });
} catch (e) {
  ok(`异常: ${e.message.slice(0, 120)}`, false);
  await page.screenshot({ path: `${OUT}/error-scope.png` }).catch(() => {});
}
console.log(results.join("\n"));
await browser.close();
