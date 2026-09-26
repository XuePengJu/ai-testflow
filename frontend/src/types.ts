/** 与后端 UserOut 对齐的核心类型（V2.8 M1） */

export type Role = "guest" | "user" | "admin";

export interface Me {
  id: number;
  username: string;
  email: string | null;
  role: Role;
  is_active: boolean;
  expires_at?: string | null;
  remaining_hours?: number | null;
}

/** 登录/注册/访客明文通道的响应体 */
export interface AuthResponse {
  access_token: string;
  token_type: string;
  username: string;
  role: Role;
  enc_key: string;
  remaining_hours?: number;
}

/* ===== V2.8 M2：对话驱动 + 任务列表（与后端 schemas 对齐） ===== */

/** 与 app/schemas/conversation.py:MessageOut 对齐 */
export interface ConvMessage {
  id: number;
  role: string;
  content: string;
  thinking: string;
  /** V4.5.2：持久化的 RAG 引用溯源（assistant 消息），与 CitationItem 对齐 */
  citations?: CitationItem[] | null;
  task_id?: string | null;
  created_at?: string | null;
  task?: TaskSummary | null;
}

/** 会话详情接口里消息内嵌的任务摘要 */
export interface TaskSummary {
  id: string;
  name: string;
  status: string;
  cases_count?: number;
  duration_ms?: number | null;
  created_at?: string | null;
  /** 节点步骤（含 input_summary / output_summary），与后端 _task_brief 对齐 */
  steps?: StepLog[];
  report?: { summary?: string };
}

export interface Conversation {
  id: string;
  title: string;
  created_at?: string | null;
  updated_at?: string | null;
  message_count: number;
  task_count: number;
  messages?: ConvMessage[];
  mode?: string;            // V4.1：workflow=首页工作流；kb_qa=知识库问答
  kb_id?: string | null;    // V4.1：知识库问答绑定的库
}

/** V4.1 引用溯源（SSE citations 事件 items 元素，与后端 _build_rag_context 对齐） */
export interface CitationItem {
  chunk_id: string;
  knowledge_id: string;
  doc_title: string;
  context_header: string;
  snippet: string;
  score: number | null;
}

/** 与 app/schemas/task.py:StepLogOut 对齐 */
export interface StepLog {
  name: string;
  title: string;
  status: string;
  /** running 期间实时子进度（如"正在为第 2/5 个测试点生成用例…"） */
  progress?: string | null;
  started_at?: string | null;
  duration_ms?: number | null;
  input_summary?: string | null;
  output_summary?: string | null;
  error?: string | null;
}

/** M3：任务详情里的单条用例（详情接口 cases[] 注入） */
export interface CaseItem {
  case_id: string;
  title: string;
  module?: string;
  case_type?: string;
  priority?: string;
  pre_condition?: string;
  steps?: string[];
  step_expectations?: string[];
  expected?: string;
  test_data?: string;
}

/** 与 app/schemas/task.py:TaskOut 对齐（列表接口 cases 为 []） */
export interface Task {
  id: string;
  name: string;
  kind: string;
  source_type: string;
  status: string;
  cases_count: number;
  duration_ms: number;
  formats: string;
  category_id?: number | null;
  parent_task_id?: string | null;
  /** 所属会话 id：详情页「继续优化」据此跳回会话挂载迭代引用（后端对历史任务做反查兜底） */
  conversation_id?: string | null;
  /** M3 全链路：e2e 任务绑定的被测系统 id（契约 3 TaskOut 增量） */
  target_id?: string | null;
  /** M3：该任务已有自动化脚本（详情抽屉显示「自动化」Tab 的依据） */
  has_auto?: boolean;
  created_at?: string | null;
  finished_at?: string | null;
  steps: StepLog[];
  cases?: CaseItem[];
}

/** 聊天输入草稿（流式回复完成后「生成测试用例」消费） */
export interface ChatDraft {
  text: string;
  file?: File | null;
  kind: string;
  formats: string[];
  /** 「总是深度思考」开关：true=每轮都先推理；null/省略=按需（由后端判定是否值得推理） */
  thinking?: boolean | null;
  /** 多角色协作（V3.1）：参与生成的视角，如 ["pm","qa","dev"]，默认 ["qa"] */
  roles?: string[];
}

