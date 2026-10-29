import type { Role } from "./types";

let token = "";
let loginVersion = 0;
export const getToken = () => token;

let pendingLogin: { key: string; promise: Promise<void> } | null = null;

export function login(role: Role, talkerId?: string): Promise<void> {
  const owner = role === "talker" ? talkerId : undefined;
  const key = `${role}:${owner || ""}`;
  if (pendingLogin?.key === key) return pendingLogin.promise;
  const version = ++loginVersion;
  const promise = (async () => {
    const response = await fetch("/api/v1/auth/demo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role, talker_id: owner }),
    });
    if (!response.ok) throw new Error("后端暂时不可用，请稍后重试。");
    const nextToken = (await response.json()).token;
    if (version === loginVersion) token = nextToken;
  })().finally(() => {
    if (pendingLogin?.promise === promise) pendingLogin = null;
  });
  pendingLogin = { key, promise };
  return promise;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const form = options.body instanceof FormData;
  const response = await fetch("/api/v1" + path, {
    ...options,
    headers: {
      Authorization: "Bearer " + token,
      ...(!form && options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(
      error.detail?.message || error.detail || `请求失败 (${response.status})`,
    );
  }
  return response.json();
}

export const post = <T>(path: string, data: unknown = {}) =>
  api<T>(path, {
    method: "POST",
    body: JSON.stringify(data),
    headers: { "Idempotency-Key": crypto.randomUUID() },
  });
