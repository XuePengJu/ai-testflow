# 项目1 · 探索 Agent 计划先行改造 — 执行计划 V1.0

> 日期：2026-09-28 ｜ 分支：`feature/plan-first-explore`（基于 `feature/layered-decision` @ 84d9a11）
> 状态：**待老板确认，业务代码一行未动**（本文件是分支上第一个 commit）

## 0. 背景（本次会话对齐的三个共识）

1. **探索式测试不稳定**：自由 ReAct 无任务边界、单步决策无记忆 → 方向不受控，轨迹反推用例质量不可预期
2. **全链路测试黑盒**：探索/执行过程中前端看不到页面，截图滞后
3. **SPA 判重难题**：tab 切换 URL 不变，无法判断页面是否探索过

已拍板方向：**探索 = 计划先行 + 结构化护栏 + 视图指纹判重**；另附已批的实时画面开关。

## 1. 交付范围（4 个模块）

| 模块 | 内容 | 层 |
|------|------|-----|
| M1 | 视图指纹判重 + 循环检测 | 后端 |
| M2 | 结构化护栏（页面白名单 + 步数预算） | 后端 |
| M3 | 计划先行（探索计划生成 → 前端确认 → 按计划执行） | 后端 + 前端 |
| M4 | 实时画面开关（截图轮播，已批方案 A） | 后端 + 前端 |

## 2. 分支策略

- 从 `feature/layered-decision` 切出（M1/M2 需叠在 FastDecider 决策层之上）
- **全程本地，不 push、不部署**（协作铁律）
- 每个模块独立 commit，方便单独回退

## 3. 模块细节

### M1 视图指纹判重（后端，~0.5 天）

- 新文件 `app/services/view_fingerprint.py`：
  - `fingerprint(url, snapshot)` = 规范化 URL + 激活状态 + 可交互元素 `(role, name)` 有序集合
  - URL 规范化：去 fragment、去 utm/时间戳/随机 id 类噪声参数
  - 激活状态：从快照识别 `aria-selected` / active class 的 tab、菜单项名称
  - `similarity(a, b)`：`(role, name)` 集合 Jaccard 相似度，**≥0.9 判同一视图**（不用精确 hash，防懒加载/时间戳误判）
- 集成 `app/services/explorer_agent.py`：
  - Agent 会话状态加 `visited: list[{fp, tab_triggers, clicked}]`（纯内存，单次任务内，不改 DB）
  - 每步快照后先查重；决策 prompt 注入「已见视图 + 未触发 tab 清单」，禁止重复漫游
  - **循环检测**：同指纹连续 3 步无新元素 → 注入强制换目标指令
  - 「探索完」定义：该视图全部 tab 触发器点过一遍（集合覆盖）
- 开关 `EXPLORE_FINGERPRINT=1`（默认开，env 可关回旧行为）

### M2 结构化护栏（后端，~0.5 天，依赖 M1 的 visited 结构）

- **页面白名单**：e2e 任务取 crawler `pages.json` 的 URL 集；纯 explore 任务取初始 URL 同域。越界动作直接拒绝并在结果中说明
- **步数预算**：核查现有 `EXPLORE_MAX_STEPS` 后复用/补齐
- 开关 `EXPLORE_GUARDRAILS=1`（默认开）

### M3 计划先行（后端 + 前端，~1~1.5 天，依赖 M1/M2）

- 后端：
  - explore 任务启动 → 新步骤「生成探索计划」（LLM 输入 = 用户需求 + 种子页面快照）→ 产出 3~5 条业务流计划，落盘 `out_dir/plan.json`
  - 任务进入**计划待确认**状态（实现时先读 tasks 状态机，优先在 step 粒度暂停，避免动全局任务状态）
  - 新 API：`GET /tasks/{id}/explore-plan`（读计划）；`POST /tasks/{id}/explore-plan/confirm`（提交编辑后计划，engine 恢复执行）
  - 开关 `EXPLORE_PLAN_FIRST=1`（默认开；关闭 = 旧的自由探索行为）
- 前端：
  - `TaskStepsCard` 新增「探索计划」确认卡：业务流列表（勾选/删除/改标题）+「按计划探索」按钮 → confirm API
  - `ExploreTimeline` 每步加「新视图 / 已见」徽章（消费 M1 在 events 里带的 `is_new_view`）

### M4 实时画面开关（后端 + 前端，~0.5~1 天，独立，可插队先做）

- 后端：`GET /tasks/{id}/live-shot` → `_task_out_dir/explore/` 下最新 `step-NNN.png`（FileResponse；非 JSON 响应不加密，无需中间件豁免；`_own_task` 归属校验）
- 前端：`TaskStepsCard` 探索步骤 running 时显示「实时画面」勾选框（默认不勾，不勾 = 零开销）；勾选后 3s 轮询 blob 刷新 `<img>`，显示当前动作摘要；任务结束自动停
- `frontend/src/api/client.ts` 加 `fetchTaskLiveShot(taskId)`

## 4. 实施顺序

```
M1 指纹 → M2 护栏 → M3 计划先行 →（M4 实时画面随时可插队）
```

## 5. 验证与回归

- 每模块完成：`pytest tests/ --basetemp=/tmp/pt-planfirst`（395 基线全绿 + 新增单测：指纹稳定性/Jaccard 阈值/白名单拦截/计划状态机）
- `npm run build`（看完整输出，防管道吞 tsc 报错）
- e2e 回归：m2 / m3 全流程；本地 `curl --noproxy '*' 127.0.0.1:8000/` 2xx 实测
- **对照实验**：同一 DBERP 探索任务跑 3 次，对比视图数 / 重复访问率 / 总步数，量化稳定性提升
- UI 改动截图 ≥3 张落 `/tmp/e2e-*`（push 门槛，本批次不 push 也要留档）

## 6. 风险与明确不做的事

- 风险：计划待确认状态可能波及任务轮询/重试逻辑 → 实现时优先 step 粒度暂停，不动全局状态机
- 明确不做（另开批次）：CDP 视频流、Chrome 扩展本地探索、知识库/首页改版、会话标题治理
- 不 push、不部署云端

## 7. 工作量预估

| 模块 | 预估 |
|------|------|
| M1 视图指纹 | 0.5 天 |
| M2 护栏 | 0.5 天 |
| M3 计划先行 | 1~1.5 天 |
| M4 实时画面 | 0.5~1 天 |
| **合计（含回归）** | **2.5~3.5 天** |