/* ===== V2.8 M4：设置页 / admin / 分类（与后端对齐） ===== */

/** 与 app/core/providers.py:PROVIDERS 对齐（键为厂商 id） */
export interface ProviderModel {
  id: string;
  label: string;
  vision: boolean;
}

export interface ProviderPreset {
  label: string;
  base_url: string;
  note: string;
  models: ProviderModel[];
}

export type ProviderMap = Record<string, ProviderPreset>;

/** 与 app/schemas/llm_pool.py:PoolItemOut 对齐（模型池一条） */
export interface LLMPoolItem {
  id: number;
  slot: string;
  provider: string;
  provider_label: string;
  base_url: string;
  model: string;
  /** 如 ****abcd；空 = 靠服务器环境变量兜底 Key */
  api_key_masked: string;
  priority: number;
  enabled: boolean;
  /** 付费模型（老板承担费用，界面给橙色徽标提示） */
  paid: boolean;
  note: string;
  /** Key 指纹（判重/同 Key 提示用，不泄露 Key） */
  key_fingerprint: string;
  cooldown_until: string | null;
  cooling: boolean;
  last_error: string | null;
  success_count: number;
  fail_count: number;
  /** 是否为当前实际生效的池（用户池非空时平台池为 false） */
  effective: boolean;
}

/** 池内一条的新增 / 修改入参（api_key 缺省 = 保留原 Key） */
export interface LLMPoolItemIn {
  provider: string;
  base_url: string;
  model: string;
  api_key?: string | null;
  paid?: boolean;
  note?: string;
  enabled?: boolean;
  priority?: number | null;
}

/** GET /api/llm/pool/{slot}/health */
export interface LLMPoolHealth {
  slot: string;
  total: number;
  enabled: number;
  available: number;
  items: Array<{
    id: number;
    model: string;
    enabled: boolean;
    cooling: boolean;
    cooldown_until: string | null;
    last_error: string | null;
    success_count: number;
    fail_count: number;
  }>;
}

/** /api/llm/effective 的 pools[slot]：该槽位池的现状（V5.1 P1） */
export interface LLMPoolStats {
  /** 有可用候选才算池真的接管 */
  active: boolean;
  /** 池归属：personal = 我的池 / platform = 平台池 / null = 未启用池 */
  owner: "personal" | "platform" | null;
  /** 池内全部条数（含已停用），与池卡卡头同口径 */
  total: number;
  enabled: number;
  available: number;
  cooling: number;
  /** 优先序号（1-based）；0 = 无可用候选 */
  hit: number;
}

/** /api/llm/effective 响应（public_view 形态，不含 Key）；source 增加 pool = 模型池生效 */
export interface LLMEffective {
  source: string;
  text: { provider: string; provider_label: string; base_url: string; model: string } | null;
  vision: { provider: string; provider_label: string; base_url: string; model: string } | null;
  embedding?: { provider: string; provider_label: string; base_url: string; model: string } | null;
  embedding_source?: string;
  /** V5.1 P1：三槽池现状（effective 走 get_current_user，访客也能拿到条数） */
  pools?: Partial<Record<"text" | "vision" | "embedding", LLMPoolStats>>;
}

/** /api/llm/test-default/{slot} 响应 */
export interface LLMTestResult {
  ok: boolean;
  err_type?: string;
  error_label?: string;
  error?: string;
  model?: string | null;
  provider_label?: string;
  latency_ms?: number;
}

/** 与 app/api/users.py:UserRow 对齐 */
export interface AdminUserRow {
  id: number;
  username: string;
  email: string | null;
  role: Role;
  is_active: boolean;
  expires_at?: string | null;
  tasks: number;
  created_at?: string | null;
}

/** /api/admin/stats 响应 */
export interface AdminStats {
  registered_users: number;
  active_guests: number;
  cleaned_24h: number;
  total_tasks: number;
}

