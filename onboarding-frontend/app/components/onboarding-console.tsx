"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  connectGmail,
  createSession,
  declineCall,
  getSession,
  sendMessage,
  type ChatMessage,
  type Collected,
  type SessionView,
} from "../lib/api";
import { CallStage, primeCallAudio } from "./call-stage";
import { CollectedPanel } from "./collected-panel";

type Mode = "text" | "call";

function bubbleLabel(messages: ChatMessage[], index: number): string | null {
  const channel = messages[index]?.channel === "call" ? "call" : "text";
  const previous = messages[index - 1];
  if (!previous) return channel === "call" ? "Call" : null;
  const previousChannel = previous.channel === "call" ? "call" : "text";
  if (previousChannel === channel) return null;
  return channel === "call" ? "Call" : "Chat";
}

export function OnboardingConsole() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [mode, setMode] = useState<Mode>("text");
  const [leaveCall, setLeaveCall] = useState(false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [linking, setLinking] = useState(false);
  const [incoming, setIncoming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const callSessionId = useRef<string | null>(null);
  const offeredByBot = useRef(false);
  const collected = session?.collected;
  const graduated = Boolean(session?.graduated);
  const early =
    graduated &&
    collected != null &&
    ((["agent_name", "user_name", "gmail", "help_with"] as const).some((key) => !collected[key]) ||
      !collected.gmail_connected);

  useEffect(() => {
    let cancel = false;
    createSession()
      .then((next) => {
        if (cancel) return;
        setSession(next);
        setError(null);
      })
      .catch(() => {
        if (!cancel) setError("Couldn't start a session.");
      });
    return () => {
      cancel = true;
    };
  }, []);

  useEffect(() => {
    const node = scroller.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [session?.messages.length, pending, busy, mode, incoming]);

  function beginCall(id: string) {
    primeCallAudio();
    callSessionId.current = id;
    setIncoming(false);
    setLeaveCall(false);
    setMode("call");
  }

  function choose(next: Mode) {
    if (!session) return;
    if (next === "call") {
      if (mode === "call") return;
      setIncoming(true);
      return;
    }
    if (mode === "call") {
      setLeaveCall(true);
      return;
    }
    if (incoming) void decline();
  }

  function pickup() {
    if (!session) return;
    offeredByBot.current = false;
    beginCall(session.id);
  }

  async function handleEnded(message: string | null) {
    const id = callSessionId.current;
    callSessionId.current = null;
    setLeaveCall(false);
    setMode("text");
    if (!id) return;
    try {
      const next = await getSession(id);
      setSession(next);
      if (next.ringing) {
        offeredByBot.current = true;
        setIncoming(true);
      }
    } catch {
      if (!message) return;
      setSession((current) =>
        current && current.id === id
          ? {
              ...current,
              messages: [...current.messages, { role: "assistant", text: message, channel: "text" }],
            }
          : current,
      );
    }
  }

  function handleCollected(next: Collected, nextGraduated: boolean, pendingGmail: string | null) {
    const id = callSessionId.current;
    setSession((current) =>
      current && current.id === id
        ? { ...current, collected: next, pending_gmail: pendingGmail, graduated: nextGraduated }
        : current,
    );
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !session || busy) return;
    const id = session.id;
    setIncoming(false);
    setBusy(true);
    setDraft("");
    setPending(text);
    setError(null);
    try {
      const turn = await sendMessage(id, text);
      setSession((current) =>
        current && current.id === id
          ? {
              ...current,
              collected: turn.collected,
              pending_gmail: turn.pending_gmail,
              graduated: turn.graduated,
              messages: [
                ...current.messages,
                { role: "user", text, channel: "text" },
                { role: "assistant", text: turn.message, channel: "text" },
              ],
            }
          : current,
      );
      if (turn.place_call) {
        offeredByBot.current = true;
        setIncoming(true);
      }
    } catch {
      setSession((current) =>
        current && current.id === id
          ? {
              ...current,
              messages: [
                ...current.messages,
                { role: "user", text, channel: "text" },
                { role: "assistant", text: "I missed that. Say it once more?", channel: "text" },
              ],
            }
          : current,
      );
    } finally {
      setPending(null);
      setBusy(false);
    }
  }

  async function decline() {
    const fromBot = offeredByBot.current;
    offeredByBot.current = false;
    setIncoming(false);
    if (!fromBot || !session || busy) return;
    const id = session.id;
    setBusy(true);
    setError(null);
    try {
      const turn = await declineCall(id);
      setSession((current) =>
        current && current.id === id
          ? {
              ...current,
              collected: turn.collected,
              pending_gmail: turn.pending_gmail,
              graduated: turn.graduated,
              ringing: turn.ringing,
              messages: [...current.messages, { role: "assistant", text: turn.message, channel: "text" }],
            }
          : current,
      );
    } catch {
      setError("Couldn't stay in chat.");
    } finally {
      setBusy(false);
    }
  }

  async function linkGmail() {
    if (!session || linking) return;
    setLinking(true);
    setError(null);
    try {
      setSession(await connectGmail(session.id));
    } catch {
      setError("Couldn't connect Gmail.");
    } finally {
      setLinking(false);
    }
  }

  async function reset() {
    offeredByBot.current = false;
    callSessionId.current = null;
    setIncoming(false);
    setLeaveCall(false);
    setMode("text");
    setDraft("");
    setPending(null);
    setBusy(true);
    setError(null);
    try {
      setSession(await createSession());
    } catch {
      setError("Couldn't start a new session.");
    } finally {
      setBusy(false);
    }
  }

  const showComposer = mode === "text" && !graduated;
  const showConnect = Boolean(session?.pending_gmail && mode === "text");
  const showIncoming = incoming && mode === "text";
  const onCall = mode === "call" && session != null;

  const showFooter = Boolean(
    (error && session) || (graduated && mode === "text") || showComposer || showConnect || showIncoming,
  );

  return (
    <div className="flex h-full flex-col bg-background text-ink">
      <header className="mx-auto flex h-16 w-full max-w-5xl shrink-0 items-center justify-between gap-3 border-b border-line px-4">
        <p className="min-w-0 truncate text-[15px] font-medium tracking-tight">Onboarding Bot</p>
        <div className="flex shrink-0 items-center gap-1">
          <div role="group" aria-label="Channel" className="flex rounded-full border border-line bg-surface p-1">
            <button
              type="button"
              aria-pressed={mode === "text" && !incoming}
              onClick={() => choose("text")}
              className={`h-11 rounded-full px-3 text-sm transition-colors duration-150 ${
                mode === "text" && !incoming ? "bg-accent text-background" : "text-muted"
              }`}
            >
              Chat
            </button>
            <button
              type="button"
              aria-pressed={mode === "call" || incoming}
              onClick={() => choose("call")}
              disabled={!session}
              className={`h-11 rounded-full px-3 text-sm transition-colors duration-150 disabled:opacity-40 ${
                mode === "call" || incoming ? "bg-accent text-background" : "text-muted"
              }`}
            >
              Call
            </button>
          </div>
          <button
            type="button"
            onClick={() => void reset()}
            disabled={!session || busy}
            className="h-11 px-2 text-sm text-muted transition-colors duration-150 disabled:opacity-40"
          >
            Reset
          </button>
        </div>
      </header>
      <div className="console-grid mx-auto min-h-0 w-full max-w-5xl flex-1">
        <section className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          {onCall ? (
            <CallStage
              sessionId={session.id}
              leave={leaveCall}
              onCollected={handleCollected}
              onEnded={handleEnded}
            />
          ) : (
            <div
              ref={scroller}
              role="log"
              aria-label="Conversation"
              className="min-h-0 flex-1 overflow-y-auto px-4"
            >
              <div className="flex flex-col gap-2 py-4">
                {!session && !error ? <p className="text-sm text-muted">Starting a session…</p> : null}
                {error && !session ? (
                  <div>
                    <p className="text-sm text-ink">{error}</p>
                    <button
                      type="button"
                      onClick={() => void reset()}
                      className="mt-3 h-11 text-sm text-accent"
                    >
                      Try again
                    </button>
                  </div>
                ) : null}
                {session && session.messages.length === 0 && !pending ? (
                  <div className="flex flex-col items-start gap-3">
                    <p className="text-sm leading-5">Say hello.</p>
                    <p className="max-w-sm text-sm leading-5 text-muted">
                      Hang up, refuse, or jump ahead. Chat picks up whatever the call didn&apos;t finish.
                    </p>
                    <button
                      type="button"
                      onClick={() => choose("call")}
                      className="h-11 w-fit rounded-full bg-accent px-4 text-sm text-background transition-colors duration-150"
                    >
                      Place a call
                    </button>
                  </div>
                ) : null}
                {session?.messages.map((message, index) => {
                  const call = message.channel === "call";
                  const mine = message.role === "user";
                  const label = bubbleLabel(session.messages, index);
                  return (
                    <div
                      key={`${message.channel ?? "text"}-${message.role}-${index}`}
                      className={`flex w-fit max-w-[85%] flex-col gap-1 ${mine ? "ml-auto items-end" : ""}`}
                    >
                      {label ? <span className="px-1 text-[11px] text-muted">{label}</span> : null}
                      <p
                        className={`rounded-2xl px-3 py-2 text-sm leading-5 break-words whitespace-pre-wrap ${
                          call
                            ? "border border-accent/40 bg-accent/10 text-ink"
                            : mine
                              ? "bg-accent/15 text-ink"
                              : "border border-line bg-surface text-ink"
                        }`}
                      >
                        {message.text}
                      </p>
                    </div>
                  );
                })}
                {pending ? (
                  <p className="ml-auto w-fit max-w-[85%] rounded-2xl bg-accent/15 px-3 py-2 text-sm leading-5 break-words text-ink">
                    {pending}
                  </p>
                ) : null}
                {busy ? (
                  <p
                    aria-label="Assistant is replying"
                    className="flex h-9 w-fit items-center gap-1 rounded-2xl border border-line bg-surface px-3"
                  >
                    <span className="size-1.5 rounded-full bg-muted motion-safe:animate-pulse" />
                    <span className="size-1.5 rounded-full bg-muted motion-safe:animate-pulse [animation-delay:150ms]" />
                    <span className="size-1.5 rounded-full bg-muted motion-safe:animate-pulse [animation-delay:300ms]" />
                  </p>
                ) : null}
              </div>
            </div>
          )}
          {showFooter ? (
            <div className="shrink-0 px-4 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
              {error && session ? <p className="mb-2 text-sm text-danger">{error}</p> : null}
              {graduated && mode === "text" ? (
                <div className="mb-2 rounded-2xl border border-line bg-surface px-4 py-3">
                  <p className="text-sm">{early ? "You're in early." : "You're in."}</p>
                  {collected?.help_with ? (
                    <p className="mt-1 text-sm leading-5 break-words text-muted">{collected.help_with}</p>
                  ) : null}
                </div>
              ) : null}
              {showIncoming ? (
                <div className="mb-2 rounded-2xl border border-accent/40 bg-accent/10 px-4 py-3">
                  <p className="text-sm">Incoming call</p>
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      onClick={pickup}
                      className="h-11 flex-1 rounded-full bg-accent text-sm text-background"
                    >
                      Pick up
                    </button>
                    <button
                      type="button"
                      onClick={() => void decline()}
                      disabled={busy}
                      className="h-11 flex-1 rounded-full border border-line bg-surface text-sm disabled:opacity-40"
                    >
                      Don&apos;t pick up
                    </button>
                  </div>
                </div>
              ) : null}
              {showConnect ? (
                <button
                  type="button"
                  onClick={() => void linkGmail()}
                  disabled={linking}
                  className="mb-2 flex min-h-14 w-full flex-col items-center justify-center rounded-2xl bg-accent px-4 py-3 text-background transition-colors duration-150 disabled:opacity-40"
                >
                  <span className="text-sm font-medium">Connect Gmail</span>
                  <span className="max-w-full truncate text-xs opacity-80">{session?.pending_gmail}</span>
                </button>
              ) : null}
              {showComposer ? (
                <form onSubmit={(event) => void send(event)} className="flex items-center gap-2">
                  <input
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="Message"
                    aria-label="Message"
                    autoComplete="off"
                    disabled={!session}
                    className="h-11 min-w-0 flex-1 rounded-full border border-line bg-surface px-4 text-sm text-ink outline-none placeholder:text-muted"
                  />
                  {draft.trim() ? (
                    <button
                      type="submit"
                      disabled={busy}
                      className="h-11 shrink-0 rounded-full bg-accent px-4 text-sm text-background transition-colors duration-150 disabled:opacity-40"
                    >
                      Send
                    </button>
                  ) : null}
                </form>
              ) : null}
            </div>
          ) : null}
        </section>
        <aside className="overflow-y-auto border-l border-line px-4 py-4">
          {session ? (
            <CollectedPanel
              collected={session.collected}
              graduated={graduated}
              pendingGmail={session.pending_gmail}
            />
          ) : (
            <p className="text-sm text-muted">Starting a session…</p>
          )}
        </aside>
      </div>
    </div>
  );
}
