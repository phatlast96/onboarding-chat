"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { voiceSocket, voiceSocketUrl, type Collected } from "../lib/api";

type TranscriptLine = {
  role: "user" | "assistant";
  text: string;
  open?: boolean;
};

type CallStageProps = {
  sessionId: string;
  leave: boolean;
  onCollected: (collected: Collected, graduated: boolean, pendingGmail: string | null) => void;
  onEnded: (message: string | null) => void;
};

let activeSocket: WebSocket | null = null;
let closeTimer = 0;
let micStream: MediaStream | null = null;
let sharedContext: AudioContext | null = null;
let micStarting = false;

function encodePcm(bytes: Uint8Array): string {
  let binary = "";
  const size = 0x8000;
  for (let index = 0; index < bytes.length; index += size) {
    binary += String.fromCharCode(...bytes.subarray(index, index + size));
  }
  return btoa(binary);
}

function releaseAudio() {
  micStream?.getTracks().forEach((track) => track.stop());
  micStream = null;
  void sharedContext?.close();
  sharedContext = null;
}

export function primeCallAudio() {
  if (!sharedContext || sharedContext.state === "closed") {
    try {
      sharedContext = new AudioContext({ sampleRate: 24000 });
    } catch {
      sharedContext = new AudioContext();
    }
  }
  void sharedContext.resume();
}

function formatElapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${minutes}:${rest.toString().padStart(2, "0")}`;
}

export function CallStage({ sessionId, leave, onCollected, onEnded }: CallStageProps) {
  const socketRef = useRef<WebSocket | null>(null);
  const onCollectedRef = useRef(onCollected);
  const onEndedRef = useRef(onEnded);
  const mutedRef = useRef(false);
  const endedRef = useRef(false);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const [muted, setMuted] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [micError, setMicError] = useState(false);

  const hangup = useCallback(() => {
    const socket = socketRef.current;
    if (endedRef.current) return;
    if (socket && socket.readyState === WebSocket.CONNECTING) {
      socket.addEventListener(
        "open",
        () => socket.send(JSON.stringify({ type: "hangup" })),
        { once: true },
      );
      return;
    }
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      endedRef.current = true;
      onEndedRef.current(null);
      return;
    }
    socket.send(JSON.stringify({ type: "hangup" }));
  }, []);

  useEffect(() => {
    onCollectedRef.current = onCollected;
    onEndedRef.current = onEnded;
  }, [onCollected, onEnded]);

  useEffect(() => {
    if (leave) hangup();
  }, [leave, hangup]);

  useEffect(() => {
    window.clearTimeout(closeTimer);
    let socket = activeSocket;
    if (!socket || socket.url !== voiceSocketUrl(sessionId) || socket.readyState === WebSocket.CLOSED) {
      if (socket && socket.readyState !== WebSocket.CLOSED) socket.close();
      releaseAudio();
      socket = voiceSocket(sessionId);
      activeSocket = socket;
    }
    socketRef.current = socket;

    const sources: AudioBufferSourceNode[] = [];
    const nextTime = { value: 0 };
    if (!sharedContext || sharedContext.state === "closed") {
      try {
        sharedContext = new AudioContext({ sampleRate: 24000 });
      } catch {
        sharedContext = new AudioContext();
      }
    }
    const context = sharedContext;
    void context.resume();

    function stopPlayback() {
      sources.forEach((source) => {
        try {
          source.stop();
        } catch {
          return;
        }
      });
      sources.length = 0;
      nextTime.value = 0;
    }

    function play(base64: string) {
      const binary = atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1) {
        bytes[index] = binary.charCodeAt(index);
      }
      const view = new DataView(bytes.buffer);
      const samples = Math.floor(bytes.length / 2);
      if (!samples) return;
      const buffer = context.createBuffer(1, samples, 24000);
      const data = buffer.getChannelData(0);
      for (let index = 0; index < samples; index += 1) {
        data[index] = view.getInt16(index * 2, true) / 0x8000;
      }
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      const start = Math.max(context.currentTime, nextTime.value);
      source.start(start);
      nextTime.value = start + buffer.duration;
      sources.push(source);
    }

    function onMessage(event: MessageEvent) {
      const data = JSON.parse(String(event.data)) as {
        type?: string;
        pcm16_base64?: string;
        role?: string;
        text?: string;
        partial?: boolean;
        collected?: Collected;
        pending_gmail?: string | null;
        graduated?: boolean;
        message?: string | null;
      };
      if (data.type === "audio" && data.pcm16_base64) {
        void context.resume();
        play(data.pcm16_base64);
      } else if (data.type === "drop") {
        stopPlayback();
      } else if (data.type === "transcript") {
        const role = data.role === "user" ? "user" : "assistant";
        const partial = Boolean(data.partial);
        const text = data.text ?? "";
        setLines((current) => {
          const last = current[current.length - 1];
          if (!text) {
            if (!last?.open) return current;
            const next = current.slice();
            next[next.length - 1] = { ...last, open: false };
            return next;
          }
          if (partial && last?.open && last.role === role) {
            const next = current.slice();
            next[next.length - 1] = { role, text: last.text + text, open: true };
            return next;
          }
          return [...current, { role, text, open: partial }];
        });
      } else if (data.type === "collected" && data.collected) {
        onCollectedRef.current(data.collected, Boolean(data.graduated), data.pending_gmail ?? null);
      } else if (data.type === "ended") {
        if (endedRef.current) return;
        endedRef.current = true;
        onEndedRef.current(data.message ?? null);
      }
    }

    socket.addEventListener("message", onMessage);

    if (!micStream && !micStarting) {
      micStarting = true;
      void navigator.mediaDevices
        .getUserMedia({ audio: { channelCount: 1, echoCancellation: true } })
        .then(async (stream) => {
          micStream = stream;
          await context.audioWorklet.addModule("/pcm-worklet.js");
          const source = context.createMediaStreamSource(stream);
          const worklet = new AudioWorkletNode(context, "pcm-processor");
          const silent = context.createGain();
          silent.gain.value = 0;
          worklet.port.onmessage = (workletEvent: MessageEvent<ArrayBuffer>) => {
            if (mutedRef.current || socket.readyState !== WebSocket.OPEN) return;
            socket.send(
              JSON.stringify({
                type: "audio",
                pcm16_base64: encodePcm(new Uint8Array(workletEvent.data)),
              }),
            );
          };
          source.connect(worklet);
          worklet.connect(silent);
          silent.connect(context.destination);
        })
        .catch(() => setMicError(true))
        .finally(() => {
          micStarting = false;
        });
    }

    const timer = window.setInterval(() => setElapsed((value) => value + 1), 1000);
    return () => {
      window.clearInterval(timer);
      socket.removeEventListener("message", onMessage);
      stopPlayback();
      closeTimer = window.setTimeout(() => {
        if (activeSocket !== socket) return;
        socket.close();
        activeSocket = null;
        releaseAudio();
      }, 200);
    };
  }, [sessionId]);

  useEffect(() => {
    const node = transcriptRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [lines]);

  function toggleMute() {
    setMuted((current) => {
      const next = !current;
      mutedRef.current = next;
      micStream?.getAudioTracks().forEach((track) => {
        track.enabled = !next;
      });
      return next;
    });
  }

  return (
    <div className="flex min-h-0 w-full flex-1 flex-col px-4">
      <div className="flex shrink-0 flex-col items-center gap-2 pt-4">
        <div className="relative grid size-16 place-items-center" aria-hidden="true">
          <span className="absolute inset-0 rounded-full border border-accent/40 motion-safe:animate-pulse motion-safe:[animation-duration:2.8s]" />
          <span className="size-10 rounded-full bg-accent/20" />
        </div>
        <p className="text-sm tabular-nums text-muted">{formatElapsed(elapsed)}</p>
        <p className="max-w-sm text-center text-sm leading-5 text-balance text-muted">
          {leave
            ? "Ending the call…"
            : "Hang up, or switch to Chat. You'll pick up whatever is still open."}
        </p>
      </div>
      <div
        ref={transcriptRef}
        role="log"
        aria-label="Call transcript"
        className="mt-3 flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto py-2"
      >
        {lines.length === 0 ? (
          <p className="text-center text-sm text-muted">Listening…</p>
        ) : (
          lines.map((line, index) => (
            <div
              key={`${line.role}-${index}`}
              className={`flex w-fit max-w-[85%] flex-col gap-1 ${line.role === "user" ? "ml-auto items-end" : ""}`}
            >
              {index === 0 ? <span className="px-1 text-[11px] text-muted">Call</span> : null}
              <p className="rounded-2xl border border-accent/40 bg-accent/10 px-3 py-2 text-sm leading-5 break-words text-ink">
                {line.text}
              </p>
            </div>
          ))
        )}
      </div>
      {micError ? (
        <p className="shrink-0 pb-2 text-center text-sm text-danger">
          Microphone unavailable. You can still hang up.
        </p>
      ) : null}
      <div className="flex shrink-0 justify-center gap-3 pt-2 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <button
          type="button"
          aria-pressed={muted}
          onClick={toggleMute}
          className="h-11 min-w-11 rounded-full border border-line px-4 text-sm transition-colors duration-150"
        >
          {muted ? "Unmute" : "Mute"}
        </button>
        <button
          type="button"
          onClick={hangup}
          className="h-11 min-w-11 rounded-full border border-danger px-4 text-sm text-danger transition-colors duration-150"
        >
          Hang up
        </button>
      </div>
    </div>
  );
}
