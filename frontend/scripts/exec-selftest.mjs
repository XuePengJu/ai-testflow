/**
 * dev-only：ExecutionPanel 纯函数自测（项目无测试框架，用 esbuild 现场打包后由 node 执行）。
 *
 * 覆盖契约关键分支（渲染逻辑无 undefined 访问）：
 *   1. running / pending 无 report            → getReport 返回 null
 *   2. failed run（error 有/无）              → isTerminal=true、徽标红、报告可缺省
 *   3. 全通过 run                              → 徽标绿、rateText passed/total
 *   4. completed 含失败                        → 徽标黄（pill-run）
 *   5. report_json 为字符串（后端漏解析兜底）  → 前端就地解析
 *   6. 截图 null / summary 缺字段              → 不抛错
 *   7. fmtDuration / rateText 边界             → 0/null、<1s
 *
 * 运行：cd frontend && node scripts/exec-selftest.mjs
 * 纯函数与组件在同一文件（文件所有权约束），故打包整个 ExecutionPanel.tsx；
 * 其依赖（client/authState/aesGcm）均无模块级 DOM 副作用，node 下可安全求值。
 */
import { build } from "esbuild";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const tmpDir = resolve(root, ".selftest-tmp");
mkdirSync(tmpDir, { recursive: true });

const assert = (cond, label) => {
  if (!cond) throw new Error(`自测失败：${label}`);
  console.log(`  ok - ${label}`);
};

// 用例数据严格按契约 2 mock
const mockReport = (over = {}) => ({
  summary: { total: 7, passed: 5, failed: 1, skipped: 1, duration_ms: 12345, ...(over.s || {}) },
  cases: [
    { case_id: "TC-001", node_id: "test_cases_0.py::test_tc_001", title: "登录成功", outcome: "passed", duration_ms: 100, error: null, screenshot: null },
    { case_id: "TC-002", node_id: "test_cases_0.py::test_tc_002", title: "下单失败提示", outcome: "failed", duration_ms: 220, error: "expect(locator).to_be_visible()", screenshot: "shots/test_tc_002.png" },
    ...(over.cases || []),
  ],
  environment: { browser: "chromium", base_url: "https://demo.example.com" },
});
const baseRun = (over = {}) => ({
  id: "r1", task_id: "t1", user_id: 1, trigger: "manual",
  progress: 0, total: 7, passed: 0, failed: 0, skipped: 0, duration_ms: 0,
  report: null, error: null,
  created_at: "2026-09-23T10:00:00", started_at: null, finished_at: null,
  ...over,
});

const entry = /* ts */ `
import { getReport, runBadgeInfo, fmtDuration, rateText, isTerminal } from "${resolve(root, "src/components/task/ExecutionPanel.tsx")}";
// 用例数据严格按契约 2 mock（工厂须在 bundle 内定义，函数无法经 JSON 序列化传入）
const mockReport = (over = {}) => ({
  summary: { total: 7, passed: 5, failed: 1, skipped: 1, duration_ms: 12345, ...(over.s || {}) },
  cases: [
    { case_id: "TC-001", node_id: "test_cases_0.py::test_tc_001", title: "登录成功", outcome: "passed", duration_ms: 100, error: null, screenshot: null },
    { case_id: "TC-002", node_id: "test_cases_0.py::test_tc_002", title: "下单失败提示", outcome: "failed", duration_ms: 220, error: "expect(locator).to_be_visible()", screenshot: "shots/test_tc_002.png" },
    ...(over.cases || []),
  ],
  environment: { browser: "chromium", base_url: "https://demo.example.com" },
});
const baseRun = (over = {}) => ({
  id: "r1", task_id: "t1", user_id: 1, trigger: "manual",
  progress: 0, total: 7, passed: 0, failed: 0, skipped: 0, duration_ms: 0,
  report: null, error: null,
  created_at: "2026-09-23T10:00:00", started_at: null, finished_at: null,
  ...(over || {}),
});
globalThis.__cases = [
  ["running 无 report", baseRun({ status: "running", progress: 3, total: 7 })],
  ["pending 无 report", baseRun({ status: "pending" })],
  ["全通过 run", baseRun({ status: "completed", passed: 7, total: 7, duration_ms: 8000, report: mockReport({ s: { total: 7, passed: 7, failed: 0, skipped: 0 } }) })],
  ["completed 含失败", baseRun({ status: "completed", passed: 5, failed: 1, total: 7, duration_ms: 12345, report: mockReport() })],
  ["failed run（进程级，无 report 带 error）", baseRun({ status: "failed", error: "pytest 超时", duration_ms: 600000 })],
  ["failed run（error 为 null）", baseRun({ status: "failed" })],
  ["report_json 字符串兜底", baseRun({ status: "completed", passed: 5, failed: 1, total: 7, report: null, report_json: JSON.stringify(mockReport()) })],
  ["report_json 非法字符串", baseRun({ status: "failed", report: null, report_json: "not-json" })],
  ["截图 null 用例", baseRun({ status: "completed", report: mockReport({ cases: [{ case_id: "TC-003", node_id: "n", title: "t", outcome: "failed", duration_ms: 5, error: "boom", screenshot: null }] }) })],
];
globalThis.__fns = { getReport, runBadgeInfo, fmtDuration, rateText, isTerminal };
export { getReport, runBadgeInfo, fmtDuration, rateText, isTerminal };
`;

