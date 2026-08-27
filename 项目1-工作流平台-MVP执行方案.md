# 项目1 · AI 测试工作流平台（MVP 执行方案）

> 定位：作品集「门面担当」全栈产品的**第一版**。原规划描述——接口管理 + AI 自动生成用例 + 定时执行 + 报告 + 质量看板，AI Agent 编排测试流程，在线可演示。
> 本方案锁定 **后端 + Agent 编排优先** 形态，前端完整版（React）后补。

---

## 0. 与已有 CLI 版的关系

`ai-testcase-generator/`（DBERP 实战版 CLI）是**已验证的原型**：解析、生成、导出逻辑、DBERP 接口/业务素材都已跑通。

本平台 = 把原型**平台化 / 生产化**：
- 复用：解析器、用例模型、生成策略、导出器、DBERP 素材、mock 兜底逻辑
- 新增：任务调度状态机、多 Agent 编排、可观测日志、REST API、轻量前端

CLI 版继续保留作为「命令行快速生成」入口；平台是「可视化编排 + 任务管理」入口。

---

## 1. 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 后端框架 | **FastAPI** | 异步、自带 Swagger、Python AI 生态友好 |
| AI 模型 | **阿里百炼 / 通义千问** | `DASHSCOPE_API_KEY`；无 Key 时 mock 兜底 |
| 数据库 | **SQLite** | 轻量，存任务/步骤日志/用例；后续可换 MySQL |
| 任务编排 | 自研状态机 + 步骤调度 | 四 Agent 串联，每步可观测、可重试 |
| 前端（MVP） | 原生 HTML+JS | 任务列表/详情/下载；完整 React 版后补 |
| 部署 | 本地跑通 → 阿里云 39.106.200.147 | 后端纯 Python，无需 Node；前端后补时需装 Node |

---

## 2. 架构：四 Agent 编排工作流

```
提交规格 ──▶ [任务状态机] ──▶ ParserAgent ──▶ GeneratorAgent ──▶ ReviewerAgent ──▶ ExporterAgent ──▶ 完成
                  │               │                │                 │                 │
                  └───────────────┴────────────────┴─────────────────┴─────────────────┘
                                 每步写入 step_log（状态/输入摘要/输出摘要/耗时/错误）
```

| Agent | 职责 | 输入 → 输出 |
|-------|------|------------|
| **ParserAgent** | 解析规格为测试单元 | 规格文件/文本 → `ApiEndpoint[]` / `RequirementUnit[]` |
| **GeneratorAgent** | 按策略调模型生成用例 | 测试单元 + 策略(等价类/边界值/场景/异常) → `TestCase[]`（mock 兜底） |
| **ReviewerAgent** | 校验 + 质量门禁 | `TestCase[]` → 校验报告(结构/Pydantic/覆盖率/异常占比/去重) |
| **ExporterAgent** | 导出多格式 | `TestCase[]` → xlsx / json / xmind 文件 |

**任务状态机**：`pending → running → completed | failed`（任一步骤失败 → failed，记录错误步骤）。

**可观测**：每个 step 记录 `name / status / started_at / finished_at / duration_ms / input_summary / output_summary / error`。

---

## 3. 目录结构

```
ai-testflow/
├── main.py                  # FastAPI 入口（挂载 API + 静态页）
├── requirements.txt
├── .env.example
├── app/
│   ├── core/
│   │   ├── config.py        # 配置（百炼Key、路径、模型名）
│   │   └── db.py            # SQLite 连接 + 建表
│   ├── models/
│   │   ├── task.py          # Task + StepLog（SQLAlchemy）
│   │   └── case.py          # 复用/桥接测试用例模型
│   ├── schemas/
│   │   └── task.py          # Pydantic 请求/响应
│   ├── services/
│   │   ├── parser.py        # 封装 ai-testcase-generator 解析
│   │   ├── generator.py     # 封装生成（含 mock 兜底）
│   │   └── exporter.py      # 封装导出
│   ├── workflow/
│   │   ├── engine.py        # 状态机 + 步骤调度
│   │   └── agents/
│   │       ├── parser_agent.py
│   │       ├── generator_agent.py
│   │       ├── reviewer_agent.py
│   │       └── exporter_agent.py
│   ├── api/
│   │   └── tasks.py         # REST 端点
│   └── static/
│       └── index.html       # 轻量前端 dashboard
├── uploads/                 # 上传的规格文件
├── outputs/                 # 导出的用例文件
└── tests/
```

