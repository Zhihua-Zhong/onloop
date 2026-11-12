import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

// The few building blocks every view uses, so the five views share one type scale, one spacing scale and one set of
// components (the integrity rule of the vision loop).

export type Tone = "human" | "blocked" | "open" | "done" | "void" | "external" | "stall" | "neutral";

const TONE: Record<Tone, { dot: string; text: string; soft: string; edge: string }> = {
  human: { dot: "bg-state-human", text: "text-state-human", soft: "bg-state-human/10", edge: "border-l-state-human" },
  blocked: { dot: "bg-state-blocked", text: "text-state-blocked", soft: "bg-state-blocked/10", edge: "border-l-state-blocked" },
  open: { dot: "bg-state-open", text: "text-state-open", soft: "bg-state-open/10", edge: "border-l-state-open" },
  done: { dot: "bg-state-done", text: "text-state-done", soft: "bg-state-done/10", edge: "border-l-state-done" },
  void: { dot: "bg-state-void", text: "text-muted", soft: "bg-state-void/10", edge: "border-l-state-void" },
  external: { dot: "bg-state-external", text: "text-state-external", soft: "bg-state-external/10", edge: "border-l-state-external" },
  stall: { dot: "bg-state-stall", text: "text-state-stall", soft: "bg-state-stall/10", edge: "border-l-state-stall" },
  neutral: { dot: "bg-muted", text: "text-muted", soft: "bg-raised", edge: "border-l-line" },
};
export const tone = (t: Tone) => TONE[t];

export function Pill({ t, children, className, testid }: { t: Tone; children: ReactNode; className?: string; testid?: string }) {
  return (
    <span data-testid={testid} className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 font-sans text-xs font-medium", TONE[t].soft, TONE[t].text, className)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", TONE[t].dot)} aria-hidden />
      {children}
    </span>
  );
}

export function Section({ title, count, t, children, testid, note }: { title: string; count?: number; t: Tone; children: ReactNode; testid?: string; note?: ReactNode }) {
  return (
    <section data-testid={testid} aria-label={title} className="flex min-w-0 flex-col gap-3">
      <header className="flex items-baseline gap-2">
        <span className={cn("h-2.5 w-2.5 translate-y-[-1px] rounded-full", TONE[t].dot)} aria-hidden />
        <h2 className="text-base font-semibold">{title}</h2>
        {count != null && <span className={cn("text-base font-semibold tabular-nums", count ? TONE[t].text : "text-muted")}>{count}</span>}
        {note && <span className="ml-auto text-xs text-muted">{note}</span>}
      </header>
      {children}
    </section>
  );
}

export function Card({ t, children, testid, className }: { t: Tone; children: ReactNode; testid?: string; className?: string }) {
  return (
    <article data-testid={testid} className={cn("rounded-ui border border-l-[3px] border-line bg-surface px-3.5 py-3 shadow-sm", TONE[t].edge, className)}>
      {children}
    </article>
  );
}

export function Meta({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cn("mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[13px] leading-snug text-muted", className)}>{children}</p>;
}

export const Dot = () => <span aria-hidden className="text-line">·</span>;

export function Empty({ children, testid }: { children: ReactNode; testid?: string }) {
  return <p data-testid={testid} className="rounded-ui border border-dashed border-line px-3.5 py-3 text-sm text-muted">{children}</p>;
}

// Read-only UI (decision 0009): where a person can act, the page shows the exact command, ready to copy.
export function Command({ cmd, className }: { cmd: string; className?: string }) {
  const [done, setDone] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(cmd).then(
      () => {
        setDone(true);
        window.setTimeout(() => setDone(false), 1500);
      },
      () => undefined,
    );
  };
  return (
    <div data-testid="command" className={cn("mt-2 flex min-w-0 items-center gap-1 rounded-md border border-line bg-raised pl-2.5 font-mono text-[12.5px]", className)}>
      <code className="min-w-0 flex-1 truncate py-1.5" title={cmd}>{cmd}</code>
      <button type="button" onClick={copy} aria-label={`Copy: ${cmd}`} className="shrink-0 rounded-r-md border-l border-line px-2 py-1.5 text-muted hover:bg-surface hover:text-fg">
        {done ? <Check className="h-3.5 w-3.5 text-state-done" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
      </button>
    </div>
  );
}

export function Problem({ error }: { error: unknown }) {
  return (
    <p role="alert" data-testid="error" className="rounded-ui border border-state-blocked/40 bg-state-blocked/10 px-3.5 py-3 text-sm text-state-blocked">
      {error instanceof Error ? error.message : "The ledger could not be read."}
    </p>
  );
}

export function Loading({ what }: { what: string }) {
  return <p data-testid="loading" className="px-1 py-6 text-sm text-muted">Reading {what}…</p>;
}

// Text from the ledger that marks code with backticks (a parse error naming `outcome:`) reads as code, not as quotes.
export function Ticks({ text }: { text: string }) {
  const parts = text.split("`");
  return (
    <>
      {parts.map((p, i) => (i % 2 ? <code key={i} className="rounded bg-raised px-1 font-mono text-[0.92em]">{p}</code> : <span key={i}>{p}</span>))}
    </>
  );
}
