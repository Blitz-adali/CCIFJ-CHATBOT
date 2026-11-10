export type Locale = "en" | "fr" | "ja";
export type ApiMode = string;

/** A file sent inline; the server extracts its text for the agent. */
export interface AttachmentUpload {
  name: string;
  mime_type: string;
  data_base64: string;
}

export interface ChatRequest {
  session_id: string | null;
  message: string;
  locale: Locale;
  path_step_id?: string;
  path_event_id?: string;
  path_context?: Record<string, unknown>;
  attachments?: AttachmentUpload[];
}

export interface PathAction {
  id: string;
  label: string;
  event_id?: string;
  data?: Record<string, unknown>;
}

/** Agent-proposed button; clicking sends `prompt` as an ordinary user message. */
export interface NextAction {
  id: string;
  label: string;
  prompt: string;
  event_id?: number;
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
  is_staff?: boolean;
  /** guest | user | member | staff, decided by the backend. */
  access_level?: AccessLevel;
}

export type AccessLevel = 'guest' | 'user' | 'member' | 'staff';

export interface AdminOverview {
  users: number;
  members: number;
  staff: number;
  member_companies: number;
  open_requests: number;
  new_requests: number;
  upcoming_events: number;
  active_contacts: number;
  has_triage_contact: boolean;
  new_intro_requests: number;
  uncovered_topics: string[];
}

export interface AdminUser {
  user_id: number;
  name: string;
  email: string;
  role: 'user' | 'staff';
  access_level: AccessLevel;
  companies: string[];
  created_at: string;
}

export interface AdminEvent {
  event_id: number;
  title: string;
  description: string;
  category: string;
  location: string | null;
  contact: string | null;
  start_at: string | null;
  end_at: string | null;
  reg_deadline: string | null;
  member_price: number | null;
  non_member_price: number | null;
  price: number | null;
  currency: string | null;
  languages: string[];
}

export interface AdminMember {
  member_id: number;
  name: string;
  sector: string | null;
  category: string | null;
  country: string | null;
  region: string | null;
  manpower: number | null;
  description: string | null;
  contact: string | null;
  secondary_sectors?: string | null;
  language?: string | null;
  directory_visible?: boolean;
  contacts?: MemberContactPerson[];
  accounts: number;
}

/** A contact person at a member company, with their role and working language. */
export interface MemberContactPerson {
  name: string;
  salutation: string | null;
  job_title: string | null;
  email: string | null;
  language: string | null;
}

export interface ImportResult {
  kind: 'events' | 'members';
  committed: boolean;
  counts: { create: number; update: number; error: number };
  rows: { row: number; label: string; action: 'create' | 'update' | 'error'; errors: string[] }[];
}

export interface KnowledgeDoc {
  doc_id: number;
  title: string;
  filename: string;
  mime_type: string;
  source_url: string | null;
  char_count: number;
  chunk_count: number;
  created_at: string;
}

export interface MemberMatch {
  id: number;
  name: string;
  score: number;
  why: string;
  sector: string | null;
  region: string | null;
  country: string | null;
  description: string | null;
  contact: string | null;
}

export type IntroStatus = 'new' | 'proposed' | 'introduced' | 'declined' | 'closed';

export interface IntroCompany { member_id: number; name: string; sector: string | null; place: string; }

export interface IntroRequestItem {
  intro_id: number;
  reference: string;
  status: IntroStatus;
  message: string;
  staff_note: string | null;
  created_at: string;
  requester: { name: string; email: string } | null;
  requester_company: IntroCompany | null;
  target: IntroCompany | null;
}

export interface AutomationRule {
  key: string;
  title: string;
  description: string;
  enabled: boolean;
  requires_approval: boolean;
  staff_task: boolean;
}

export interface OutreachItem {
  outreach_id: number;
  member: string;
  email: string;
  rule: string;
  rule_title: string;
  channel: string;
  subject: string;
  body: string;
  reason: string;
  status: 'pending' | 'sent' | 'skipped';
  created_at: string;
  sent_at: string | null;
  read_at: string | null;
}

