export type Collected = {
  agent_name: string | null;
  user_name: string | null;
  gmail: string | null;
  gmail_connected: boolean;
  help_with: string | null;
};

export type ChatMessage = {
  role: string;
  text: string;
  channel?: "text" | "call";
};

export type SessionView = {
  id: string;
  collected: Collected;
  pending_gmail: string | null;
  graduated: boolean;
  ringing: boolean;
  messages: ChatMessage[];
};

export type Turn = {
  message: string;
  collected: Collected;
  pending_gmail: string | null;
  graduated: boolean;
  place_call: boolean;
  ringing: boolean;
};

const DEFAULT_BACKEND_URL = "http://127.0.0.1:8000";

function backendUrl(): string {
  return (process.env.NEXT_PUBLIC_BACKEND_URL ?? DEFAULT_BACKEND_URL).replace(/\/$/, "");
}

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

export function createSession(): Promise<SessionView> {
  return fetch(`${backendUrl()}/sessions`, { method: "POST" }).then(read<SessionView>);
}

export function getSession(id: string): Promise<SessionView> {
  return fetch(`${backendUrl()}/sessions/${id}`).then(read<SessionView>);
}

export function connectGmail(id: string): Promise<SessionView> {
  return fetch(`${backendUrl()}/sessions/${id}/gmail`, { method: "POST" }).then(
    read<SessionView>,
  );
}

export function declineCall(id: string): Promise<Turn> {
  return fetch(`${backendUrl()}/sessions/${id}/call/decline`, { method: "POST" }).then(
    read<Turn>,
  );
}

export function sendMessage(id: string, text: string): Promise<Turn> {
  return fetch(`${backendUrl()}/sessions/${id}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  }).then(read<Turn>);
}

export function voiceSocketUrl(id: string): string {
  const url = new URL(backendUrl());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `/sessions/${id}/voice`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

export function voiceSocket(id: string): WebSocket {
  return new WebSocket(voiceSocketUrl(id));
}