/** /api/categories 响应（扁平列表，parent_id 组树） */
export interface CategoryNode {
  id: number;
  name: string;
  parent_id: number | null;
  task_count: number;
}

/* ===== M0 平台自身质量量化（质量看板，与 app/api/quality.py 对齐） ===== */

/** pytest 按文件分布的单文件统计 */
export interface QualityPytestFileStat {
  total: number;
  passed: number;
}

/** 单条 pytest 用例明细（聚合脚本透传，旧版 summary 无此字段） */
export interface QualityCase {
  file: string;
  name: string;
  param: string;
  outcome: string;
  duration_ms: number;
  /** 用例类型：api=接口测试（触达 HTTP 客户端）/ unit=单元测试（旧数据无此字段） */
  kind?: "api" | "unit" | "";
}

/** 聚合结果的 pytest 维度 */
export interface QualityPytest {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  pass_rate: number;
  duration_ms: number;
  coverage_pct: number;
  /** 接口测试 / 单元测试条数（按用例函数判定；旧数据无此字段） */
  api_cases?: number;
  unit_cases?: number;
  by_file: Record<string, QualityPytestFileStat>;
  cases?: QualityCase[];
  failures: { file: string; test: string; message: string }[];
  /** 各类型段最近采集时间（分段合并后 unit/api 时间可不同；旧数据无此字段） */
  segments?: { unit?: string; api?: string };
}

/** 单个 e2e 套件结果（run-e2e.mjs 产出） */
export interface QualityE2ESuite {
  name: string;
  outcome: string;
  duration_ms: number;
  error: string | null;
  /** 真实操作步骤（runner 从脚本 ✅/❌ 日志行解析），旧报告无此字段 */
  steps?: { ok: boolean; name: string }[];
  /** 该套件最近一次真实执行时间（部分运行合并后各套件可不同） */
  collected_at?: string;
}

/** 聚合结果的 e2e 维度 */
export interface QualityE2E {
  suites: QualityE2ESuite[];
  total: number;
  passed: number;
  pass_rate: number;
  duration_ms: number;
}

/** quality-summary.json 结构 */
export interface QualitySummary {
  pytest: QualityPytest;
  e2e: QualityE2E;
  generated_at: string;
  /** 是否全量运行（部分运行合并后 false） */
  full_run?: boolean;
}

/** GET /api/quality/summary 响应（尚未运行过时 exists=false） */
export interface QualitySummaryResp {
  exists: boolean;
  summary: QualitySummary | null;
  message?: string;
}

/** e2e 套件实时状态（运行进度轮询返回） */
export interface QualityRunSuiteState {
  name: string;
  status: "pending" | "running" | "passed" | "failed" | "error";
  duration_ms: number | null;
  steps_ok: number;
  steps_total: number;
  last_step: string;
}

/** GET /api/quality/run/status 响应 */
export interface QualityRunStatus {
  run_id: string | null;
  status: "idle" | "running" | "completed" | "failed";
  stage: string;
  message: string;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** 本次运行计划（V2：范围选择） */
  plan?: { unit: boolean; api: boolean; e2e: string[] | null } | null;
  /** pytest 阶段进度（0~100；阶段外 null） */
  pytest_percent?: number | null;
  pytest_done?: number | null;
  pytest_total_hint?: number | null;
  /** e2e 套件实时状态（仅 e2e 阶段及之后有值） */
  suites?: QualityRunSuiteState[];
  /** 最近输出行（环形缓冲，最多 12 条） */
  log_tail?: string[];
}

/** GET /api/quality/history 元素（趋势图数据点） */
export interface QualityHistoryPoint {
  ts: string;
  pytest_total: number;
  pass_rate: number;
  coverage_pct: number;
  e2e_total: number;
  e2e_passed: number;
  /** 部分运行标记（旧数据/全量运行无此字段） */
  partial?: boolean;
  /** 本次运行范围，如 ["pytest:api", "e2e"] */
  scopes?: string[];
}

