import type { Collected } from "../lib/api";

type FactKey = "agent_name" | "user_name" | "gmail" | "help_with";

const CALL_FACTS = [
  ["user_name", "Your name"],
  ["gmail", "Gmail"],
  ["help_with", "Help with"],
] as const satisfies ReadonlyArray<readonly [FactKey, string]>;

const CHAT_FACTS = [["agent_name", "Assistant name"]] as const satisfies ReadonlyArray<
  readonly [FactKey, string]
>;

const FACTS = [...CALL_FACTS, ...CHAT_FACTS];
const TOTAL = FACTS.length + 1;

const CHANNEL_HINT =
  "A call collects your name, Gmail, and what you need help with. Naming the assistant stays in chat.";

type CollectedPanelProps = {
  collected: Collected;
  graduated: boolean;
  pendingGmail?: string | null;
};

function filledCount(collected: Collected): number {
  const facts = FACTS.filter(([key]) => collected[key]).length;
  return facts + (collected.gmail_connected ? 1 : 0);
}

function statusLabel(collected: Collected, graduated: boolean): string {
  const filled = filledCount(collected);
  const total = TOTAL;
  if (graduated && filled < total) return `Started early · ${filled} of ${total}`;
  if (graduated) return `Finished · ${filled} of ${total}`;
  return `${filled} of ${total} collected`;
}

function FactRow({ label, value, filled }: { label: string; value: string; filled: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="flex shrink-0 items-center gap-2 text-sm text-muted">
        <span
          className={`size-1.5 shrink-0 rounded-full ${filled ? "bg-accent" : "border border-muted"}`}
          aria-hidden="true"
        />
        {label}
      </dt>
      <dd className={`min-w-0 text-right text-sm break-words ${filled ? "text-ink" : "text-muted"}`}>
        {value}
      </dd>
    </div>
  );
}

function FactGroup({
  title,
  facts,
  collected,
  extra,
}: {
  title: string;
  facts: ReadonlyArray<readonly [FactKey, string]>;
  collected: Collected;
  extra?: { label: string; value: string; filled: boolean };
}) {
  return (
    <section>
      <h2 className="text-[11px] font-medium tracking-wide text-muted uppercase">{title}</h2>
      <dl className="mt-2 flex flex-col gap-2">
        {facts.map(([key, label]) => {
          const value = collected[key];
          return <FactRow key={key} label={label} value={value ?? "Still open"} filled={Boolean(value)} />;
        })}
        {extra ? <FactRow label={extra.label} value={extra.value} filled={extra.filled} /> : null}
      </dl>
    </section>
  );
}

export function CollectedPanel({ collected, graduated, pendingGmail }: CollectedPanelProps) {
  const shown = { ...collected, gmail: collected.gmail ?? pendingGmail ?? null };
  const early = graduated && filledCount(shown) < TOTAL;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-[15px] font-medium tracking-tight">Collected</p>
        <p className="mt-0.5 text-sm text-muted">{statusLabel(shown, graduated)}</p>
      </div>
      {graduated ? (
        <p className="rounded-2xl bg-accent/10 px-3 py-2 text-sm leading-5 text-ink">
          {early ? "Started early. What's still open can wait." : "Onboarding complete."}
        </p>
      ) : (
        <p className="text-sm leading-5 text-muted">{CHANNEL_HINT}</p>
      )}
      <FactGroup
        title="On a call"
        facts={CALL_FACTS}
        collected={shown}
        extra={{
          label: "Gmail connected",
          value: collected.gmail_connected ? "true" : "false",
          filled: collected.gmail_connected,
        }}
      />
      <FactGroup title="Chat only" facts={CHAT_FACTS} collected={shown} />
    </div>
  );
}

