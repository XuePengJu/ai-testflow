/**
 * 后端时间解析统一入口。
 *
 * 背景：后端 `utcnow()` 存的是**无时区标记的 UTC**（naive datetime），
 * JSON 序列化后形如 `"2026-09-19T18:05:53"`（不带 Z / 偏移量）。
 * JS 的 `new Date()` 对无时区字符串按**本地时区**解析，
 * GMT+8 环境下所有时间显示与「生成中」计时都会偏差 +8 小时。
 *
 * 规则：字符串末尾已带时区（Z 或 ±hh:mm）则原样解析，否则按 UTC 补 Z。
 */
export function parseServerTime(s?: string | null): Date | null {
  if (!s) return null;
  const iso = /(?:Z|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`;
  const d = new Date(iso);
  return isNaN(d.getTime()) ? null : d;
}