/** 与 app/schemas/automation.py:TargetOut 对齐（M1 全链路：被测系统，M3 Drawer 自动化 Tab 复用） */
export interface TargetItem {
  id: string;
  name: string;
  base_url: string;
  auth_type: string;
  has_auth: boolean;
  created_at?: string | null;
}

/* ===== M3 自动化执行（与契约 2 / app/schemas/automation.py ExecutionRunOut 严格对齐） ===== */

export type ExecutionStatus = "pending" | "running" | "completed" | "failed";

/** report_json.summary（契约 2） */
export interface ExecutionSummary {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
}

/** report_json.cases[] 单条用例结果（契约 2；screenshot 相对 auto_dir，如 shots/test_tc_001.png） */
export interface ExecutionCase {
  case_id: string;
  node_id: string;
  title: string;
  outcome: "passed" | "failed" | "skipped";
  duration_ms: number;
  error: string | null;
  screenshot: string | null;
}

/** report_json.environment（契约 2） */
export interface ExecutionEnvironment {
  browser: string;
  base_url: string;
}

/** report_json 解析后的对象（契约 2） */
export interface ExecutionReport {
  summary: ExecutionSummary;
  cases: ExecutionCase[];
  environment: ExecutionEnvironment;
  /** M4 自愈：触发过自愈循环时存在（见 HealMeta） */
  heal?: HealMeta | null;
}

/** GET /api/tasks/{task_id}/executions 元素 & GET /api/executions/{run_id} 响应（契约 3）。
 *  report 为后端解析后的对象；pending/running 时为 null（running 只带 progress/total）。 */
export interface ExecutionRunOut {
  id: string;
  task_id: string;
  user_id: number;
  trigger: "manual" | "retry";
  status: ExecutionStatus;
  progress: number;
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  duration_ms: number;
  report: ExecutionReport | null;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** M4 自愈：已进行的自愈轮次（0=未触发；running 期间轮询可见递增） */
  heal_round?: number;
}

/* ===== M4 自愈循环（与 auto_runner._run_heal_flow 写入 report_json.heal 的结构对齐） ===== */

/** heal.log[].suspects[] 单个失败用例取证（auto_healer 落库只保留 case_id/error/screenshot） */
export interface HealSuspect {
  case_id: string;
  error: string;
  /** 失败截图相对任务目录路径（本版本仅路径，供人工核对） */
  screenshot: string;
}

/** heal.log[] 每轮自愈明细 */
export interface HealLogEntry {
  round: number;
  suspects: HealSuspect[];
  diagnosis: { root_cause: string | null; analysis: string };
  /** 本轮修复覆盖写回的脚本文件名（未修复为空数组） */
  changed_files: string[];
  /** passed / failed / skipped / error / diagnosis_failed / rejected_syntax_error:… / rejected_assert_change */
  rerun_outcome: string;
}

/** heal.suspected_bugs[] 疑似真缺陷/环境问题（不可修复收敛记录） */
export interface SuspectedBug {
  case_id: string;
  /** product_bug / env / exhausted */
  kind: string;
  reason: string;
}

/** report_json.heal（M4 自愈结果，存在即表示触发过自愈循环） */
export interface HealMeta {
  rounds: number;
  log: HealLogEntry[];
  suspected_bugs: SuspectedBug[];
}

/** POST run-auto / retry 响应（契约 3） */
export interface ExecutionStartResp {
  run_id: string;
  status: string;
}

/* ===== 页面探索可视化（与 web_crawler.PageDesc / GET /api/tasks/{id}/pages 对齐） ===== */

/** crawler 抓取的单页结构描述（pages.json 元素，前端只消费展示字段） */
export interface PageDescInfo {
  url: string;
  title: string;
  /** 截图相对任务目录路径（如 pages/page-001.png），空串 = 无截图 */
  screenshot?: string;
  rendered?: boolean;
  text_digest?: string;
}

/** GET /api/tasks/{task_id}/pages 响应（无产物时 pages 为空数组） */
export interface TaskPagesResp {
  task_id: string;
  pages: PageDescInfo[];
  /** 探索录屏可播标记（任务目录 videos/explore.webm 存在时为 true） */
  video_available?: boolean;
}



