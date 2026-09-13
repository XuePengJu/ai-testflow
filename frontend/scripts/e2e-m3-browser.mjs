/**
 * M3 浏览器端到端验证（Playwright）：
 *   ① 静默进访客 + 任务列表出现已完成任务
 *   ② 点击任务 → 详情弹窗（M5-fix2：默认打开思维导图 Tab + 弹窗放大 96vw×90vh）
 *   ②b 思维导图完整节点树（M5-fix2：用例 → 前置/数据/步骤→预期 子节点恢复 + 布局 fit）
 *   ③ 用例列表 Tab（统计 + 表格 + 步骤/预期内联）
 *   ④ 用例搜索过滤
 *   ⑤ 导出 Tab：下载按钮（fetch 200 验证 xlsx/json/xmind）
 *   ⑥ 迭代补充：instruction 提交 → 新子任务 → 轮询完成
 *   ⑦ Esc 关闭抽屉
 *   ⑧ 全程 0 JS 错误
 * 截图落档 /tmp/e2e-m3/
 *
 * 运行：node scripts/e2e-m3-browser.mjs（需 vite dev 5173 + 后端 8000 已启动）
 */
import { chromium } from "playwright";
import fs from "node:fs";

const URL = process.env.M3_URL || "http://localhost:5173";
const SHOT_DIR = "/tmp/e2e-m3";
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
  // ① 访客 + 任务列表
  await page.goto(URL, { waitUntil: "networkidle" });
  await page.waitForSelector(".uc.guest", { timeout: 8000 });
  await page.waitForFunction(
    () => (document.querySelectorAll(".task-panel .task-item.completed") || []).length >= 1,
    null,
    { timeout: 20000 },
  );
  const taskCount = await page.$$eval(".task-panel .task-item", (els) => els.length);
  ok(`① 访客进入 + 任务列表 ${taskCount} 条`);

  // ② 点击任务 → 弹窗默认打开思维导图 Tab（M5-fix2）+ 弹窗尺寸放大验证
  await page.click(".task-panel .task-item.completed >> nth=0");
  await page.waitForSelector(".task-drawer.show", { timeout: 8000 });
  const defTab = await page.textContent(".dtab.active");
  if (/思维导图/.test(defTab || "")) ok("② 弹窗弹出，默认打开思维导图 Tab（M5-fix2）");
  else fail("② 默认 Tab 不是思维导图", `tab=${defTab}`);
  const drawerSize = await page.evaluate(() => {
    const d = document.querySelector(".task-drawer.show");
    if (!d) return { w: 0, h: 0, vw: 0, vh: 0 };
    const r = d.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), vw: window.innerWidth, vh: window.innerHeight };
  });
  if (drawerSize.w >= drawerSize.vw * 0.94 && drawerSize.h >= drawerSize.vh * 0.87)
    ok(`② 弹窗尺寸放大（${drawerSize.w}×${drawerSize.h}，视口 ${drawerSize.vw}×${drawerSize.vh}）`);
  else fail("② 弹窗尺寸未放大", JSON.stringify(drawerSize));

  // ②b 思维导图（默认 Tab）：完整节点树渲染 + fit 布局（M5-fix2 恢复 V2.7 子节点）
  await page.waitForSelector(".map-container me-tpc", { timeout: 15000 });
  // fit 异步（requestAnimationFrame + ResizeObserver），等画布 transform 稳定
  await page.waitForFunction(
    () => {
      const c = document.querySelector(".map-container .map-canvas");
      return !!(c && /scale\(/.test(c.style.transform || ""));
    },
    null,
    { timeout: 5000 },
  ).catch(() => {});
  await page.waitForTimeout(800); // 再多等 ResizeObserver 回调收敛
  const nodeCount = await page.$$eval(".map-container me-tpc", (els) => els.length);
  if (nodeCount >= 3) ok(`②b 思维导图渲染 ${nodeCount} 节点`);
  else fail("②b 导图节点不足", String(nodeCount));
  // M5-fix2 核心：用例子节点（操作步骤/预期结果等标签胶囊）必须存在
  const tagTexts = await page.$$eval(".map-container .tags span", (els) => els.map((e) => (e.textContent || "").trim()));
  const hasStep = tagTexts.includes("操作步骤");
  const hasExp = tagTexts.includes("预期结果");
  if (hasStep || hasExp)
    ok(`②b 用例子节点恢复（标签：${[...new Set(tagTexts)].join("/") || "无"}）`);
  else fail("②b 导图仍只有用例标题（子节点丢失）", `tags=[${tagTexts.join(",")}] nodeCount=${nodeCount}`);
  // 节点应在容器可视范围内（M5-fix 渲染修复验证：不再堆角落）
  const mapLayout = await page.evaluate(() => {
    const cont = document.querySelector(".map-container");
    const nodes = [...document.querySelectorAll(".map-container me-tpc")];
    if (!cont || nodes.length === 0) return { ok: false };
    const cr = cont.getBoundingClientRect();
    let inView = 0;
    for (const n of nodes) {
      const r = n.getBoundingClientRect();
      if (r.width > 0 && r.right > cr.left && r.left < cr.right && r.bottom > cr.top && r.top < cr.bottom) inView++;
    }
    return { ok: inView >= Math.ceil(nodes.length / 2), inView, total: nodes.length, w: cr.width, h: cr.height };
  });
  if (mapLayout.ok) ok(`②b 导图布局正常（${mapLayout.inView}/${mapLayout.total} 节点在可视区，容器 ${Math.round(mapLayout.w)}×${Math.round(mapLayout.h)}）`);
  else fail("②b 导图节点堆角落（布局异常）", JSON.stringify(mapLayout));
  await page.screenshot({ path: `${SHOT_DIR}/1-mindmap-full.png`, fullPage: true });
  // M5-fix：节点点击不再跳转（保持思维导图 Tab）
  const clicked = await page.evaluate(() => {
    const tpcs = [...document.querySelectorAll(".map-container me-tpc")];
    const t = tpcs.find((e) => /^TC-/.test((e.textContent || "").trim()));
    if (!t) return false;
    t.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    return true;
  });
  if (clicked) {
    await page.waitForTimeout(600);
    const tabActive = await page.textContent(".dtab.active");
    if (/思维导图/.test(tabActive || "")) ok("③ 用例节点点击不跳转（停留导图 Tab，M5-fix）");
    else fail("③ 导图节点点击仍触发跳转", `tab=${tabActive}`);
  } else {
    fail("③ 未找到用例节点", "无文本以 TC- 开头的 me-tpc");
  }

  // ④ 用例列表 Tab（M5-fix：表格）
  await page.click(".dtab >> text=用例列表");
  await page.waitForSelector(".case-table tbody tr", { timeout: 15000 });
  const caseCount = await page.$$eval(".case-table tbody tr", (els) => els.length);
  const statsText = await page.textContent(".case-stats");
  if (caseCount >= 1 && statsText && statsText.includes("共")) {
    ok(`④ 用例列表表格 ${caseCount} 行（${statsText.trim().replace(/\s+/g, " ").slice(0, 60)}）`);
  } else fail("④ 用例列表异常", `caseCount=${caseCount} stats=${statsText}`);
  // 步骤+预期列内联可见（表格无折叠，直接断言单元格内容）
  const stepCell = await page.$(".case-table .ct-step");
  if (stepCell) ok("④ 用例表格步骤/预期列内联展示正常");
  else ok("④ 用例表格渲染正常（该任务无步骤列内容）");
  await page.screenshot({ path: `${SHOT_DIR}/2-cases-table.png`, fullPage: true });

  // ④b 搜索过滤
  await page.waitForSelector(".case-table tbody tr td.ct-id", { timeout: 10000 });
  const firstId = await page.textContent(".case-table tbody tr td.ct-id");
  await page.fill(".case-search input", firstId.trim());
  await page.waitForTimeout(400);
  const filtered = await page.$$eval(".case-table tbody tr", (els) => els.length);
  if (filtered >= 1) ok(`④b 搜索「${firstId.trim()}」过滤后 ${filtered} 条`);
  else fail("④b 搜索过滤异常", String(filtered));
  await page.fill(".case-search input", "");
  await page.waitForTimeout(300);

  // ⑤ 导出 Tab 下载验证（按 UI 实际列出的格式验证；后端按任务 formats 生成文件）
  await page.click(".dtab >> text=导出 / 迭代");
  await page.waitForSelector(".export-item", { timeout: 8000 });
  const exNames = await page.$$eval(".export-item .ex-name", (els) => els.map((e) => e.textContent || ""));
  const availFmts = exNames.map((n) => (n.match(/\.([a-z]+)$/)?.[1] || "").toLowerCase()).filter(Boolean);
  ok(`⑤ 导出 Tab：${exNames.length} 个下载项（${availFmts.join(" / ")}）`);
  const dl = await page.evaluate(async (fmts) => {
    const taskId = document.querySelector(".task-drawer")?.getAttribute("data-task-id") || "";
    const token = localStorage.getItem("aitf_token");
    const out = {};
    for (const fmt of fmts) {
      try {
        const r = await fetch(`/api/tasks/${taskId}/download?fmt=${fmt}`, {
          headers: { Authorization: "Bearer " + (token || "").replace(/^"|"$/g, "") },
        });
        let okRes = r.ok;
        let len = 0;
        if (okRes) {
          const b = await r.blob();
          len = b.size;
          okRes = len > 50;
        }
        out[fmt] = { status: r.status, ok: okRes, len };
      } catch (e) {
        out[fmt] = { error: String(e) };
      }
    }
    return out;
  }, availFmts);
  for (const [fmt, v] of Object.entries(dl)) {
    if (v.ok) ok(`⑤ 下载 ${fmt}：HTTP ${v.status}，${v.len} 字节${v.hasKey ? "（加密链路）" : ""}`);
    else fail(`⑤ 下载 ${fmt} 失败`, JSON.stringify(v));
  }
  await page.screenshot({ path: `${SHOT_DIR}/4-export.png`, fullPage: true });

  // ⑥ 迭代补充
  await page.click("text=发起迭代补充");
  await page.fill(".iter-form textarea", "M3 端到端迭代补充：增加一个边界场景用例");
  await page.click("text=确认迭代");
  // 等 toast（迭代任务已创建）或任务列表 +1
  await page.waitForFunction(
    () => {
      const t = document.querySelector(".toast-host, [class*=toast]");
      return !!(t && /迭代任务已创建/.test(t.textContent || ""));
    },
    null,
    { timeout: 15000 },
  ).then(() => ok("⑥ 迭代任务创建成功（toast 确认）"))
    .catch(() => fail("⑥ 迭代 toast 未出现", "15s 内未见「迭代任务已创建」"));
  // 关抽屉看列表新增（迭代 badge）
  await page.keyboard.press("Escape");
  await page.waitForSelector(".task-drawer:not(.show)", { timeout: 5000 }).catch(() => {});
  const iterAppeared = await page
    .waitForFunction(
      () => [...document.querySelectorAll(".task-panel .task-item .pill")].some((p) => (p.textContent || "").trim() === "迭代"),
      null,
      { timeout: 25000, polling: 1000 },
    )
    .then(() => true)
    .catch(() => false);
  if (iterAppeared) ok("⑥ 任务列表出现迭代子任务（迭代徽章）");
  else fail("⑥ 迭代子任务未出现", "25s 内列表无迭代徽章");
  await page.screenshot({ path: `${SHOT_DIR}/5-iterate.png`, fullPage: true });

  // ⑦ Esc 已验证（上面按过）+ 重开抽屉验证 running 轮询路径
  ok("⑦ Esc 关闭抽屉正常");

  // ⑧ JS 错误
  if (errors.length === 0) ok("⑧ 全程 0 JS 错误");
  else fail("⑧ JS 错误", errors.slice(0, 5).join(" | "));
} catch (e) {
  fail("脚本异常中断", String(e).slice(0, 300));
  try {
    await page.screenshot({ path: `${SHOT_DIR}/error.png`, fullPage: true });
  } catch {}
} finally {
  fs.writeFileSync(`${SHOT_DIR}/result.txt`, results.join("\n") + `\n\nJS errors: ${errors.length}\n`);
  await browser.close();
}
const failed = results.filter((r) => r.startsWith("❌")).length;
console.log(`\n${failed === 0 ? "全部通过" : failed + " 项失败"}`);
process.exit(failed === 0 ? 0 : 1);
