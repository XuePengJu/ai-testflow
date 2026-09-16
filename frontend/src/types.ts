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
}

/** 与 app/schemas/task.py:StepLogOut 对齐 */
export interface StepLog {
  name: string;
  title: string;
  status: string;
  /** running 期间实时子进度（如"正在为第 2/5 个测试点生成用例…"） */
  progress?: string | null;
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
  /** 「深度思考」开关（默认开）：关掉则不请求模型思考，也不展示思考面板 */
  thinking?: boolean;
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

/** 与 app/schemas/llm_config.py:LLMConfigOut 对齐 */
export interface LLMConfigRow {
  slot: string;
  provider: string;
  base_url: string;
  model: string;
  /** 如 ****abcd；空 = 未配置 Key */
  api_key_masked: string;
}

/** /api/llm/effective 响应（public_view 形态，不含 Key） */
export interface LLMEffective {
  source: string;
  text: { provider: string; provider_label: string; base_url: string; model: string } | null;
  vision: { provider: string; provider_label: string; base_url: string; model: string } | null;
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

