# 后端免备案 HTTPS 暴露：Cloudflare Tunnel

Vercel 前端是强制 HTTPS 的，直连 `http://IP:8000` 会被浏览器「混合内容」拦截。
最干净的免备案方案：后端经 Cloudflare 隧道拿到海外边缘的 HTTPS 子域名。

## 前提
- 一个域名（任意注册商均可），其 DNS 交给 Cloudflare 托管（NS 改到 Cloudflare）。
- 不需要 ICP 备案：因为对外服务的边缘是 Cloudflare 海外节点，阿里云服务器只是隧道 origin。

## 步骤（在阿里云服务器上执行）
1. 安装 cloudflared
   ```bash
   # Alibaba Cloud Linux / CentOS 系
   wget -O cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
   chmod +x cloudflared && mv cloudflared /usr/local/bin/
   ```
2. 登录并授权（浏览器打开输出的链接，登录 Cloudflare）
   ```bash
   cloudflared tunnel login
   ```
3. 建隧道（名字随意）
   ```bash
   cloudflared tunnel create ai-testflow
   ```
4. 把子域名指向隧道（假设后端域名 api.your-domain.com）
   ```bash
   cloudflared tunnel route dns ai-testflow api.your-domain.com
   ```
5. 写隧道配置 `~/.cloudflared/config.yml`
   ```yaml
   tunnel: ai-testflow
   credentials-file: /root/.cloudflared/<uuid>.json
   ingress:
     - hostname: api.your-domain.com
       service: http://127.0.0.1:8000
     - service: http_status:404
   ```
6. 以系统服务常驻
   ```bash
   cloudflared service install
   sudo systemctl enable --now cloudflared
   ```

完成后 `https://api.your-domain.com/health` 应返回 `{"status":"ok"}`，
把这个地址填进前端 `frontend/config.js` 的 `window.API_BASE`，并在后端 `.env` 设
`CORS_ORIGINS=https://你的vercel域名`。

> 注：Cloudflare 免费隧道对国内访问速度一般，作品集演示足够；若追求速度再考虑备案子域名。
