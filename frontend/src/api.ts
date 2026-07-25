// Thin API client. All requests are same-origin with cookie credentials.

export interface Me {
  authenticated: boolean;
  username?: string;
  is_admin?: boolean;
  email?: string;
  signup_open: boolean;
  first_run: boolean;
}

export interface Command {
  name: string;
  agent: string;
  summary: string;
  usage: string;
  example: string;
  needs_args: boolean;
}

export interface Account {
  id: number;
  username: string;
  email?: string;
  is_admin: number;
  created_at: string;
}

export interface TraceEvent {
  agent: string;
  kind: string;
  name: string;
  args: string;
  status: string;
}

export interface Download {
  name: string;
  download_name?: string;
  size: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}

export interface StoredMessage {
  role: "user" | "assistant";
  content: string;
  trace: TraceEvent[];
  downloads: Download[];
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    throw new ApiError(res.status, data.detail || data.error || `Request failed (${res.status})`);
  }
  return data as T;
}

export const api = {
  me: () => request<Me>("/api/auth/me"),
  login: (username: string, password: string) =>
    request<{ username: string; is_admin: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  signup: (username: string, password: string, email: string, code: string) =>
    request<{ username: string; is_admin: boolean }>("/api/auth/signup", {
      method: "POST",
      body: JSON.stringify({ username, password, email, code }),
    }),
  logout: () => request("/api/auth/logout", { method: "POST", body: "{}" }),
  changePassword: (current_password: string, new_password: string) =>
    request("/api/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password, new_password }),
    }),
  setRecoveryEmail: (email: string) =>
    request<{ status: string; email: string }>("/api/auth/email", {
      method: "POST",
      body: JSON.stringify({ email }),
    }),
  forgotPassword: (identifier: string) =>
    request<{ message: string }>("/api/auth/forgot", {
      method: "POST",
      body: JSON.stringify({ identifier }),
    }),
  resetPassword: (token: string, new_password: string) =>
    request<{ username: string }>("/api/auth/reset", {
      method: "POST",
      body: JSON.stringify({ token, new_password }),
    }),
  meta: () => request<{ commands: Command[] }>("/api/meta"),
  listConversations: () =>
    request<{ conversations: ConversationSummary[] }>("/api/conversations"),
  createConversation: () =>
    request<ConversationSummary>("/api/conversations", { method: "POST", body: "{}" }),
  getConversation: (id: string) =>
    request<{ id: string; messages: StoredMessage[] }>(
      `/api/conversations/${encodeURIComponent(id)}`,
    ),
  deleteConversation: (id: string) =>
    request(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" }),
  listAccounts: () => request<{ accounts: Account[] }>("/api/accounts"),
  createAccount: (username: string, password: string, is_admin: boolean) =>
    request<Account>("/api/accounts", {
      method: "POST",
      body: JSON.stringify({ username, password, is_admin }),
    }),
  deleteAccount: (username: string) =>
    request(`/api/accounts/${encodeURIComponent(username)}`, { method: "DELETE" }),
  upload: async (file: File) => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/upload", { method: "POST", credentials: "include", body });
    const data = await res.json();
    if (!res.ok) throw new ApiError(res.status, data.detail || "Upload failed");
    return data as { name: string; path: string; size: string; readable: boolean; note: string };
  },
};

export type StreamFrame =
  | { type: "start"; conversation_id: string }
  | { type: "ping" }
  | { type: "trace"; event: TraceEvent }
  | { type: "done"; conversation_id: string; answer: string; trace: TraceEvent[]; downloads: Download[] }
  | { type: "error"; conversation_id?: string; error: string; error_id?: string };

// Stream a chat turn, yielding each SSE frame as it arrives.
export async function* streamChat(
  message: string,
  files: string[],
  conversationId: string,
  signal: AbortSignal,
): AsyncGenerator<StreamFrame> {
  const res = await fetch("/api/chat/stream", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, files, conversation_id: conversationId }),
    signal,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* keep the default */
    }
    throw new ApiError(res.status, detail);
  }
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (line) yield JSON.parse(line.slice(6)) as StreamFrame;
    }
  }
}
