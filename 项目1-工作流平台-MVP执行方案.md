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

- 完整 React 前端 + 质量看板（覆盖率/异常占比可视化）
- 定时执行（Celery/APScheduler）+ Allure 报告
- 接真实 DBERP 后端做端到端接口自动化闭环
- 部署到阿里云在线演示（需装 Node 跑前端）
