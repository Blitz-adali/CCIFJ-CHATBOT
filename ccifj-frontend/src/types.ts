export type Locale = "en" | "fr" | "ja";
export type ApiMode = string;

export interface ChatRequest {
  request_id?: string;
  session_id: string | null;
  message: string;
  locale: Locale;
}

export interface ChatIntent {
  code: string;
  confidence?: number;
  evidence_terms?: string[];
}

export interface AuthUser {
  user_id: number;
  name: string;
  email: string;
}

export interface AuthSession {
  expires_in?: number;
  expires_at?: number;
  access_token: string;
  token_type: "bearer";
  user: AuthUser;
}

export interface ApprovalAction {
  description?: string;
  name: string;
  args: Record<string, unknown>;
}

export interface PendingApproval {
  interrupt_id: string;
  actions: ApprovalAction[];
}

export interface ChatSource {
  id?: string;
  title: string;
  url: string;
  snippet?: string;
  locale?: Locale | string;
  last_checked?: string;
}

export interface ActionChoice {
  label: string;
  value?: string;
  description?: string;
}

export type ActionType = "external_link" | "clarify" | "staff_handoff" | "guided_form" | string;

export interface ChatAction {
  id?: string;
  type: ActionType;
  label: string;
  description?: string;
  url?: string;
  status?: string;
  choices?: ActionChoice[];
}

export interface JourneyStep {
  stage?: string;
  status?: string;
  summary?: string;
}

export interface ChatResponse {
  reply?: string;
  session_id?: string | null;
  message_id?: string;
  mode?: ApiMode;
  answer: string;
  suggested_questions?: string[];
  intent?: ChatIntent | null;
  requires_clarification?: boolean;
  sources?: ChatSource[];
  actions?: ChatAction[];
  journey?: JourneyStep[];
  warnings?: string[];
  pending_approvals?: PendingApproval[];
  request_id?: string;
}

export interface ApiStatusResponse {
  status?: string;
  mode?: ApiMode;
  data_freshness?: string;
  version?: string;
}


export interface ChatHistory {
  last_request_id: string | null;
  session_id: string;
  messages: { message_id: number; role: "user" | "assistant" | "tool"; content: string; created_at: string; response?: ChatResponse }[];
  next_after_id: number | null;
  pending_approvals: PendingApproval[];
  turn_status: "ready" | "processing" | "unknown";
}
