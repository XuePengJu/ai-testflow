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
