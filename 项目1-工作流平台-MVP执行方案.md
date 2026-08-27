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

> 目标：从"单机单用户演示"升级为"多用户 SaaS 形态"——三级角色（**访客 guest / 注册用户 user / 管理员 admin**），访客免注册按 IP 体验、数据 24h 自动回收；注册用户数据持久；管理员负责用户与访客治理。这也是面试讲点：**认证安全 + 数据权限隔离 + 多租户数据生命周期** 是测试工程师做测开/平台必备考点。

### 9.0 角色与数据生命周期总览

| 维度 | 访客 guest | 普通用户 user | 管理员 admin |
|------|-----------|--------------|--------------|
| 身份来源 | 按 IP 自动创建（`guest_<ip_hash>`） | 注册（用户名+邮箱+密码） | 首个注册用户 / 环境变量预置 |
| 登录方式 | 免登录，首次访问自动发 guest token | 账号密码 → JWT | 账号密码 → JWT |
| 数据保留 | **1 天（24h）**，到期自动删除 | 永久（用户注销前） | 永久 |
| 文件目录 | `uploads/guest_<ip_hash>/`、`outputs/guest_<ip_hash>/`（临时目录） | `uploads/u_<user_id>/`、`outputs/u_<user_id>/` | 同 user |
| 任务上限 | 单访客 ≤10 个任务（防滥用） | 无硬限制（可配） | 无限制 |
| 可见任务 | 仅自己的 | 仅自己的 | 全部（`?all=true`） |
| 管理能力 | 无 | 无 | 用户管理 / 访客治理 / 全局任务 |

```
访客生命周期：
IP 首次访问 ──▶ 创建 guest 用户（expires_at = now + 24h）
            ──▶ 签发 guest JWT（role=guest，exp 与 expires_at 对齐）
            ──▶ 建临时目录 uploads/guest_<hash>/ outputs/guest_<hash>/
24h 到期（定时任务，每小时扫一次 + 访客访问时懒清理双保险）
            ──▶ 删除该 guest 的 tasks / step_logs / 上传与导出文件 / 临时目录
            ───▶ 物理删除 guest 用户记录，清理动作写入 clean_log（审计）
```

- **IP 获取**：`X-Forwarded-For`（nginx 反代场景取第一跳）> `request.client.host`；本地演示即 127.0.0.1
- **IP 哈希存目录名**（不存明文 IP，降低隐私敏感度；DB 中存 `ip_hash` 用于同 IP 复用 guest）
- **同 IP 二次访问**：若该 IP 的 guest 未过期 → 直接续发 token（数据续用）；已过期 → 新建 guest（旧数据已清）
- 访客过期判定以 `User.expires_at` 为准，guest JWT 的 `exp` 设为 `expires_at` 时刻，token 失效与数据删除同步

### 9.1 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 密码哈希 | `passlib[bcrypt]` | 业界标准，自带盐，不存明文 |
| Token | `pyjwt`（JWT HS256） | 无状态、FastAPI 生态最简；不引入 session/Redis 复杂度 |
| 鉴权方式 | `OAuth2PasswordBearer` | FastAPI 原生支持，`/docs` 里可直接调试探 Token |
| 依赖新增 | `passlib[bcrypt]>=1.7` `pyjwt>=2.8` `bcrypt<4.1` | 轻量，无编译依赖（bcrypt≥4.1 与 passlib 1.7.x 不兼容，必须 pin） |

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
    email = Column(String(128), unique=True, nullable=True)      # guest 无邮箱 → 允许 NULL
    password_hash = Column(String(128), nullable=True)           # guest 无密码 → 允许 NULL
    role = Column(String(16), default="user")                    # guest / user / admin
    ip_hash = Column(String(64), index=True)                     # 仅 guest：同 IP 复用
    expires_at = Column(DateTime, nullable=True)                 # 仅 guest：now + 24h
    data_dir = Column(String(128))                               # u_<id> / guest_<ip_hash>
    is_active = Column(Boolean, default=True)                    # 软禁用
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime)

# Task 表新增字段（SQLite 迁移脚本处理）
user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

