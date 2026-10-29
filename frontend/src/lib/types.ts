export type Role = "talker" | "hinter";
export type QuestionStatus =
  | "ready"
  | "legacy_pending"
  | "awaiting_user"
  | "unavailable"
  | "pending_review"
  | "generating"
  | "pending_confirmation"
  | "answered"
  | "rejected"
  | "failed"
  | "blocked"
  | "held"
  | "expired"
  | "interrupted"
  | "withdrawn";
// Call-only states extend the existing review workflow.
export type CallQuestionStatus = "held" | "expired" | "interrupted";
export interface AudioAsset {
  id: string;
  kind: string;
  status: string;
  filename?: string;
  language?: string;
  spoken_text?: string;
  voice?: string;
  content_type?: string;
  text_sha256?: string;
}
export interface AssistanceRequest {
  question_summary: string;
  information_needed: string;
  response_instruction: string;
}
export interface Question {
  assistance_request?: AssistanceRequest;
  id: string;
  display_no: string;
  text: string;
  status: QuestionStatus;
  version: number;
  duplicate_count: number;
  human_reviewed: boolean;
  created_at: string;
  answered_at?: string;
  call_id?: string;
  risk_reason?: string;
  decision?: string;
}
export interface AnswerPart {
  id: string;
  question_id: string;
  question: string;
  answer: string;
  status: "known" | "partial" | "unknown" | "clarify" | "refused" | "withdrawn";
  updated?: boolean;
}
export interface Message {
  id: string;
  type: "talker" | "hinter" | "ai" | "screening";
  text: string;
  status: string;
  created_at: string;
  question_ids?: string[];
  input_mode?: string;
  audio?: AudioAsset;
  error?: string;
  public_stream?: boolean;
  text_done?: boolean;
  audio_done?: boolean;
  refusal?: boolean;
  published_at?: string;
  revision?: number;
  call_id?: string;
  delivery?: string;
  human_reviewed?: boolean;
  turn_id?: string;
  transcription_status?: string;
  reply_kind?: "answer" | "supplement";
  reply_to_message_id?: string;
  is_answer_card?: boolean;
  answer_parts?: AnswerPart[];
  updating?: boolean;
  reply_summary?: { state: "collecting" | "updating" | "ready" | "stopped"; completed: number; total: number };
  transcription?: {
    raw_transcript: string;
    final_asr_text: string;
    final_asr_status: string;
    status: string;
    model: string;
    model_label?: string;
    route: string;
    silence_ms: number;
    final_asr_ms?: number;
    refiner_ms: number;
    finalization_ms: number;
    reason: string;
  };
}
export interface Answer {
  is_answer_card?: boolean;
  answer_parts?: AnswerPart[];
  updating?: boolean;
  id: string;
  text: string;
  status: string;
  question_ids: string[];
  tts_audio: AudioAsset;
  wiki_sources?: string[];
  public_stream?: boolean;
  text_done?: boolean;
  audio_done?: boolean;
  refusal?: boolean;
  revision?: number;
}
export interface WikiDocument {
  path: string;
  content: string;
  revision: string;
}
export interface CallAlert {
  id: string;
  question_id: string;
  text: string;
  reason: string;
  category: string;
  status: string;
  created_at: string;
}
export interface VoiceCall {
  id: string;
  status: string;
  started_at: string;
  ended_at?: string;
  epoch: number;
  paused: boolean;
  muted: boolean;
  error?: string;
  last_latency_ms?: number;
  endpoint_mode?: "adaptive" | "fixed_silence";
  recognition_context?: { version: number; terms: string[] };
  activity_state?: "listening" | "thinking";
  thinking_started_at?: string;
  thinking_budget_ms?: number;
  last_thinking_ms?: number;
  last_voice_to_send_ms?: number;
  pending_count?: number;
  queued_count?: number;
  alerts?: CallAlert[];
  constraints?: { id: string; text: string; scope: string }[];
  constraints_revision?: number;
}
export interface SessionSummary {
  id: string; talker_id: string; title: string; created_at: string; status: string;
  customer_name: string; message_count: number; pending_count: number; call_active: boolean;
}
export interface Talker {
  id: string; name: string; created_at: string; session_count: number;
  latest_session_id: string | null; sessions: SessionSummary[];
}
export interface Session {
  talker_id: string;
  talker_name: string;
  id: string;
  role: Role;
  user_id: string;
  customer_id: string;
  customer_name: string;
  title: string;
  language: string;
  created_at: string;
  last_sequence: number;
  sample_loaded: boolean;
  ai_mode?: boolean;
  automatic_paused?: boolean;
  response_flow_version?: number;
  guidance_projection?: string;
  guidance?: {
    id: string; text: string; kind: string; revision: number; revoked?: boolean;
    question_id?: string;
    binding?: { extracted_question: string; assistance_request: AssistanceRequest };
  }[];
  interaction_mode?: string;
  call?: VoiceCall;
  retraction_checks?: {
    id: string; status: string; checked_count: number; withdrawn_count: number;
    created_at: string; error?: string;
  }[];
  messages: Message[];
  questions: Question[];
  answers: Answer[];
  wiki: {
    owner_name: string;
    user: string;
    user_id: string;
    context: string;
    documents: { path: string; title: string }[];
  };
}
