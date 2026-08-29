# AI 测试工作流平台（MVP）

> 作品集「门面担当」全栈产品 **第一版**：后端 + Agent 编排优先。
> 一句话定位：把"规格 → AI 生成测试用例 → 质量校验 → 导出"做成一条**可编排、可观测的工作流**，配可视化前端。

---

## 它解决什么

真实部署的 **DBERP 进销存系统**（服务器上已部署）没有 Swagger、也没有现成测试用例。
本平台以它为被测对象，把测试用例生成做成平台能力：

- 输入：接口规格（OpenAPI JSON）/ 业务需求（Markdown）
- 输出：结构化测试用例（xlsx / json / xmind）
- 过程：四 Agent 编排，每步可观测、可重试、可定位错误

---

## 架构：四 Agent 编排工作流

```
提交规格 ──▶ [ParserAgent] ──▶ [GeneratorAgent] ──▶ [ReviewerAgent] ──▶ [ExporterAgent] ──▶ 完成
             解析规格            AI 生成用例            质量门禁              多格式导出
                │                   │                     │                    │
                └───────────────────┴─────────────────────┴────────────────────┘
                          每一步写入 StepLog（状态 / 耗时 / 输出摘要 / 错误）
```

| Agent | 职责 | 产出 |
|-------|------|------|
| **ParserAgent** | 解析规格 → 测试单元 | `ApiEndpoint[]` / `RequirementUnit[]` |
| **GeneratorAgent** | 按策略调模型生成用例 | `TestCase[]`（无 Key 自动 mock 兜底） |
| **ReviewerAgent** | 质量校验 + 门禁 | 覆盖率 / 异常占比 / 结构校验报告 |
| **ExporterAgent** | 导出多格式 | xlsx / json / xmind |

任务状态机：`pending → running → completed | failed`（任一步失败 → 整体 failed，记录错误步骤）。

---

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | **FastAPI**（自带 Swagger） |
| 数据库 | **SQLite**（SQLAlchemy ORM，timeout=30 防并发写锁） |
| 认证 | **bcrypt + JWT**（passlib / pyjwt）+ APScheduler 定时清理 |
| AI 模型 | **阿里百炼 / 通义千问**（无 Key 自动 mock 兜底） |
| 前端（MVP） | 原生 HTML + JS（登录/注册/游客三入口 + 任务列表 / 四步骤时间线 / 下载） |
| 用例生成核心 | 内置 `generator_core/`（整合自 CLI 原型，自包含、clone 即跑） |

