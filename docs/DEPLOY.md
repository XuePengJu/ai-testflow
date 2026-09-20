# 部署指南（同源单服务 + cloudflared 隧道）

> 更新：2026-09-20。适用版本 **V4.5.1**（V4.0 RAG 知识库 / V4.1 问答合一 + 访客共享账号 + 演示模式开关 / V4.2 Embedding 槽 / V4.3–V4.5.1 UI）。
>
> 注：V4.1 起访客体系简化为「全站唯一共享账号」（无 24h TTL、无自动清理调度），admin 手动清空共享数据；部署架构与脚本不受影响。
> 历史架构（Vercel 前后端分离 / cpolar 内网穿透）均已废弃，仅在第 5 节保留说明。

架构：

- **同源单服务**：FastAPI（uvicorn，127.0.0.1:8000）同时提供 REST API（`/api`）与前端静态页（`frontend/dist`）；
  React 版走相对路径调用，天然同源，无需跨域配置。
- **服务器**：阿里云 ECS（IP 与数据库端口见运维记录，**不写入公开文档**），宝塔面板运维，代码目录 `/root/ai-testflow`。
- **数据库**：MySQL（同一服务器的 3356 端口远程库），本地开发可降级 SQLite（`app/core/db.py` 双方言）。
- **公网访问**：**cloudflared 命名隧道**（`ai.clickscope.in` → `http://localhost:8000`）。
  因域名未完成 ICP 备案，服务器不能直开 80/443，隧道为当前最稳方案。
- **被测系统**：DBERP 进销存部署于同一服务器，经另一条隧道（`erp.clickscope.in`）访问。

```
浏览器 ──HTTPS──> cloudflared 隧道 ──> 阿里云:8000 (uvicorn)
                                        ├── /api/*   REST API
                                        ├── /health  健康检查
                                        └── /        前端静态页(frontend/dist)
浏览器 ──HTTPS──> erp.clickscope.in ──> nginx (/www/wwwroot/dberp, PHP 8.2)
```

---

## 一、SSH 登录

服务器 root 使用**专用密钥**（不是 GitHub 那把 `id_ed25519`）：

```bash
ssh -i <服务器密钥.pem> -o IdentitiesOnly=yes root@<服务器IP>
```

> ❌ 用 `~/.ssh/id_ed25519` 连会 `Permission denied (publickey)` —— 该公钥未在服务器授权。

---

## 二、首次部署（已完成的步骤，备查）

1. 取代码：

   ```bash
   cd /root && git clone <仓库地址> ai-testflow && cd ai-testflow
   ```

2. 装依赖：

   ```bash
   /www/server/pyporject_evn/versions/3.13.14/bin/python -m venv venv
   venv/bin/pip install -r requirements.txt
   ```

3. 写生产环境变量 `.env`（参考 `.env.example`）：

   ```ini
   ENV=production
   JWT_SECRET=<一段足够长的随机串>      # openssl rand -hex 32
   DATABASE_URL=mysql+pymysql://user:pass@host:3356/ai-testflow
   DASHSCOPE_API_KEY=<阿里百炼Key，可选，留空走 mock>
   ```

4. 常驻运行：复制 `deploy/ai-testflow.service` 到 `/etc/systemd/system/`：

   ```bash
   cp deploy/ai-testflow.service /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now ai-testflow
   ```

   单元要点：`WorkingDirectory=/root/ai-testflow`，`ExecStart=/root/ai-testflow/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000`，`Restart=on-failure`。

5. 隧道：`/etc/cloudflared/config.yml` 配 ingress（`ai.clickscope.in` → `http://localhost:8000`），`systemctl enable --now cloudflared`。

6. 验证：`curl https://ai.clickscope.in/health` → `{"status":"ok","db_dialect":"mysql"}`

---

## 三、日常更新（推荐：bundle 上传）

> **服务器上 `git pull` 拉不动**：github.com 主站从阿里云超时（`codeload` 通但 git 走主站）。
> 用 **git bundle 传差分包**最稳，实测 528K / 几秒完成。

本地（开发机）：

```bash
cd ~/Documents/软件测试示例项目/ai-testflow
git push git@github.com:XuePengJu/ai-testflow.git main   # 沙箱内 https 推送会被掐，走 SSH
# 打差分包：<服务器当前 commit>..main
git bundle create /tmp/aitf.bundle <server-commit>..main
scp -i <服务器密钥.pem> /tmp/aitf.bundle root@<服务器IP>:/tmp/
```

服务器：

```bash
cd /root/ai-testflow
git bundle verify /tmp/aitf.bundle          # 确认基线 commit 匹配
git pull /tmp/aitf.bundle main              # 应用增量
kill <旧的手工 uvicorn pid> 2>/dev/null     # 若存在手工起的进程，先杀，避免端口冲突
systemctl restart ai-testflow
curl -s http://127.0.0.1:8000/health        # → {"status":"ok","db_dialect":"mysql"}
```

验证外网：

```bash
curl -sk https://ai.clickscope.in/ | grep -oE 'index-[A-Za-z0-9_-]+\.(js|css)'
```

> 前端 `dist/` 已入库，服务器侧**不需要 Node / 不需要 build**。

---

## 四、排障速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `systemctl restart` 后端口没起来 | 线上长期有**手工起的 uvicorn** 占着 8000，systemd 启动失败 | `ps aux | grep uvicorn` 找到 pid 杀掉，再 `systemctl restart ai-testflow` |
| 浏览器 `ERR_CONNECTION_CLOSED` | Cloudflare 分配 IP 在国内部分网络/代理节点被干扰 | 服务端若 `/health` 正常，说明是客户端链路问题：给 Clash 加 `DOMAIN-SUFFIX,clickscope.in,DIRECT` 或换节点 |
| 页面白屏、控制台资源 404 | 浏览器缓存了旧 `index.html`，去请求已被新构建删除的旧 hash 文件 | 硬刷新（Cmd/Ctrl+Shift+R）或无痕窗口 |
| `/api/health` 返回 404 | 健康检查端点是 **`/health`**，没有 `/api` 前缀 | 用 `/health` |
| `api.clickscope.in` 不通 | 隧道 ingress 只配了 `ai.clickscope.in` 与 `erp.clickscope.in` | 前端走同源 `/api`，无需该域名 |
| 本地起 8000 后进程消失 | 沙箱里 `nohup ... &` 会随命令结束被回收 | 用后台常驻方式启动，或 `scripts/start_local.sh restart` |

---

## 五、配置对照表

| 位置 | 改什么 | 填什么 |
|---|---|---|
| 后端 `.env` | `ENV` / `JWT_SECRET` | `production` / 强随机串 |
| 后端 `.env` | `DATABASE_URL` | MySQL 连接串（留空则本地 SQLite） |
| 后端 `.env` | `DASHSCOPE_API_KEY` | 阿里百炼 Key，留空走 mock |
| systemd | `ai-testflow.service` | ExecStart 指向 venv 内 uvicorn，8000 端口 |
| cloudflared | `/etc/cloudflared/config.yml` | `ai.clickscope.in` / `erp.clickscope.in` 两条 ingress |

---

## 六、历史架构（已废弃）

- **2026-08-30 ~ 09-08**：前后端分离（Vercel 前端 + Cloudflare 隧道后端），因备案与链路复杂度废弃。
- **2026-09-08 ~ 09-14**：cpolar 内网穿透（web/erp 两条隧道），因域名不稳定、且部署文档里的 `gh-proxy` 拉代码方案不可靠而废弃。
- **2026-09-15 起**：cloudflared 命名隧道 + systemd 托管 uvicorn（本文所述）。