# 防滥用计数（独立于 users 表，不随 guest 删除而丢失，见下）
class GuestCreationLog(Base):
    __tablename__ = "guest_creation_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    ip_hash = Column(String(64), index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    # 只追加不删（或只清 7 天前记录），保证"单 IP 24h ≤ 5 个 guest"可验证

# 清理审计（guest 用户记录本身物理删，审计走这张表，不与 username unique 冲突）
class CleanLog(Base):
    __tablename__ = "clean_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    guest_ip_hash = Column(String(64))
    deleted_tasks = Column(Integer, default=0)
    deleted_files = Column(Integer, default=0)
    trigger = Column(String(16))            # scheduler / manual / lazy（访问时懒清理）
    cleaned_at = Column(DateTime, default=datetime.utcnow)
```

- `email`/`password_hash` 改为可空：访客无邮箱无密码，靠 IP + guest token 识别
- `Task.user_id` 允许 NULL：存量任务迁移时归到引导创建的默认管理员名下，不丢数据
- `StepLog` 不加用户字段（通过 task → user 间接归属，避免冗余）
- **guest username 规则**：`guest_<ip_hash>_<seq>`（seq 按该 ip_hash 历史创建数递增，从 GuestCreationLog 取）——guest 记录**到期物理删除**，审计由 `clean_log` 承担，不再走"软删保留 7 天"路线（软删会撞 username unique，导致同 IP 重建 guest 失败）
- **防滥用计数独立建表**：`guest_creation_log` 只记录 ip_hash + 时间，不随 guest 清理删除——否则计数器随 guest 记录一起被删，"单 IP 24h ≤ 5"形同虚设；超限返回 429
- 文件隔离：上传/导出按 `data_dir` 分目录，访客临时目录随 TTL 整目录删除，注册用户目录独立互不影响

### 9.3 认证流程

```
访客（免注册体验）：
GET / 或 POST /api/guest/token ──▶ 取 IP → ip_hash
    ├─ 存在未过期 guest(ip_hash) ──▶ 直接签发 guest JWT（exp = expires_at）
    └─ 不存在/已过期 ──▶ 建 guest 用户 + 临时目录 ──▶ 签发 guest JWT
受保护接口对 guest 同样有效：guest token 一样走 get_current_user，role=guest 仅能力受限

注册用户：
注册 POST /api/auth/register ──▶ 校验用户名/邮箱唯一 ──▶ bcrypt 哈希入库（首个用户自动 role=admin）
登录 POST /api/auth/login ─────▶ 校验密码 ──▶ 签发 JWT（payload: user_id/username/role，exp 24h）
受保护接口 ──▶ Authorization: Bearer <token> ──▶ get_current_user 解码校验
                                            ├─ token 无效/过期 ──▶ 401
                                            ├─ 解码后**必须回查 DB**（不是纯无状态）：
                                            │    ├─ 用户不存在/ is_active=False ──▶ 401（禁用即时生效）
                                            │    └─ guest 且 expires_at < now ──▶ 401 + 顺带懒清理该 guest + 前端引导注册
                                            └─ 访问他人资源 ───────▶ 404（不暴露存在性）