---

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env        # 可选：填 DASHSCOPE_API_KEY 接真模型；留空走 mock
python scripts/migrate_v2.py  # 首次/升级时执行（幂等）：建用户表 + 预置 admin + 存量数据迁移
python main.py              # 等价于 uvicorn main:app --port 8000
```

首次访问**无需注册**：可直接「游客体验」，或注册账号（首个注册用户自动成为管理员）。

访问：
- 前端 Dashboard：http://127.0.0.1:8000
- 接口文档（Swagger）：http://127.0.0.1:8000/docs

---

## 认证与多用户（V2）

三级角色 + 访客生命周期治理，完整设计与落地代码见方案文档第 9 节：

| 角色 | 身份来源 | 数据保留 | 能力 |
|------|---------|---------|------|
| **guest 访客** | 按 IP 自动建临时身份（ip_hash + seq 防撞唯一约束） | **24h TTL**，到期连任务/文件/目录一起清 | 体验全功能，任务上限 10，可一键转正 |
| **user 注册用户** | 邮箱 + 密码（bcrypt） | 永久 | 只管自己的任务与文件 |
| **admin 管理员** | 首个注册用户 / `ADMIN_BOOTSTRAP` 预置 | 永久 | 看全部任务、用户启停/删除、访客治理、统计看板 |

关键实现（面试可讲点）：

- **JWT + 每请求回查 DB**：token 无状态签发（HS256，24h），但 `get_current_user` 每次回查库——admin 禁用用户**下一请求即生效**，不用等 token 过期；过期 guest 访问时顺手懒清理
- **防滥用计数独立成表**（`guest_creation_log`）：只追加不随 guest 删除，单 IP 24h 超 5 个访客直接 429——计数若挂在 guest 记录上会被自己的清理逻辑删掉
- **访客清理**：APScheduler 每小时扫 + 启动兜底 + 过期即拦，删除动作写 `clean_log` 审计表；越权访问统一 404 防枚举
- **数据隔离**：上传/导出按用户 `data_dir` 分目录，访客临时目录随 TTL 整目录删除

### API 分级加密（V2.1）

HTTP 明文传输下抓包可直接看到响应数据结构，分级加密防"抓包抄接口"：

| 角色 | 流量形态 |
|------|---------|
| **admin** | 全程**明文直通**（Swagger 调试 / 运维排查不受影响） |
| **user / guest** | JSON 响应**强制加密**为 `{"enc": base64url密文}`，请求体同形加密；抓包只看到密文 |

- **算法**：AES-256-GCM（`nonce(12B)‖密文‖tag(16B)`），GCM 自带完整性校验，篡改即解密失败
- **密钥分发**：密钥不存数据库，登录/注册/访客签发时由 **HKDF(JWT_SECRET, 用户ID)** 派生下发——免迁移、访客转正不换密钥、多设备一致
- **前端纯 JS 实现**：不依赖 `crypto.subtle`（HTTP+IP 非安全上下文下浏览器禁用），NIST 标准向量验证 + 与后端 Python `cryptography` 双向互通
- **明文通道**：login / register / guest/token / upgrade（密钥分发环节）；文件上传下载保持二进制流不加密
- 开关 `API_ENCRYPT=0` 可整体关闭（本地调试）

### 测试设计（68 条自动化用例，`pytest tests/`）

| 模块 | 覆盖 |
|------|------|
| 注册/登录 | 首用户晋升 admin、弱密码/重复用户名/保留域名邮箱拒绝、登录限速 5 次锁 10 分钟、改密后旧密码失效 |
| Token | 缺失/格式错误/过期/篡改签名/伪造他人 → 401 |
| 访客生命周期 | 同 IP 复用不发散、任务上限 10 返回 429、过期懒清理、单 IP 第 6 个访客 429、转正数据完整迁移 |
| 清理任务 | 级联删任务/日志/目录、未到期不误删、手动清理接口、禁用访客立即清数据 |
| 角色权限矩阵 | user/guest 访问 admin 接口 403、越权访问他人任务 404、admin 自删被拒 |
| 分级加密 | admin 明文直通、user/guest 响应密文、密文往返还原、加密请求体透明解密、错误密钥 400、篡改密文拒绝、密钥分发通道明文、转正密钥不变 |

---

## 演示流程（30 秒出成品）

1. 首页三选一：登录 / 注册 / 游客体验（游客 24h 内数据保留，可随时转正）
2. 选「接口规格(api)」或「业务需求(business)」，上传 DBERP 规格文件（`examples/` 下有现成样本）或粘贴文本
3. 选导出格式（xlsx / json / xmind），点「提交任务」
4. 任务列表实时刷新；点开看 **四 Agent 步骤时间线** 与 **质量报告**
5. 一键下载导出的测试用例文件

> 无百炼 Key 时自动走 **mock 兜底**，无需联网即可演示完整编排流程。

---

## REST API

任务类接口均需 `Authorization: Bearer <token>`（guest/user/admin 皆可）：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册（首个用户自动成 admin） |
| POST | `/api/auth/login` | 登录，返回 JWT |
| GET | `/api/auth/me` | 当前身份（guest 含剩余时长） |
| POST | `/api/auth/change-password` | 改密 |
| POST | `/api/guest/token` | 按 IP 签发/复用访客 token |
| POST | `/api/guest/upgrade` | 访客转注册用户（数据迁移） |
| POST | `/api/tasks` | 提交任务：`file` 或 `text` + `kind` + `formats` |
| GET | `/api/tasks` | 任务列表（admin 加 `?all=true` 看全部） |
| GET | `/api/tasks/{id}` | 任务详情 + 四步骤日志 |
| GET | `/api/tasks/{id}/download?fmt=` | 下载导出文件（xlsx/json/xmind） |
| GET | `/api/users` | （admin）用户/访客列表 |
| PATCH | `/api/users/{id}` | （admin）启用/禁用 |
| DELETE | `/api/users/{id}` | （admin）删除并级联清理 |
| POST | `/api/admin/guests/clean` | （admin）手动清理过期访客 |
| GET | `/api/admin/stats` | （admin）注册用户/活跃访客/24h 清理数 |
| GET | `/health` | 健康检查 |

---

## 关于用例生成核心库

本仓库已**内置** `generator_core/`（整合自 CLI 原型 `ai-testcase-generator` 的解析 / 生成 / 导出逻辑），
不再依赖外部项目，**clone 即跑**。平台层在其上叠加：任务调度状态机、四 Agent 编排、
步骤可观测日志、REST API 与可视化前端。`generator_core/` 内部采用 mock 兜底，无百炼 Key 也能生成用例。

---

## 目录结构

```
ai-testflow/
├── main.py                  # 入口（挂载 API + 静态页 + 启动检查 + 调度器）
├── requirements.txt
├── generator_core/          # 内置用例生成核心（config/ + src/，自包含）
├── examples/                # DBERP 接口规格 / 业务需求样本
├── scripts/
│   └── migrate_v2.py        # 幂等迁移：建用户表 + 预置 admin + 存量任务归属
├── tests/                   # 68 条自动化用例（认证 + 分级加密，pytest）
├── app/
│   ├── core/                # config / db / security(bcrypt+JWT) / crypto(AES-GCM) / middleware(分级加密) / utils
│   ├── models/              # Task + StepLog / User + GuestCreationLog + CleanLog
│   ├── schemas/task.py      # Pydantic 响应
│   ├── services/
│   │   └── pipeline_lib.py  # 调用内置 generator_core 解析/生成/导出
│   ├── workflow/
│   │   ├── engine.py        # 状态机 + 步骤调度（文件按 data_dir 隔离）
│   │   └── agents/          # 四 Agent（parser/generator/reviewer/exporter）
│   ├── api/                 # auth / guest / users(admin) / tasks + deps(鉴权)
│   ├── jobs/
│   │   └── guest_cleaner.py # 访客清理（定时 + 懒清理 + 审计）
│   └── static/index.html    # 轻量前端（登录/注册/游客三入口）
├── uploads/  outputs/       # 上传 / 导出目录（按用户分目录，已 gitignore）
└── docs/                    # 项目文档（需求 + 技术方案）
    ├── PRD.md               # 产品需求文档
    └── 项目1-工作流平台-MVP执行方案.md  # 技术实现方案
```

---

## 后续演进（非 MVP）

- 完整 **React + TS** 前端 + 质量看板（覆盖率 / 异常占比可视化）
- admin 管理后台页面（当前仅有 API + Swagger）
- 定时执行用例 + **Allure 报告**
- 接真实 DBERP 后端做**端到端接口自动化闭环**
- 部署到阿里云在线演示（前端阶段需装 Node.js）
