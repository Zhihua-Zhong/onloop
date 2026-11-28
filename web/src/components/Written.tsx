import { Sparkles } from "lucide-react";
import type { HomeDecision } from "@/lib/api";
import { shortHash, type DecisionText } from "@/lib/texts";
import { Button } from "@/components/ui/button";

// What the smart layer wrote, marked as written (prototype F): the sparkle, and where it came from. Generated text never
// stands where a fact should: a suggestion is one of the recorded options, its grounds are the decision's facts, and
// without a text the page shows the facts alone.

export function WrittenMark({ hash, what = "this week's data" }: { hash: string; what?: string }) {
  return (
    <span data-testid="written-by">
      Written by onloop from {what} · facts <span className="font-mono">{shortHash(hash)}</span>
    </span>
  );
}

export function Suggested({ t, hash }: { t: DecisionText; hash: string }) {
  if (!t.suggested) return null;
  return (
    <div className="rounded-lg border border-line p-4" data-testid="suggested">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted">
        <Sparkles className="size-3.5" aria-hidden />
        Suggested
      </div>
      <div className="mt-1 text-base font-semibold" data-testid="suggested-answer">{t.suggested}</div>
      {t.grounds && <p className="mt-1 text-sm text-muted">{t.grounds}</p>}
      <p className="mt-2 text-xs text-muted">
        <WrittenMark hash={hash} what="this decision's facts" />. One of the answers it offered; you choose.
      </p>
    </div>
  );
}

/** The recorded options, the suggested one first as the one primary button, then Decline. */
export function OptionButtons({
  d,
  suggested,
  busy,
  onAnswer,
  size,
}: {
  d: HomeDecision;
  suggested: string | null;
  busy: boolean;
  onAnswer: (resolution: "done" | "declined", option?: string) => void;
  size?: "sm";
}) {
  const rest = d.options.filter((o) => o !== suggested);
  return (
    <div className="flex flex-wrap gap-2">
      {suggested && (
        <Button size={size} disabled={busy} onClick={() => onAnswer("done", suggested)} data-testid="option" data-suggested="true">
          <Sparkles aria-hidden />
          {suggested}
        </Button>
      )}
      {rest.map((o) => (
        <Button key={o} size={size} variant="outline" disabled={busy} onClick={() => onAnswer("done", o)} data-testid="option">
          {o}
        </Button>
      ))}
      <Button size={size} variant="ghost" disabled={busy} onClick={() => onAnswer("declined")} data-testid={size ? "mark-declined" : "decline"}>
        Decline
      </Button>
    </div>
  );
}
