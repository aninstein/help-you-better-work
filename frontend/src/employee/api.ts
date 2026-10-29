export type Account = { user: { id: string; display_name: string; phone: string | null; email: string | null; version: number; status: string }; roles: string[]; capabilities: string[]; must_change_password: boolean; default_workspace_id: string; csrf_token: string };
export type Workspace = { id: string; name: string; state: string; owner_user_id: string | null; legacy_id?: string };
export type Thread = { id: string; title: string; workspace_id: string; created_at: number };
export type RecordItem = { id: string; seq: number; role: string; kind: string; text: string; visibility: string; revision: number; withdrawn: boolean; created_at: number; metadata: {run_id?:string} };
export type Run = { id: string; status: string; record_id: string; error_code?: string; created_at?: number; attempt: number };
export type RunEvent = { id: string; seq: number; type: string; payload: { text?: string; name?: string; result?: string; error_code?: string } };
export type Projection = { record_id: string; status: string; items: { text: string; source_record_id: string; source_revision: number }[] };
export type UserRow = Account['user'] & { roles: string[]; created_at: number };
let csrf = '';
let expired: (() => void) | undefined;
export function setSession(account: Account | null) { csrf = account?.csrf_token || ''; }
export function onExpired(callback: () => void) { expired = callback; }
export class ApiError extends Error { constructor(public status: number, public code: string, message: string) { super(message); } }
export async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const form = options.body instanceof FormData;
  let response: Response;
  try {
    response = await fetch('/api/v1' + path, { credentials: 'same-origin', ...options, headers: { ...(!form && options.body ? {'Content-Type':'application/json'} : {}), ...(csrf ? {'X-CSRF-Token': csrf} : {}), ...options.headers } });
  } catch { throw new ApiError(0, 'NETWORK_ERROR', '连接中断，内容已保留，请检查网络后重试。'); }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.detail;
    if (response.status === 401 && !path.startsWith('/auth/')) expired?.();
    throw new ApiError(response.status, detail?.code || 'REQUEST_FAILED', typeof detail === 'string' ? detail : detail?.message || '请求未完成，请稍后重试。');
  }
  return response.json();
}
export const send = <T>(path: string, body: unknown, method = 'POST', key?: string) => request<T>(path, { method, body: JSON.stringify(body), ...(key ? {headers:{'Idempotency-Key':key}} : {}) });
export const terminal = (status: string) => ['completed','failed','cancelled'].includes(status);