export interface RunResult {
  run_id: number | null;
  dry_run: boolean;
  as_of: string;
  created: number;
  duplicates: number;
  items: { outreach_id: number; user_id: number; member?: string; rule: string; status: string; channel: string; subject: string; reason: string }[];
}

export interface EngagementRow {
  user_id: number;
  name: string;
  email: string;
  company: string | null;
  joined_at: string | null;
  profile_completed: boolean;
  goals: string[];
  channel: string;
  last_activity: string;
  last_activity_kind: string;
  days_inactive: number;
  score: number;
  stage: string;
  messages_sent: number;
}

export interface MemberProfile {
  access_level: AccessLevel;
  joined_at: string | null;
  goals: string[];
  interests: string;
  preferred_language: Locale;
  preferred_channel: 'in_app' | 'email' | 'line';
  profile_completed: boolean;
  goal_options: { key: string; label: string }[];
}

export interface MemberNotification { id: number; subject: string; body: string; sent_at: string; read: boolean; }

export interface AnalyticsSummary {
  days: number;
  totals: { questions: number; conversations: number; replies: number };
  daily: { day: string; questions: number }[];
  audience: { guest: number; user: number; member: number; staff: number };
  topics: { topic: string; count: number }[];
  quality: { answers_with_sources: number | null; answers_with_fallback_warnings: number | null;
    median_response_seconds: number | null; p90_response_seconds: number | null };
  outcomes: { event_bookings: number; room_reservations: number; staff_requests: number; introduction_requests: number;
    onboarding_messages_sent: number; actions_proposed: number };
  recent_questions: { text: string; at: string; audience: string }[];
}

export interface LangsmithSummary {
  tracing: boolean;
  project: string;
  status: 'connected' | 'off' | 'auth_error' | 'no_project' | 'error';
  days: number;
  message?: string;
  chat_turns?: number;
  runs?: number;
  error_rate?: number | null;
  total_tokens?: number;
  total_cost?: number | null;
  median_latency_seconds?: number | null;
  p99_latency_seconds?: number | null;
  tool_sample?: number;
  top_tools?: { tool: string; calls: number }[];
}

export interface StaffTopic { key: string; label: string; description: string; }

export interface StaffContact {
  contact_id: number;
  name: string | null;
  role: string | null;
  mobile: string | null;
  email: string;
  category: string;
  sub_category: string | null;
  topics: string[];
  languages: string | null;
  is_triage: boolean;
  is_active: boolean;
}

export interface AuthSession {
  access_token: string;
  token_type: "bearer";
  expires_in?: number;
  user: AuthUser;
}

export interface ApprovalAction {
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
  chunk_ids?: string[];
  provenance?: 'retrieved' | 'database';
}

export interface EventCardData {
  event_id: number;
  title: string;
  category: string;
  location: string | null;
  start_at: string | null;
  end_at: string | null;
  reg_deadline: string | null;
  price: string | null;
  member_price: string | null;
  non_member_price: string | null;
  currency: string | null;
  registration_open: boolean | null;
}

export interface StaffRequestItem {
  reference: string;
  request_type: string;
  category: string;
  subject: string;
  message: string;
  sender_name: string;
  sender_email: string;
  recipients: string[];
  status: 'new' | 'in_review' | 'closed';
  created_at: string;
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
  session_id?: string | null;
  message_id?: string;
  mode?: ApiMode;
  /** Normalized from the backend's `reply` field by api.ts. */
  answer: string;
  attachment_count?: number;
  suggested_questions?: string[];
  intent?: ChatIntent | null;
  requires_clarification?: boolean;
  sources?: ChatSource[];
  events?: EventCardData[];
  actions?: ChatAction[];
  journey?: JourneyStep[];
  warnings?: string[];
  pending_approvals?: PendingApproval[];
  path_actions?: PathAction[];
  next_actions?: NextAction[];
  active_path_step?: string | null;
  path_context?: Record<string, unknown>;
  request_id?: string;
}

export interface ApiStatusResponse {
  status?: string;
  mode?: ApiMode;
  data_freshness?: string;
  version?: string;
  agent_ready?: boolean;
}