```

**安全细节**：
- 密码强度：≥8 位且含字母+数字（注册时校验）
- JWT secret 从 `.env` 读取（`JWT_SECRET`），`.env.example` 提供占位；**启动时检测**：若为默认占位值则打 WARNING（演示可跑），生产环境拒绝启动
- 限速：登录接口失败 5 次锁 10 分钟（内存计数即可，MVP 不引 Redis；注意仅在单进程 uvicorn 下有效，多 worker 需换共享存储——MVP 明确单进程部署）
- 管理员引导：首个注册用户 = admin；后续可用环境变量 `ADMIN_BOOTSTRAP` 预置
- 访客防滥用：guest 任务上限 10 个 + 单 IP 每 24h 最多新建 5 个 guest 身份（**计数走 `guest_creation_log` 独立表，超限 429**，不随 guest 记录删除失效）
- 访客转正：guest 在过期前可「一键转正」——原 guest 的任务与文件迁入新注册用户目录，数据不丢
- **IP 信任边界**：`X-Forwarded-For` 客户端可伪造——只有部署在自管 nginx 后（nginx 重写 XFF 为真实来源）才开启 `uvicorn --proxy-headers` 解析；本地/直连部署一律用 `request.client.host`。防止公网伪造 XFF 无限刷 guest
- **依赖版本坑**：`passlib 1.7.x` 与 `bcrypt>=4.1` 组合会报 `__about__` 警告/异常，requirements 里 pin `bcrypt<4.1`；bcrypt 仅取密码前 72 字节，注册时顺带校验长度上限

**访客清理任务（TTL 24h）**：
- `app/jobs/guest_cleaner.py`，APScheduler 每小时执行（随 FastAPI 生命周期启动，不引 Celery）
- 逻辑：`DELETE FROM users WHERE role='guest' AND expires_at < now`
  - 级联删除该 guest 的 tasks / step_logs（DB 外键 ON DELETE CASCADE 或手动删）
  - `shutil.rmtree(uploads/guest_<hash>/, outputs/guest_<hash>/)`（`ignore_errors=True` 防并发占用）
- 兜底：启动时也跑一次（服务重启间隔可能超 1h）
- **懒清理**：guest 请求进来发现 `expires_at < now` 时同步执行清理再返回 401——把"过期后数据仍存活最长 1h"的窗口收窄到"该访客下次访问即清"
- 删除动作写 `clean_log` 表（admin 可见"今晨清理了 N 个访客"），面试可讲数据生命周期治理

### 9.3.1 关键实现示例（评审问题的落地代码）

**① guest username 防撞唯一约束**（seq 从 GuestCreationLog 取历史创建数，记录物理删不冲突）：

```python
seq = db.query(GuestCreationLog).filter(
    GuestCreationLog.ip_hash == ip_hash).count()
username = f"guest_{ip_hash}_{seq}"            # 永不重复
user = User(username=username, role="guest", ip_hash=ip_hash,
            expires_at=now + timedelta(hours=24),
            data_dir=f"guest_{ip_hash}_{seq}")
```

**② 防滥用计数走独立表**（guest 记录被删计数仍在，超限 429）：

```python
def get_or_create_guest(ip_hash: str, db: Session) -> User:
    # 未过期的 guest 直接续用
    guest = db.query(User).filter(User.ip_hash == ip_hash,
                                  User.role == "guest",
                                  User.expires_at > now()).first()
    if guest:
        return guest
    # 24h 窗口内创建次数（日志只追加不删，计数才有效）
    recent = db.query(GuestCreationLog).filter(
        GuestCreationLog.ip_hash == ip_hash,
        GuestCreationLog.created_at >= now() - timedelta(hours=24)).count()
    if recent >= 5:
        raise HTTPException(429, "该 IP 今日访客体验次数已用完，请注册")
    # 新建 guest + 追加创建日志
    ...
    db.add(GuestCreationLog(ip_hash=ip_hash))
```

**③ get_current_user 解码后回查 DB**（禁用即时生效 + 过期 guest 懒清理）：

```python
async def get_current_user(token: str = Depends(oauth2_scheme),
                           db: Session = Depends(get_db)) -> User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401)
    user = db.get(User, payload["user_id"])     # 每次都查库
    if not user or not user.is_active:          # admin 禁用 → 下一请求即 401
        raise HTTPException(401)
    if user.role == "guest" and user.expires_at < datetime.utcnow():
        clean_guest(user, db, trigger="lazy")   # 顺手懒清理
        raise HTTPException(401, "体验已到期，数据已清理")
    return user
```

> 代价是每请求多一次 SQLite 主键查询（微秒级，可忽略）；面试讲点：无状态 JWT 吊销难，用轻量回查折中。

**④ XFF 伪造防护（nginx 侧强制重写）**：

```nginx
# 仅自管反代场景；nginx 覆盖客户端伪造的头
proxy_set_header X-Forwarded-For $remote_addr;
```

```python
# main.py 启动兜底
if config.JWT_SECRET == DEFAULT_PLACEHOLDER:
    if config.ENV == "production":
        raise RuntimeError("生产环境必须设置 JWT_SECRET")
    logger.warning("JWT_SECRET 为默认占位值，仅限本地演示")