---

## 4. API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 提交任务：上传规格文件 **或** 粘贴文本 + 选素材类型(api/business) + 导出格式 |
| GET | `/api/tasks` | 任务列表（状态/进度/用例数） |
| GET | `/api/tasks/{id}` | 任务详情 + 四步骤日志 + 用例概览 |
| GET | `/api/tasks/{id}/download?fmt=xlsx` | 下载导出文件 |
| GET | `/health` | 健康检查 |

FastAPI 自带 `/docs` Swagger 交互文档。

---

## 5. 轻量前端（MVP）

`static/index.html` 原生实现：
- 顶部：提交区（文件上传 / 文本粘贴 / 类型选择 / 格式选择 / 提交）
- 列表：任务卡片（状态徽章、进度条、用例数、耗时）
- 详情：四 Agent 步骤时间线（名称/状态/耗时/输出摘要/错误）
- 下载：xlsx / json / xmind 按钮

完整 React + 质量看板版本作为第二阶段。

---

## 6. 验收标准（MVP 完成定义）

1. `uvicorn main:app` 启动，访问 `/docs` 与 `/` 正常
2. 提交 DBERP 规格（api 或 business），任务从 `pending → running → completed`，四步骤日志齐全
3. 导出的 xlsx/json/xmind 文件可下载、内容结构化、异常/边界用例占比 ≥ 50%
4. **无百炼 Key 时 mock 兜底跑通全流程**（降低演示门槛）
5. 任一步骤异常 → 任务 `failed` 且错误步骤可定位
6. README 含架构图、快速开始、演示说明

---

## 7. 任务拆分（见 TaskList）

T7 方案 → T6 骨架 → T8 工作流引擎 → T9 接入模块 → T10 四 Agent → T11 API → T12 前端 → T13 跑通+README

---

## 8. 后续演进（非 MVP）

- **用户认证 + 多用户数据隔离（设计见第 9 节，V2 优先实施）**
- 完整 React 前端 + 质量看板（覆盖率/异常占比可视化）
- 定时执行（Celery/APScheduler）+ Allure 报告
- 接真实 DBERP 后端做端到端接口自动化闭环
- 部署到阿里云在线演示（需装 Node 跑前端）

---

## 9. 用户认证与多用户设计（V2）

> 目标：从"单机单用户演示"升级为"多用户 SaaS 形态"——注册登录、任务按用户隔离、管理员可管理用户。这也是面试讲点：**认证安全 + 数据权限隔离** 是测试工程师做测开/平台必备考点。

### 9.1 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 密码哈希 | `passlib[bcrypt]` | 业界标准，自带盐，不存明文 |
| Token | `pyjwt`（JWT HS256） | 无状态、FastAPI 生态最简；不引入 session/Redis 复杂度 |
| 鉴权方式 | `OAuth2PasswordBearer` | FastAPI 原生支持，`/docs` 里可直接调试探 Token |
| 依赖新增 | `passlib[bcrypt]>=1.7` `pyjwt>=2.8` | 轻量，无编译依赖 |

```bash
pip install "passlib[bcrypt]" pyjwt
```

### 9.2 数据模型变更

```python
# app/models/user.py
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(128), unique=True, nullable=False)
    password_hash = Column(String(128), nullable=False)   # bcrypt
    role = Column(String(16), default="user")             # user / admin
    is_active = Column(Boolean, default=True)             # 软禁用
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime)

# Task 表新增字段（SQLite 迁移脚本处理）
user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
```

- `Task.user_id` 允许 NULL：存量任务迁移时归到引导创建的默认管理员名下，不丢数据
- `StepLog` 不加用户字段（通过 task → user 间接归属，避免冗余）

### 9.3 认证流程

```
注册 POST /api/auth/register ──▶ 校验用户名/邮箱唯一 ──▶ bcrypt 哈希入库（首个用户自动 role=admin）
登录 POST /api/auth/login ─────▶ 校验密码 ──▶ 签发 JWT（payload: user_id/username/role，exp 24h）
受保护接口 ──▶ Authorization: Bearer <token> ──▶ get_current_user 解码校验
                                            ├─ token 无效/过期 ──▶ 401
                                            └─ 访问他人资源 ───────▶ 404（不暴露存在性）
```

