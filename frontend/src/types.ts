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
  created_at?: string | null;
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
  duration_ms?: number | null;
  input_summary?: string | null;
  output_summary?: string | null;
  error?: string | null;
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
  created_at?: string | null;
  finished_at?: string | null;
  steps: StepLog[];
  cases?: Record<string, unknown>[];
}

/** 聊天输入草稿（流式回复完成后「生成测试用例」消费） */
export interface ChatDraft {
  text: string;
  file?: File | null;
  kind: string;
  formats: string[];
}

