# AI 测试工作流平台

> 作品集「门面担当」全栈产品，当前版本 **V2.4**。
> 一句话定位：把"规格 → AI 生成测试用例 → 质量校验 → 导出"做成一条**可编排、可观测的工作流**，配可视化前端。支持多厂商大模型、多级分类、思维导图预览、三级用户体系与流量分级加密。

在线演示：[ai.clickscope.in](https://ai.clickscope.in)

***

## 它解决什么

真实部署的 **DBERP 进销存系统**（服务器上已部署）没有 Swagger、也没有现成测试用例。
本平台以它为被测对象，把测试用例生成做成平台能力：

- 输入：接口规格（OpenAPI JSON）/ 业务需求（Markdown，支持图片引用）

- 输出：结构化测试用例（xlsx / json / xmind）

- 过程：四 Agent 编排，每步可观测、可重试、可定位错误

- 扩展：用户级模型配置、任务分类拖拽、思维导图在线评审

***

## 功能一览

### 核心工作流

- 四 Agent 编排：Parser → Generator → Reviewer → Exporter

- 任务状态机：pending → running → completed | failed

- 步骤日志：每步状态 / 耗时 / 输出摘要 / 错误详情

### 任务管理

- 任务列表（左右分栏：左分类树 / 右任务卡片）

- 多级分类树：新建 / 重命名 / 删除 / 拖拽移动（防环校验）

- 拖拽归类：任务拖入分类即关联，拖回「未分类」即移出

- 点击分类节点过滤，实时显示各节点任务数

### 任务详情（3 Tab）

- 🧠 **思维导图**：MindElixir 在线渲染，模块 → 用例层级，标签显示类型与优先级（P0/P1/P2）

- 📋 **测试用例**：按模块分组卡片，表格展示完整用例字段（前置 / 步骤 / 预期）

- ⚙️ **工作流步骤**：四步时间线，每步日志与质量报告

### 多格式导出

- **XLSX**：测试管理工具可导入

- **JSON**：自动化框架可用

- **XMind**：脑图评审，模块 → 用例层级，类型与优先级以标签呈现

### 多厂商大模型接入

- 7+ 厂商预设（OpenAI 兼容协议，HTTP 直连）：

  - 阿里百炼 / 智谱 GLM（含 Coding Plan 专用端点）/ 腾讯混元 / DeepSeek / Kimi / 豆包（火山方舟）/ 自定义

- **双槽位配置**：文本模型 + 视觉模型（多模态）

  - 只配文本模型：全流程用它，图片被忽略并提示

  - 双模型：含图片时视觉模型先解读 → 描述并入文本 → 文本模型生成用例

- **双层配置**：用户自定义配置 > 平台默认（admin 设）> 环境变量 > mock 兜底

- **连通测试**：一键验证 Key 有效性与延迟

- **Key 安全**：AES-256-GCM 加密落库，接口回显脱敏（`sk-****abcd`）

### 三级角色与数据隔离

| 角色            | 来源              | 数据保留               | 能力                           |
| ------------- | --------------- | ------------------ | ---------------------------- |
| **guest 访客**  | 按 IP 自动建临时身份    | **24h TTL**，到期级联清理 | 体验全功能，任务上限 10，可一键转正          |
| **user 注册用户** | 邮箱 + 密码（bcrypt） | 永久                 | 只管自己的任务 / 分类 / 模型配置 / 文件     |
| **admin 管理员** | 首个注册用户自动晋升      | 永久                 | 全量任务、用户治理、访客治理、平台统计、平台默认模型配置 |

### 分级流量加密（V2.1）

| 角色               | 流量形态                                            |
| ---------------- | ----------------------------------------------- |
| **admin**        | 全程**明文直通**（Swagger 调试 / 运维排查不受影响）               |
| **user / guest** | JSON 响应**强制加密**为 `{"enc": base64url密文}`，请求体同形加密 |

- 算法：AES-256-GCM（`nonce(12B)‖密文‖tag(16B)`），GCM 自带完整性校验

- 密钥分发：HKDF(JWT\_SECRET, 用户ID) 派生，登录/注册/访客签发时明文下发

- 前端纯 JS 实现，不依赖 `crypto.subtle`（HTTP 非安全上下文可用）

- 文件上传下载保持二进制流不加密

- 开关 `API_ENCRYPT=0` 可整体关闭（本地调试）

### 安全特性

- JWT（HS256，24h）+ 每请求回查 DB——禁用用户下一请求即生效

- 登录限速：同一用户连续失败 5 次锁 10 分钟

- 访客防滥用：单 IP 24h 最多 5 个访客身份，单访客最多 10 个任务

- 越权统一 404（防资源枚举）

- 访客清理：APScheduler 定时 + 懒清理 + 手动清理，删除动作写审计表

### 产品包装

- 右侧悬浮「需求进度」抽屉：已上线 / 开发中 / 规划中，三组进度展示

- 顶栏模型状态胶囊：实时显示当前生效模型与来源

- 隧道慢速提示条（Cloudflare 隧道访问时显示，可关闭记住）

***

## 技术栈

| 层     | 选型                                               |
| ----- | ------------------------------------------------ |
| 后端    | **FastAPI**（自带 Swagger）                          |
| 数据库   | **SQLite**（SQLAlchemy ORM，timeout=30 防并发写锁）      |
| 认证    | **bcrypt + JWT**（passlib / pyjwt）+ 每请求回查用户状态     |
| 加密    | **AES-256-GCM**（Python cryptography + 前端纯 JS 实现） |
| AI 模型 | OpenAI 兼容协议 HTTP 直连，支持 7+ 厂商；无 Key 自动 mock 兜底    |
| 前端    | 原生 HTML + JS（单文件，零构建，零依赖）                        |
| 思维导图  | **MindElixir**（120KB，可编辑，原生标签支持）                 |
| 任务调度  | APScheduler（访客清理定时任务）                            |
| 测试    | pytest（68 条自动化用例）                                |

***

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env        # 可选：填 DASHSCOPE_API_KEY 接真模型；留空走 mock
python scripts/migrate_v2.py  # 首次/升级时执行（幂等）：建用户表 + 预置 admin + 存量数据迁移
python main.py              # 等价于 uvicorn main:app --port 8000
```

首次访问**无需注册**：可直接「游客体验」，或注册账号（首个注册用户自动成为管理员）。

访问：

- 前端 Dashboard：<http://127.0.0.1:8000>

- 接口文档（Swagger）：<http://127.0.0.1:8000/docs>

***

## 演示流程（30 秒出成品）

1. 首页三选一：登录 / 注册 / 游客体验（游客 24h 内数据保留，可随时转正）
2. 选「接口规格(api)」或「业务需求(business)」，上传 DBERP 规格文件（`examples/` 下有现成样本）或粘贴文本
3. 选导出格式（xlsx / json / xmind），点「提交任务」
4. 任务列表实时刷新；点开任务看 **思维导图预览** / **测试用例表格** / **四步骤时间线**
5. 一键下载导出的测试用例文件

> 无模型 Key 时自动走 **mock 兜底**，无需联网即可演示完整编排流程。
> 配置真实模型：点顶栏模型状态胶囊 → 选厂商 → 填 Key → 测试连通 → 保存。

***

## REST API

任务类接口均需 `Authorization: Bearer <token>`（guest/user/admin 皆可）：

### 认证

| 方法   | 路径                          | 说明                |
| ---- | --------------------------- | ----------------- |
| POST | `/api/auth/register`        | 注册（首个用户自动成 admin） |
| POST | `/api/auth/login`           | 登录，返回 JWT + 加密密钥  |
| GET  | `/api/auth/me`              | 当前身份（guest 含剩余时长） |
| POST | `/api/auth/change-password` | 改密                |

### 访客

| 方法   | 路径                   | 说明                 |
| ---- | -------------------- | ------------------ |
| POST | `/api/guest/token`   | 按 IP 签发/复用访客 token |
| POST | `/api/guest/upgrade` | 访客转注册用户（数据迁移）      |

### 任务

| 方法   | 路径                              | 说明                                        |
| ---- | ------------------------------- | ----------------------------------------- |
| POST | `/api/tasks`                    | 提交任务：`file` 或 `text` + `kind` + `formats` |
| GET  | `/api/tasks`                    | 任务列表（admin 加 `?all=true` 看全部）             |
| GET  | `/api/tasks/{id}`               | 任务详情 + 四步骤日志 + 用例列表                       |
| GET  | `/api/tasks/{id}/download?fmt=` | 下载导出文件（xlsx/json/xmind）                   |

### 分类

| 方法     | 路径                                       | 说明                  |
| ------ | ---------------------------------------- | ------------------- |
| GET    | `/api/categories`                        | 获取分类树               |
| POST   | `/api/categories`                        | 新建分类（可选 parent\_id） |
| PATCH  | `/api/categories/{id}`                   | 重命名分类               |
| DELETE | `/api/categories/{id}`                   | 删除分类（子分类级联，任务回落未分类） |
| POST   | `/api/categories/{id}/move`              | 移动分类到新父节点（防环校验）     |
| POST   | `/api/tasks/{task_id}/category/{cat_id}` | 任务归入分类              |
| DELETE | `/api/tasks/{task_id}/category`          | 任务移出分类              |

### 模型配置

| 方法   | 路径                          | 说明                 |
| ---- | --------------------------- | ------------------ |
| GET  | `/api/llm/config`           | 获取当前用户配置（Key 脱敏）   |
| POST | `/api/llm/config`           | 保存用户配置（Key 加密落库）   |
| POST | `/api/llm/test`             | 测试连通（用已保存或传入的配置）   |
| GET  | `/api/llm/providers`        | 获取厂商预设列表           |
| GET  | `/api/llm/platform-default` | （admin）获取/设置平台默认配置 |
| PUT  | `/api/llm/platform-default` | （admin）设置平台默认配置    |

### 管理后台（admin）

| 方法     | 路径                             | 说明                     |
| ------ | ------------------------------ | ---------------------- |
| GET    | `/api/users`                   | 用户/访客列表                |
| PATCH  | `/api/users/{id}`              | 启用/禁用用户                |
| DELETE | `/api/users/{id}`              | 删除并级联清理                |
| POST   | `/api/admin/guests/clean`      | 手动清理过期访客               |
| POST   | `/api/admin/guests/clean-all`  | 清空全部访客                 |
| POST   | `/api/admin/guests/{id}/clean` | 定向清理单个访客               |
| GET    | `/api/admin/stats`             | 注册用户/活跃访客/24h 清理数/任务总数 |

### 其他

| 方法  | 路径           | 说明           |
| --- | ------------ | ------------ |
| GET | `/config.js` | 前端配置（API 基址） |
| GET | `/health`    | 健康检查         |

***

## 关于用例生成核心库

本仓库已**内置** `generator_core/`（整合自 CLI 原型 `ai-testcase-generator` 的解析 / 生成 / 导出逻辑），
不再依赖外部项目，**clone 即跑**。平台层在其上叠加：任务调度状态机、四 Agent 编排、
步骤可观测日志、REST API 与可视化前端。`generator_core/` 内部采用 mock 兜底，无模型 Key 也能生成用例。

***

## 目录结构

```
ai-testflow/
├── main.py                      # 入口（挂载 API + 静态页 + 启动检查 + 调度器）
├── requirements.txt
├── .env.example                 # 环境变量示例
├── generator_core/              # 内置用例生成核心（config/ + src/，自包含）
│   ├── src/
│   │   ├── parser/              # 规格解析（API / business）
│   │   ├── generator/           # 用例生成（LLM + mock 兜底）
│   │   ├── reviewer/            # 质量评审
│   │   └── exporter/            # 多格式导出（xlsx/json/xmind）
│   └── config/
├── examples/                    # DBERP 接口规格 / 业务需求样本
├── frontend/                    # 前端（原生 HTML+JS，单文件零构建）
│   ├── index.html               # 主页面
│   ├── config.js                # 前端配置（API_BASE）
│   └── vendor/                  # 第三方库（mind-elixir 等）
│       └── mind-elixir/
├── scripts/
│   └── migrate_v2.py            # 幂等迁移：建用户表 + 预置 admin + 存量任务归属
├── tests/                       # 68 条自动化用例（认证 + 分级加密 + 权限，pytest）
├── app/
│   ├── core/
│   │   ├── config.py            # 配置加载
│   │   ├── db.py                # 数据库连接
│   │   ├── security.py          # bcrypt + JWT
│   │   ├── crypto.py            # AES-256-GCM 加解密
│   │   ├── providers.py         # LLM 厂商预设
│   │   └── middleware.py        # 分级加密中间件
│   ├── models/                  # SQLAlchemy 模型
│   │   ├── task.py              # Task / StepLog
│   │   ├── user.py              # User / GuestCreationLog / CleanLog
│   │   └── category.py          # Category（多级分类树）
│   ├── schemas/                 # Pydantic 请求/响应
│   │   ├── task.py
│   │   ├── user.py
│   │   └── category.py
│   ├── services/
│   │   ├── pipeline_lib.py      # 调用内置 generator_core
│   │   └── llm_service.py       # OpenAI 兼容 LLM 客户端
│   ├── workflow/
│   │   ├── engine.py            # 状态机 + 步骤调度
│   │   └── agents/              # 四 Agent（parser/generator/reviewer/exporter）
│   ├── api/                     # API 路由
│   │   ├── auth.py
│   │   ├── guest.py
│   │   ├── tasks.py
│   │   ├── categories.py
│   │   ├── llm_config.py
│   │   ├── users.py             # admin 用户管理
│   │   └── deps.py              # 鉴权依赖
│   └── jobs/
│       └── guest_cleaner.py     # 访客清理（定时 + 懒清理 + 审计）
├── uploads/  outputs/           # 上传 / 导出目录（按用户分目录，已 gitignore）
└── docs/                        # 项目文档
    ├── PRD.md                   # 产品需求文档
    └── 项目1-工作流平台-MVP执行方案.md  # 技术实现方案
```

***

## 部署架构

- **前端**：Vercel 静态托管，品牌域名 [ai.clickscope.in](https://ai.clickscope.in)

- **后端**：阿里云服务器（39.106.200.147），systemd 管理 uvicorn，经 Cloudflare 命名隧道暴露

- **API 域名**：[api.clickscope.in](https://api.clickscope.in)（Cloudflare 代理 + 命名隧道）

- **被测系统**：DBERP 进销存，[erp.clickscope.in](https://erp.clickscope.in)

详见 `DEPLOY.md` 与 `deploy/cloudflared-setup.md`。

***

## 后续演进

| 版本       | 内容                              | 状态    |
| -------- | ------------------------------- | ----- |
| V2.5（规划） | React + TS 前端重写（当前为原生 HTML 过渡版） | 📋 规划 |
| V2.6（规划） | 定时执行 + Allure 报告集成              | 📋 规划 |
| V2.7（规划） | 接真实 DBERP 后端做端到端接口自动化闭环         | 📋 规划 |