```

### 9.4 API 变更

| 方法 | 路径 | 鉴权 | 说明 |
|------|------|------|------|
| POST | `/api/guest/token` | 无 | 按 IP 建/复用访客身份，返回 guest JWT + 剩余有效时长；超 24h/5 个上限 → 429 |
| POST | `/api/guest/upgrade` | Bearer + guest | 访客转注册用户（任务与文件迁移到新账户） |
| POST | `/api/auth/register` | 无 | 注册，返回用户信息（不含 hash） |
| POST | `/api/auth/login` | 无 | 返回 `access_token` + `token_type` |
| POST | `/api/auth/change-password` | Bearer | 修改本人密码（旧密码校验，改后旧 token 仍有效至 exp，可接受） |
| GET | `/api/auth/me` | Bearer | 当前用户信息（guest 含 expires_at 倒计时） |
| GET | `/api/users` | Bearer + admin | 用户列表（含 guest，标记角色/过期时间/任务数） |
| PATCH | `/api/users/{id}` | Bearer + admin | 启用/禁用注册用户；禁用 guest = 立即清理其数据 |
| DELETE | `/api/users/{id}` | Bearer + admin | 删除用户（级联任务/文件；admin 本人不可删） |
| POST | `/api/admin/guests/clean` | Bearer + admin | 手动触发访客清理（返回清理数量） |
| GET | `/api/admin/stats` | Bearer + admin | 统计：注册用户数 / 活跃访客数 / 24h 清理数 |
| POST | `/api/tasks` | Bearer | 创建时写入 `user_id`，文件落 `data_dir` 目录 |
| GET | `/api/tasks` | Bearer | **只返回当前用户的任务**（admin 可带 `?all=true` 看全部） |
| GET | `/api/tasks/{id}` | Bearer | 非本人且非 admin → 404 |
| GET | `/api/tasks/{id}/download` | Bearer | 同上（防 URL 直链越权下载） |
| GET | `/health` | 无 | 保持公开 |

### 9.5 前端改动（MVP 原生 HTML）

- 登录页增加「游客体验」入口：点击 → `POST /api/guest/token` → 存 token 进主页（免注册）
- 未登录访问 `/` 不再强制跳登录，而是弹「登录 / 注册 / 游客体验」三选一
- 顶栏显示身份徽标：`访客（剩余 xx 小时）` / 用户名 / `管理员`；访客顶栏常驻「注册保留数据」引导按钮
- 访客任务数达 10 个时前端 toast 提示上限并引导注册
- Token 存 `localStorage`，`fetch` 统一注入 `Authorization` 头；401 时清 token → 访客过期则提示"体验已到期，数据已清理，注册后可长期保留"
- 顶栏显示用户名 + 退出按钮；admin 用户多一个「用户管理」入口（用户/访客列表、启停、手动清理访客、统计看板）
- 任务列表页加「我的任务」筛选（admin 可切「全部」）

### 9.6 数据迁移（SQLite）

自写轻量迁移脚本 `scripts/migrate_v2.py`：
1. `ALTER TABLE tasks ADD COLUMN user_id INTEGER`（SQLite 支持 ADD COLUMN）
2. 建表 `users` / `guest_creation_log` / `clean_log`（email/password_hash 可空，含 ip_hash/expires_at/data_dir），创建默认 admin（用户名 `admin`，密码从环境变量读，默认随机生成打印一次）
3. `UPDATE tasks SET user_id = <admin_id> WHERE user_id IS NULL`
4. 已有 `uploads/`、`outputs/` 平铺文件迁入 `outputs/u_<admin_id>/`
5. 幂等：检测列已存在则跳过；**admin 已存在也跳过（不重置密码、不重复打印）**——脚本可安全重复执行

### 9.7 测试设计（本职专业度，重点写进 README）

认证模块是**接口测试实战素材**，`tests/` 补充以下用例集：

| 类别 | 用例 |
|------|------|
| 注册 | 正常注册 / 用户名重复 409 / 邮箱格式非法 422 / 密码强度不足 422 / 用户名超长 |
| 登录 | 正确密码 / 密码错误 401（提示模糊化）/ 用户不存在 401（与密码错误同文案，防枚举）/ 禁用用户 403 / 连续错 5 次锁定 |
| Token | 缺失 Authorization 401 / 格式错误 401 / 过期 401（伪造 exp 验证）/ 篡改签名 401 / **用户被禁用后存量 token 立即 401（DB 回查生效）** |
| 越权 | 用户 A 访问用户 B 的任务详情 404 / 越权下载他人导出文件 404 / user 调 admin 接口 403 / guest 调 admin 接口 403 |
| 防滥用 | 同 IP 24h 第 6 个 guest → 429（guest 记录已删计数仍在）/ guest 第 11 个任务 → 429 / 伪造 X-Forwarded-For 在直连部署下不影响判 IP |
| 并发 | 同一账号并发登录多端 token 互不影响（无状态 JWT 天然支持） |
| 访客生命周期 | 同 IP 首访自动建 guest / 同 IP 二访复用同一 guest / 不同 IP 各自 guest 互相隔离 / guest 任务上限第 11 个 429 / 过期 guest token 401 / 过期 guest 数据与临时目录被清理 / 清理后同 IP 再访新建 guest / 访客转注册后任务与文件完整迁移 |
| 访客清理任务 | 到期 guest 的 tasks/step_logs 被级联删 / 临时目录（上传+导出）被删 / 未到期 guest 不被误删 / 服务重启后清理兜底执行 / admin 手动清理接口生效并返回数量 / **懒清理：过期 guest 携旧 token 访问 → 401 且数据同步被清** |
| 角色权限矩阵 | 三角色 × 核心接口状态码全组合（guest/user/admin × 8 接口 = 24 条断言，表驱动参数化跑） |

### 9.8 实施拆分（V2 迭代）

- T14：User 模型（三级角色 + guest 字段）+ GuestCreationLog/CleanLog 表 + 注册/登录/改密 API + bcrypt/JWT（0.5 天）
- T15：get_current_user 依赖（**每请求回查 DB：is_active + guest expires_at**）+ 存量接口加鉴权 + 任务按 user_id 过滤 + 文件按 data_dir 分目录（0.5 天）
- T16：访客身份：`POST /api/guest/token`（IP→ip_hash 建/复用 guest）+ guest 任务上限 + 单 IP 24h 5 个身份上限（guest_creation_log）+ 访客转注册迁移（0.5 天）
- T17：访客清理：APScheduler 每小时清理过期 guest（DB 级联 + 临时目录删除 + clean_log 审计）+ 启动兜底 + 访问时懒清理 + admin 手动清理接口（0.5 天）
- T18：迁移脚本 + 引导 admin（0.5 天）
- T19：前端：登录/注册/游客体验三入口 + 401 拦截 + 顶栏身份徽标与到期倒计时（0.5 天）
- T20：登录限速 + admin 用户管理页（用户/访客列表、启停、统计看板）（0.5 天）
- T21：认证/越权/访客生命周期/角色矩阵测试用例集 + README 更新（0.5 天）

### 9.9 验收标准

- [ ] 未带 Token 访问 `/api/tasks` → 401；注册登录后可正常提交任务
- [ ] 用户 A 无法看到/下载用户 B 的任何任务（404）
- [ ] admin 可查看全部任务、管理用户启停、手动清理访客、看到统计
- [ ] 密码哈希入库（非明文）、JWT 过期自动登出
- [ ] **访客：同 IP 免登录自动获得身份；数据/文件隔离在 guest_<ip_hash> 临时目录；24h 后任务、日志、文件全部自动删除且同 IP 再访是全新身份**
- [ ] 访客任务上限 10 个生效；同 IP 24h 第 6 个 guest 身份 → 429；访客转注册后数据完整迁移不丢
- [ ] admin 禁用用户后，该用户**存量 token 立即 401**（get_current_user 回查 DB）
- [ ] 越权/认证/访客生命周期测试用例集 ≥ 30 条且全部通过
- [ ] 存量 SQLite 数据迁移后任务不丢失
