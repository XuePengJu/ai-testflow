// 前端 API 基址配置（Vercel 部署时由本文件决定后端地址）
//
// 取值规则：
//   1. 留空（""）或删除本文件        → 使用相对路径 /api
//        · 后端同源部署（直接访问后端）时可用
//        · Vercel 用 vercel.json rewrite 把 /api 代理到后端时也可用
//   2. 设为后端公网 HTTPS 地址         → 前端直连后端（需后端 CORS 允许本 Vercel 域名）
//        例：window.API_BASE = "https://ai-testflow-api.your-domain.com";
//
// 推荐（免备案方案）：把下面改成 Cloudflare 隧道给你的后端 HTTPS 地址。
window.API_BASE = "";