const outfile = resolve(tmpDir, "bundle.mjs");
await build({
  stdin: { contents: entry, loader: "tsx", resolveDir: root },
  bundle: true,
  platform: "node",
  format: "esm",
  outfile,
  logLevel: "silent",
});

const { getReport, runBadgeInfo, fmtDuration, rateText, isTerminal } = await import(pathToFileURL(outfile).href);

// 1/2：running / pending 无 report → null，报告视图走占位分支
const running = baseRun({ status: "running", progress: 3, total: 7 });
assert(getReport(running) === null, "running 无 report → getReport=null");
assert(rateText(running) === "0/7", "running rateText=0/7");
assert(runBadgeInfo(running).text === "执行中", "running 徽标=执行中（蓝）");
assert(getReport(baseRun({ status: "pending" })) === null, "pending 无 report → getReport=null");
assert(rateText(baseRun({ status: "pending" })) === "—", "pending rateText=—");

// 3：全通过 → 绿、rate 正确
const allPass = baseRun({ status: "completed", passed: 7, total: 7, duration_ms: 8000, report: mockReport({ s: { total: 7, passed: 7, failed: 0, skipped: 0 } }) });
const apBadge = runBadgeInfo(allPass);
assert(apBadge.cls.includes("pill-ok"), "全通过徽标绿（pill-ok）");
assert(rateText(allPass) === "7/7", "全通过 rateText=7/7");
const apRep = getReport(allPass);
assert(apRep.summary.total === 7 && apRep.cases.length === 2 && apRep.environment.browser === "chromium", "全通过 report 结构可读");
assert(apRep.cases[0].screenshot === null, "截图 null 不抛错");

// 4：completed 含失败 → 黄
const withFail = baseRun({ status: "completed", passed: 5, failed: 1, total: 7, duration_ms: 12345, report: mockReport() });
assert(runBadgeInfo(withFail).cls.includes("pill-run"), "含失败徽标黄（pill-run）");

// 5：failed run（进程级）→ 红、terminal、无报告占位含 error
const failedRun = baseRun({ status: "failed", error: "pytest 超时" });
assert(runBadgeInfo(failedRun).cls.includes("pill-fail"), "failed 徽标红");
assert(isTerminal(failedRun) && isTerminal(withFail) && !isTerminal(running), "isTerminal 终态判定");
assert(getReport(failedRun) === null, "failed run 无 report → 占位分支");

// 6：report_json 字符串兜底 / 非法字符串
const rawStr = baseRun({ status: "completed", report: null, report_json: JSON.stringify(mockReport()) });
assert(getReport(rawStr)?.summary.total === 7, "report_json 字符串兜底解析");
assert(getReport(baseRun({ status: "failed", report: null, report_json: "not-json" })) === null, "report_json 非法 → null");

// 7：fmtDuration 边界
assert(fmtDuration(0) === "—" && fmtDuration(null) === "—" && fmtDuration(undefined) === "—", "fmtDuration 0/null → —");
assert(fmtDuration(850) === "850ms" && fmtDuration(12345) === "12.3s", "fmtDuration ms/s 换算");

// 清理 & 汇总
rmSync(tmpDir, { recursive: true, force: true });
console.log("\nExecutionPanel 纯函数自测：9/9 组全部通过 ✅");
