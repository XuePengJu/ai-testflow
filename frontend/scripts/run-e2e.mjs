/**
 * M0 Node e2e 轻量 runner：逐个执行 e2e-m1~m5 脚本，产出 e2e-report.json。
 *
 * 用法：node frontend/scripts/run-e2e.mjs [输出目录]
 *   输出目录默认 <项目根>/quality_data；每套件记录 name/outcome/duration_ms/error，
 *   单套件失败不中断后续套件（try/catch + 退出码判定）。
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outDir = process.argv[2] || path.resolve(__dirname, "../../quality_data");
fs.mkdirSync(outDir, { recursive: true });

/** 与设计文档 2.2① 对齐的套件清单（按里程碑顺序执行） */
const SUITES = [
  "e2e-m1-browser.mjs",
  "e2e-m2-browser.mjs",
  "e2e-m3-browser.mjs",
  "e2e-m4-browser.mjs",
  "e2e-m5-smoke.mjs",
];

/** 单套件超时（毫秒）：浏览器脚本含启动开销，5 分钟兜底 */
const SUITE_TIMEOUT_MS = 5 * 60 * 1000;

const results = [];
for (const name of SUITES) {
  const script = path.join(__dirname, name);
  const t0 = Date.now();
  let outcome = "passed";
  let error = null;
  if (!fs.existsSync(script)) {
    outcome = "error";
    error = "脚本不存在";
  } else {
    const r = spawnSync(process.execPath, [script], {
      cwd: path.resolve(__dirname, ".."),
      timeout: SUITE_TIMEOUT_MS,
      encoding: "utf8",
    });
    if (r.error) {
      outcome = "error";
      error = String(r.error.message).slice(-500);
    } else if (r.status !== 0) {
      outcome = "failed";
      error = ((r.stderr || "") + (r.stdout || "")).trim().slice(-500);
    }
  }
  results.push({ name, outcome, duration_ms: Date.now() - t0, error });
  console.log(`[e2e] ${name}: ${outcome} (${Date.now() - t0}ms)`);
}

const report = {
  suites: results,
  total: results.length,
  passed: results.filter((r) => r.outcome === "passed").length,
  generated_at: new Date().toISOString(),
};
const outPath = path.join(outDir, "e2e-report.json");
fs.writeFileSync(outPath, JSON.stringify(report, null, 2), "utf8");
console.log(`[e2e] 报告已写入 ${outPath}（${report.passed}/${report.total} 通过）`);
// 有套件失败时以非零退出码结束，便于上层感知（报告仍完整落盘）
process.exit(report.passed === report.total ? 0 : 1);
