// 前端 API 基址配置
//
// 取值规则：
//   1. 本地开发（localhost / 127.0.0.1）→ http://localhost:8000 (uvicorn dev)
//   2. 其他（Vercel 等线上域名）→ 直连后端公网 HTTPS 地址（需后端 CORS 允许）
//   3. 留空（""）或删除本文件 → 使用相对路径 /api（兜底）
(function () {
  var h = location.hostname || "";
  if (h === "localhost" || h === "127.0.0.1" || h === "0.0.0.0" || h === "[::1]") {
    // dev：后端 uvicorn 8000；CORS 默认 *
    window.API_BASE = "http://localhost:8000";
  } else {
    window.API_BASE = "https://api.clickscope.in"; // 线上：Cloudflare 隧道品牌域名
  }
})();