**安全细节**：
- 密码强度：≥8 位且含字母+数字（注册时校验）
- JWT secret 从 `.env` 读取（`JWT_SECRET`），`.env.example` 提供占位
- 限速：登录接口失败 5 次锁 10 分钟（内存计数即可，MVP 不引 Redis）
- 管理员引导：首个注册用户 = admin；后续可用环境变量 `ADMIN_BOOTSTRAP` 预置

### 9.4 API 变更

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/auth/register` | 无 | 注册，返回用户信息（不含 hash） |
| POST | `/api/auth/login` | 无 | 返回 `access_token` + `token_type` |
| GET | `/api/auth/me` | Bearer | 当前用户信息 |
| GET | `/api/users` | Bearer + admin | 用户列表（admin 管理页用） |
| PATCH | `/api/users/{id}` | Bearer + admin | 启用/禁用用户 |
| POST | `/api/tasks` | Bearer | 创建时写入 `user_id` |
| GET | `/api/tasks` | Bearer | **只返回当前用户的任务**（admin 可带 `?all=true` 看全部） |
| GET | `/api/tasks/{id}` | Bearer | 非本人且非 admin → 404 |
| GET | `/api/tasks/{id}/download` | Bearer | 同上（防 URL 直链越权下载） |
| GET | `/health` | 无 | 保持公开 |

### 9.5 前端改动（MVP 原生 HTML）

- 新增登录/注册页（`/login`）：未登录访问 `/` 自动跳转
- Token 存 `localStorage`，`fetch` 统一注入 `Authorization` 头；401 时清 token 跳登录页
- 顶栏显示用户名 + 退出按钮；admin 用户多一个「用户管理」入口
- 任务列表页加「我的任务」筛选（admin 可切「全部」）

### 9.6 数据迁移（SQLite）

自写轻量迁移脚本 `scripts/migrate_v2.py`：
1. `ALTER TABLE tasks ADD COLUMN user_id INTEGER`（SQLite 支持 ADD COLUMN）
2. 建表 `users`，创建默认 admin（用户名 `admin`，密码从环境变量读，默认随机生成打印一次）
3. `UPDATE tasks SET user_id = <admin_id> WHERE user_id IS NULL`
4. 幂等：检测列已存在则跳过

### 9.7 测试设计（本职专业度，重点写进 README）

认证模块是**接口测试实战素材**，`tests/` 补充以下用例集：

| 类别 | 用例 |
|------|------|
| 注册 | 正常注册 / 用户名重复 409 / 邮箱格式非法 422 / 密码强度不足 422 / 用户名超长 |
| 登录 | 正确密码 / 密码错误 401（提示模糊化）/ 用户不存在 401（与密码错误同文案，防枚举）/ 禁用用户 403 / 连续错 5 次锁定 |
| Token | 缺失 Authorization 401 / 格式错误 401 / 过期 401（伪造 exp 验证）/ 篡改签名 401 |
| 越权 | 用户 A 访问用户 B 的任务详情 404 / 越权下载他人导出文件 404 / user 调 admin 接口 403 |
| 并发 | 同一账号并发登录多端 token 互不影响（无状态 JWT 天然支持） |

### 9.8 实施拆分（V2 迭代）

- T14：User 模型 + 注册/登录 API + bcrypt/JWT（0.5 天）
- T15：get_current_user 依赖 + 存量接口加鉴权 + 任务按 user_id 过滤（0.5 天）
- T16：迁移脚本 + 引导 admin（0.5 天）
- T17：前端登录页 + 401 拦截跳转 + 顶栏（0.5 天）
- T18：登录限速 + admin 用户管理页（0.5 天）
- T19：认证/越权测试用例集 + README 更新（0.5 天）

### 9.9 验收标准

- [ ] 未带 Token 访问 `/api/tasks` → 401；注册登录后可正常提交任务
- [ ] 用户 A 无法看到/下载用户 B 的任何任务（404）
- [ ] admin 可查看全部任务、管理用户启停
- [ ] 密码哈希入库（非明文）、JWT 过期自动登出
- [ ] 越权/认证测试用例集 ≥ 15 条且全部通过
- [ ] 存量 SQLite 数据迁移后任务不丢失
