import type { Collected } from "../lib/api";

const CALL_FACTS = [
  ["user_name", "Your name"],
  ["gmail", "Gmail"],
  ["help_with", "Help with"],
] as const;

const CHAT_FACTS = [["agent_name", "Assistant name"]] as const;

const FACTS = [...CALL_FACTS, ...CHAT_FACTS];

const CHANNEL_HINT =
  "A call collects your name, Gmail, and what you need help with. Naming the assistant stays in chat.";

type CollectedPanelProps = {
  collected: Collected;
  graduated: boolean;
};

function filledCount(collected: Collected): number {
  return FACTS.filter(([key]) => collected[key]).length;
}

function statusLabel(collected: Collected, graduated: boolean): string {
  const filled = filledCount(collected);
  const total = FACTS.length;
  if (graduated && filled < total) return `Started early · ${filled} of ${total}`;
  if (graduated) return `Finished · ${filled} of ${total}`;
  return `${filled} of ${total} collected`;
}

function FactGroup({
  title,
  facts,
  collected,
}: {
  title: string;
  facts: ReadonlyArray<readonly [keyof Collected, string]>;
  collected: Collected;
}) {
  return (
    <section>
      <h2 className="text-[11px] font-medium tracking-wide text-muted uppercase">{title}</h2>
      <dl className="mt-2 flex flex-col gap-2">
        {facts.map(([key, label]) => {
          const value = collected[key];
          return (
            <div key={key} className="flex items-baseline justify-between gap-3">
              <dt className="flex shrink-0 items-center gap-2 text-sm text-muted">
                <span
                  className={`size-1.5 shrink-0 rounded-full ${
                    value ? "bg-accent" : "border border-muted"
                  }`}
                  aria-hidden="true"
                />
                {label}
              </dt>
              <dd className={`min-w-0 text-right text-sm break-words ${value ? "text-ink" : "text-muted"}`}>
                {value ?? "Still open"}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

export function CollectedPanel({ collected, graduated }: CollectedPanelProps) {
  const early = graduated && filledCount(collected) < FACTS.length;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-[15px] font-medium tracking-tight">Collected</p>
        <p className="mt-0.5 text-sm text-muted">{statusLabel(collected, graduated)}</p>
      </div>
      {graduated ? (
        <p className="rounded-2xl bg-accent/10 px-3 py-2 text-sm leading-5 text-ink">
          {early ? "Started early. What's still open can wait." : "Onboarding complete."}
        </p>
      ) : (
        <p className="text-sm leading-5 text-muted">{CHANNEL_HINT}</p>
      )}
      <FactGroup title="On a call" facts={CALL_FACTS} collected={collected} />
      <FactGroup title="Chat only" facts={CHAT_FACTS} collected={collected} />
    </div>
  );
}

