"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { createSession, sendMessage, type Collected, type SessionView } from "../lib/api";
import { CallStage, primeCallAudio } from "./call-stage";
import { CollectedPanel } from "./collected-panel";

type Mode = "text" | "call";

export function OnboardingConsole() {
  const [session, setSession] = useState<SessionView | null>(null);
  const [mode, setMode] = useState<Mode>("text");
  const [leaveCall, setLeaveCall] = useState(false);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const callSessionId = useRef<string | null>(null);
  const collected = session?.collected;
  const graduated = Boolean(session?.graduated);
  const early = graduated && collected != null && Object.values(collected).some((value) => !value);

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
  }, [session?.messages.length, pending, busy, mode]);

  function choose(next: Mode) {
    if (next === mode || !session) return;
    if (next === "call") {
      primeCallAudio();
      callSessionId.current = session.id;
      setLeaveCall(false);
      setMode("call");
      return;
    }
    setLeaveCall(true);
  }

  function handleEnded(message: string | null) {
    const id = callSessionId.current;
    callSessionId.current = null;
    setLeaveCall(false);
    setMode("text");
    if (!message || !id) return;
    setSession((current) =>
      current && current.id === id
        ? {
            ...current,
            messages: [...current.messages, { role: "assistant", text: message }],
          }
        : current,
    );
  }

  function handleCollected(next: Collected, nextGraduated: boolean) {
    const id = callSessionId.current;
    setSession((current) =>
      current && current.id === id ? { ...current, collected: next, graduated: nextGraduated } : current,
    );
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !session || busy) return;
    setBusy(true);
    setDraft("");
    setPending(text);
    setError(null);
    try {
      const turn = await sendMessage(session.id, text);
      setSession((current) =>
        current && current.id === session.id
          ? {
              ...current,
              collected: turn.collected,
              graduated: turn.graduated,
              messages: [
                ...current.messages,
                { role: "user", text },
                { role: "assistant", text: turn.message },
              ],
            }
          : current,
      );
    } catch {
      setSession((current) =>
        current && current.id === session.id
          ? {
              ...current,
              messages: [
                ...current.messages,
                { role: "user", text },
                { role: "assistant", text: "I missed that. Say it once more?" },
              ],
            }
          : current,
      );
    } finally {
      setPending(null);
      setBusy(false);
    }
  }

  async function reset() {
    callSessionId.current = null;
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
  const onCall = mode === "call" && session != null;

  const showFooter = Boolean((error && session) || (graduated && mode === "text") || showComposer);

  return (
    <div className="flex h-full flex-col bg-background text-ink">
      <header className="mx-auto flex h-16 w-full max-w-5xl shrink-0 items-center justify-between gap-3 border-b border-line px-4">
        <p className="min-w-0 truncate text-[15px] font-medium tracking-tight">Persona</p>
        <div className="flex shrink-0 items-center gap-1">
          <div role="group" aria-label="Channel" className="flex rounded-full border border-line bg-surface p-1">
            <button
              type="button"
              aria-pressed={mode === "text"}
              onClick={() => choose("text")}
              className={`h-11 rounded-full px-3 text-sm transition-colors duration-150 ${
                mode === "text" ? "bg-accent text-background" : "text-muted"
              }`}
            >
              Chat
            </button>
            <button
              type="button"
              aria-pressed={mode === "call"}
              onClick={() => choose("call")}
              disabled={!session}
              className={`h-11 rounded-full px-3 text-sm transition-colors duration-150 disabled:opacity-40 ${
                mode === "call" ? "bg-accent text-background" : "text-muted"
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
                    <p className="text-sm leading-5">Say hello, or place a call.</p>
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
                {session?.messages.map((message, index) => (
                  <p
                    key={`${message.role}-${index}`}
                    className={`w-fit max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-5 break-words whitespace-pre-wrap ${
                      message.role === "user"
                        ? "ml-auto bg-accent/15 text-ink"
                        : "border border-line bg-surface text-ink"
                    }`}
                  >
                    {message.text}
                  </p>
                ))}
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
            <CollectedPanel collected={session.collected} graduated={graduated} />
          ) : (
            <p className="text-sm text-muted">Starting a session…</p>
          )}
        </aside>
      </div>
    </div>
  );
}
