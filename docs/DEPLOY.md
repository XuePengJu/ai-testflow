# 部署指南（同源单服务架构）

> 更新：2026-09-12。旧版前后端分离架构（Vercel 前端 + Cloudflare 隧道后端）已于 2026-09-08 废弃，
> 仓库根 `vercel.json` 仅作历史回滚保留，不再用于部署。

架构：

- **同源单服务**：FastAPI（uvicorn，8000 端口）同时提供 REST API（`/api`）与前端静态页；
  前端 `config.js` 的 `API_BASE=""` 走相对路径，天然同源，无需跨域配置。
- **服务器**：阿里云 ECS，宝塔面板运维（Python 3.13.14）。
- **公网访问**：cpolar 内网穿透隧道（web 隧道 → 本平台入口）。
- **被测系统**：DBERP 进销存部署于同一服务器，经 cpolar erp 隧道访问。

```
浏览器 ──HTTPS──> cpolar web 隧道 ──> 阿里云:8000 (uvicorn)
                                        ├── /api/*  REST API
                                        └── /       前端静态页(frontend/)
浏览器 ──HTTPS──> cpolar erp 隧道 ────> nginx 默认站点(/www/wwwroot/dberp/public, PHP 8.2)
```

---

## 一、首次部署

SSH 登录服务器（root）：

```bash
ssh -i 阿里密钥.pem root@<服务器IP>   # IP 见运维记录，不写入公开文档
```

1. 拉代码（服务器直连 GitHub 失败，必须走 gh-proxy）

   ```bash
   cd /root && git clone https://gh-proxy.com/https://github.com/XuePengJu/ai-testflow.git && cd ai-testflow
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
   DASHSCOPE_API_KEY=<阿里百炼Key，可选，留空走 mock>
   ```

   > 生成随机密钥：`openssl rand -hex 32`

4. 常驻运行：复制 `deploy/ai-testflow.service` 到 `/etc/systemd/system/` 并启用（见文件内注释）。

5. cpolar 隧道：配置 `/usr/local/etc/cpolar/cpolar.yml`（web 隧道 → `http://localhost:8000`）。
   **保留 tunnel id**，重启后域名不变。

6. 验证：`curl https://<web隧道域名>/health` → `{"status":"ok"}`

---

## 二、日常更新（已验证流程）

```bash
cd /root/ai-testflow
git pull --ff-only https://gh-proxy.com/https://github.com/XuePengJu/ai-testflow.git main
systemctl restart ai-testflow
curl http://127.0.0.1:8000/health
```

> 直连 GitHub 会失败，pull 必须走 gh-proxy；`--ff-only` 防止服务器产生意外合并提交。

---

## 三、配置对照表

| 位置 | 改什么 | 填什么 |
|---|---|---|
| `frontend/config.js` | `window.API_BASE` | `""`（相对路径，同源） |
| 后端 `.env` | `ENV` / `JWT_SECRET` | `production` / 强随机串 |
| 后端 `.env` | `CORS_ORIGINS` | 缺省 `*`（同源部署无需收紧） |
| systemd | `ai-testflow.service` | ExecStart 指向 venv 内 uvicorn，8000 端口 |
| cpolar | `cpolar.yml` | web / erp 两条隧道，保留 tunnel id |

---

## 四、历史架构说明（已废弃）

2026-08-30 ~ 2026-09-08 期间采用前后端分离：前端 Vercel（`ai.clickscope.in`）+ 后端 Cloudflare 命名隧道（`api.clickscope.in`）。
因域名备案问题与链路复杂度废弃，cloudflared 已从服务器移除（配置备份于服务器 `/root/.cloudflared/config.yml.bak`），
`vercel.json` 留作回滚。当前架构下部署不依赖 Vercel / Cloudflare。
