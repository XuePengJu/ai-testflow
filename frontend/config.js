// 前端 API 基址配置
//
// 取值规则：
//   1. 本地访问（localhost / 127.0.0.1）→ 使用当前页面 origin 的绝对地址
//        · 后端同源伺服 / 与 /config.js，绝对地址可避开预览面板等特殊环境
//          对相对路径 /api 的解析问题（本地后端 CORS 为 *，跨源亦放行）
//   2. 其他（Vercel 等线上域名）→ 直连后端公网 HTTPS 地址（需后端 CORS 允许）
//   3. 留空（""）或删除本文件 → 使用相对路径 /api（兜底）
(function () {
  var h = location.hostname || "";
  if (h === "localhost" || h === "127.0.0.1" || h === "0.0.0.0" || h === "[::1]") {
    window.API_BASE = location.origin;           // http://localhost:8000
  } else {
    window.API_BASE = "https://api.clickscope.in"; // 线上：Cloudflare 隧道品牌域名
  }
})();
