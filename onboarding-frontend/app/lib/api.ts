export type Collected = {
  agent_name: string | null;
  user_name: string | null;
  gmail: string | null;
  help_with: string | null;
};

export type ChatMessage = {
  role: string;
  text: string;
};

export type SessionView = {
  id: string;
  collected: Collected;
  graduated: boolean;
  messages: ChatMessage[];
};

export type Turn = {
  message: string;
  collected: Collected;
  graduated: boolean;
};

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(await response.text());
  return response.json() as Promise<T>;
}

export function createSession(): Promise<SessionView> {
  return fetch("/backend/sessions", { method: "POST" }).then(read<SessionView>);
}

export function sendMessage(id: string, text: string): Promise<Turn> {
  return fetch(`/backend/sessions/${id}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  }).then(read<Turn>);
}

export function voiceSocketUrl(id: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.hostname}:8000/sessions/${id}/voice`;
}

export function voiceSocket(id: string): WebSocket {
  return new WebSocket(voiceSocketUrl(id));
}
