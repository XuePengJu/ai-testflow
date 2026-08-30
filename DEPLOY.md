# 部署指南（前后端分离）

架构：
- **前端**：`frontend/`（原生 HTML+JS）→ 部署到 **Vercel**，走海外边缘，免备案，自带 HTTPS。
- **后端**：FastAPI（uvicorn）→ 跑在**阿里云服务器**，经 **Cloudflare 隧道**暴露为 HTTPS 子域名，免备案。
- 前端通过 `frontend/config.js` 里的 `window.API_BASE` 直连后端；后端用 `CORS_ORIGINS` 放行前端域名。

```
浏览器 ──HTTPS──> Vercel 前端(vercel.app / 自定义域名)
                     │  fetch  /api/*
                     └─HTTPS─> Cloudflare 隧道 ──> 阿里云:8000 (uvicorn)
```

---

## 当前状态（2026-08-30 已落地）

- 前端：Vercel 部署 `ai-testflow`，默认域 `ai-testflow.vercel.app`，品牌域 `ai.clickscope.in`（Cloudflare 灰云指 `cname.vercel-dns.com`）。
- 后端：已上线阿里云并 `active`（`systemctl is-active ai-testflow` → active）。
- 后端经 **Cloudflare 命名隧道** `ai-testflow` 暴露为稳定品牌 HTTPS 子域名：
  `https://api.clickscope.in`
  - 隧道配置文件 `/root/.cloudflared/config.yml`（ingress: `api.clickscope.in` → `http://localhost:8000`）。
  - systemd 服务 `cloudflared` 常驻（`enable` 并 `active`），重启不变地址。
  - 验证：`/health` → `{"status":"ok"}`，`/docs` → 200。
- 前端 `config.js` 的 `window.API_BASE = "https://api.clickscope.in"`，后端 `CORS_ORIGINS = https://ai.clickscope.in,https://ai-testflow.vercel.app`。
- 临时的快速隧道（`rom-realtors-banner-dom.trycloudflare.com`）已停用。

---

## 一、前端 → Vercel

1. 把 `frontend/config.js` 的 `window.API_BASE` 改成后端 HTTPS 地址：
   ```js
   window.API_BASE = "https://api.your-domain.com";   // Cloudflare 隧道给的地址
   ```
   若改用 Vercel rewrite 代理（见下「备选」），则保持 `""` 即可。
2. 在 Vercel 导入本仓库，构建配置：
   - Framework Preset：**Other**
   - Build Command：**留空**
   - Output Directory：**`frontend`**
   （仓库根 `vercel.json` 已写好这两项，一般自动识别）
3. 部署后获得 `https://<project>.vercel.app`，可绑定自定义域名（同样免备案）。

---

## 二、后端 → 阿里云服务器

SSH 登录服务器（root）：

```bash
ssh -i 阿里密钥.pem root@39.106.200.147
```

1. 拉代码
   ```bash
   cd /root && git clone https://github.com/XuePengJu/ai-testflow.git && cd ai-testflow
   ```
2. 装依赖（用宝塔已装的 Python 3.13.14）
   ```bash
   /www/server/pyporject_evn/versions/3.13.14/bin/python -m venv venv
   venv/bin/pip install -r requirements.txt
   ```
3. 写生产环境变量 `.env`（参考 `.env.example`）
   ```ini
   ENV=production
   JWT_SECRET=<一段足够长的随机串>
   CORS_ORIGINS=https://<你的vercel域名>,https://你的自定义域名
   DASHSCOPE_API_KEY=<阿里百炼Key，可选，留空走 mock>
   ```
   > 生成随机密钥：`openssl rand -hex 32`
4. 常驻运行：复制 `deploy/ai-testflow.service` 到 `/etc/systemd/system/` 并启用（见文件内注释）。
5. 暴露公网 HTTPS：按 `deploy/cloudflared-setup.md` 建 Cloudflare 隧道，把
   `api.your-domain.com` 指向 `127.0.0.1:8000`。
6. 验证：`curl https://api.your-domain.com/health` → `{"status":"ok"}`

---

## 三、配置对照表

| 位置 | 改什么 | 填什么 |
|---|---|---|
| `frontend/config.js` | `window.API_BASE` | 后端 HTTPS 地址（`https://api.your-domain.com`） |
| 后端 `.env` | `CORS_ORIGINS` | 前端域名（Vercel 域名，逗号分隔） |
| 后端 `.env` | `ENV` / `JWT_SECRET` | `production` / 强随机串 |
| Vercel 项目 | Output Directory | `frontend` |

---

## 备选：Vercel rewrite 代理（前端不必直连后端）

不改 `config.js`（`API_BASE` 留 `""`，走相对 `/api`），在仓库根加：
```json
// vercel.json 追加
"rewrites": [{ "source": "/api/(.*)", "destination": "https://api.your-domain.com/api/$1" }]
```
优点：同源、无需 CORS、后端地址只在一处维护。缺点：`destination` 需写死后端地址，
且 Vercel 对静态 rewrite 的上游协议以 HTTPS 为准（所以后端仍需 HTTPS，即仍需隧道）。
