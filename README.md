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
| 数据库 | **SQLite**（SQLAlchemy ORM） |
| AI 模型 | **阿里百炼 / 通义千问**（无 Key 自动 mock 兜底） |
| 前端（MVP） | 原生 HTML + JS（任务列表 / 四步骤时间线 / 下载） |
| 用例生成核心 | 内置 `generator_core/`（整合自 CLI 原型，自包含、clone 即跑） |

---

## 快速开始

```bash
pip install -r requirements.txt
cp .env.example .env        # 可选：填 DASHSCOPE_API_KEY 接真模型；留空走 mock
python main.py              # 等价于 uvicorn main:app --port 8000
```

访问：
- 前端 Dashboard：http://127.0.0.1:8000
- 接口文档（Swagger）：http://127.0.0.1:8000/docs

---

## 演示流程（30 秒出成品）

1. 首页选「接口规格(api)」或「业务需求(business)」
2. 上传 DBERP 规格文件（`examples/` 下有现成样本），或粘贴文本
3. 选导出格式（xlsx / json / xmind），点「提交任务」
4. 任务列表实时刷新；点开看 **四 Agent 步骤时间线** 与 **质量报告**
5. 一键下载导出的测试用例文件

> 无百炼 Key 时自动走 **mock 兜底**，无需联网即可演示完整编排流程。

---

## REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 提交任务：`file` 或 `text` + `kind` + `formats` |
| GET | `/api/tasks` | 任务列表 |
| GET | `/api/tasks/{id}` | 任务详情 + 四步骤日志 |
| GET | `/api/tasks/{id}/download?fmt=` | 下载导出文件（xlsx/json/xmind） |
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
├── main.py                  # 入口（挂载 API + 静态页）
├── requirements.txt
├── generator_core/          # 内置用例生成核心（config/ + src/，自包含）
├── examples/                # DBERP 接口规格 / 业务需求样本
├── app/
│   ├── core/                # config（百炼Key/mock开关）、db（SQLite）
│   ├── models/task.py       # Task + StepLog（SQLAlchemy）
│   ├── schemas/task.py      # Pydantic 响应
│   ├── services/
│   │   └── pipeline_lib.py  # 调用内置 generator_core 解析/生成/导出
│   ├── workflow/
│   │   ├── engine.py        # 状态机 + 步骤调度
│   │   └── agents/          # 四 Agent（parser/generator/reviewer/exporter）
│   ├── api/tasks.py         # REST 端点
│   └── static/index.html    # 轻量前端 dashboard
├── uploads/  outputs/       # 上传 / 导出目录（已 gitignore）
└── 项目1-工作流平台-MVP执行方案.md
```

---

## 后续演进（非 MVP）

- 完整 **React + TS** 前端 + 质量看板（覆盖率 / 异常占比可视化）
- 定时执行（APScheduler）+ **Allure 报告**
- 接真实 DBERP 后端做**端到端接口自动化闭环**
- 部署到阿里云在线演示（前端阶段需装 Node.js）
