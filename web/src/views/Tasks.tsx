import { markError, marks, usePending } from "@/lib/marks";
import { isUrl } from "@/lib/url";
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { tasksApi, uiPost, type DigestModel, type TaskCard } from "@/lib/api";
import { ago, duration, plural } from "@/lib/format";
import { Card, Dot, Meta, Pill, tone, type Tone } from "@/components/kit";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

// The person's task list (decision 0013). Each card answers, in reading order: what does this change for the whole
// (the effect), which goal item does it move, how much does it weigh, what waits and from when, and what happens if
// nobody answers. The order is the server's: weight bands, then the days left (computed when read), then age.

const BAND: Record<TaskCard["band"], { label: string; t: Tone }> = {
  critical: { label: "critical", t: "blocked" },
  supporting: { label: "supporting", t: "human" },
  unweighted: { label: "no weight", t: "neutral" },
  skippable: { label: "skippable", t: "void" },
  incomplete: { label: "incomplete", t: "stall" },
};

const MISSING: Record<string, string> = { no_effect: "what it changes", no_holds: "what waits on it" };


function origin(t: TaskCard): string {
  switch (t.origin) {
    case "outcome":
    case "cli":
      return t.author_role ? `asked by ${t.author_role}` : "asked";
    case "adapter":
      return "from the tracker";
    case "gate":
      return "an approval at the merge gate";
    case "notification":
      return `a prompt in ${t.author_role}'s session`;
    case "pause":
      return "you paused it";
    default:
      return "waits on you";
  }
}

// The cost of delay, from the date the server keeps: the count of days is always computed, never stored.
export function costText(t: TaskCard): string | null {
  if (t.days_left != null && t.cost_from) {
    if (t.days_left === 0) return "costing now";
    const d = new Date(`${t.cost_from}T00:00:00`);
    const when = d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
    return `costs from ${when} (in ${plural(t.days_left, "day")})`;
  }
  return t.cost_unknown ? "costs from a day not known" : null;
}

function stage(t: TaskCard, now: string): string | null {
  if (t.stage === "returned_author") return `returned to ${t.author_role ?? "its author"} ${ago((Date.parse(now) - Date.parse(t.returned_at ?? now)) / 1000)}`;
  if (t.stage === "returned_coordinator") return "with the coordinator: close it or keep it";
  const left = (Date.parse(t.return_author_at) - Date.parse(now)) / 1000;
  return t.author_role && left > 0 ? `returns to ${t.author_role} in ${duration(left)}` : null;
}

export function TaskCardView({ t, now, onHide }: { t: TaskCard; now: string; onHide: (id: string) => void }) {
  const pending = usePending(t.id);
  const failed = markError(t.id);
  const band = BAND[t.band];
  const st = stage(t, now);
  const critical = t.weight === "critical";
  return (
    <Card t={critical ? "blocked" : t.incomplete ? "stall" : "human"} testid={`task-${t.id}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <Pill t={band.t} testid="band">{critical && t.incomplete ? "critical, incomplete" : band.label}</Pill>
        {t.goal && <span className="rounded-full border border-line px-2 py-0.5 font-mono text-[11.5px] text-muted" data-testid="goal">{t.goal}</span>}
        {!t.goal && t.origin !== "pause" && t.origin !== "notification" && <span className="text-[11.5px] text-muted">no goal</span>}
      </div>
      <h3 className={cn("mt-1.5 text-[15px] font-semibold leading-snug", !t.effect && "text-state-stall")} data-testid="effect">
        {t.headline}
      </h3>
      {t.incomplete && t.incomplete_reasons.length > 0 && (
        <p className="mt-0.5 text-[13px] text-state-stall">Not said: {t.incomplete_reasons.map((r) => MISSING[r] ?? r).join(", ")}</p>
      )}
      {t.raw_line && (
        <details className="mt-1 text-[12.5px] text-muted" data-testid="raw-line">
          <summary className="cursor-pointer select-none hover:text-fg">The line it was asked with</summary>
          <code className="mt-1 block break-all font-mono text-[12px]">{t.raw_line}</code>
        </details>
      )}
      {(costText(t) || t.holds) && (
        <p className="mt-1 text-[13px] leading-snug" data-testid="cost">
          {t.holds ? <span className="font-medium">{t.holds}</span> : <span className="text-muted">what waits is not said</span>}
          {costText(t) && (
            <span className={t.days_left === 0 ? "font-semibold text-state-blocked" : ""}> · {costText(t)}</span>
          )}
        </p>
      )}
      <p className="mt-1 text-[13px] leading-snug" data-testid="default">
        {t.default ? <>If nobody answers: <span className="font-medium">{t.default}</span></> : <span className="text-muted">No default: nothing happens until you answer.</span>}
      </p>
      <Meta>
        <span>{origin(t)}</span>
        <Dot />
        <span>waiting {duration(t.age_s)}</span>
        {st && (
          <>
            <Dot />
            <span className={t.stage !== "open" ? "font-medium text-fg" : ""} data-testid="stage">{st}</span>
          </>
        )}
        {t.ref && (
          <>
            <Dot />
            {isUrl(t.ref) ? (
              <a className="inline-flex items-center gap-0.5 text-accent hover:underline" href={t.ref} target="_blank" rel="noopener noreferrer">
                open <ExternalLink className="h-3 w-3" aria-hidden />
              </a>
            ) : (
              <span className="font-mono text-[12px]">{t.ref}</span>
            )}
          </>
        )}
      </Meta>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {t.origin === "gate" ? (
          <span className="text-[13px] font-medium text-muted">Approve it on the code host; onloop sees the approval.</span>
        ) : t.origin === "pause" ? (
          <code className="rounded bg-raised px-1.5 py-0.5 text-[12px]">onloop role resume {t.ref?.replace(/^role:/, "")}</code>
        ) : (
          <>
            <Button size="sm" variant="outline" disabled={!!pending} onClick={() => marks.mark(t.id, "done")} data-testid="mark-done">Done</Button>
            <Button size="sm" variant="ghost" disabled={!!pending} onClick={() => marks.mark(t.id, "declined")} data-testid="mark-declined">Decline</Button>
          </>
        )}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => onHide(t.id)} data-testid="not-now">Not now</Button>
      </div>
      {pending && <UndoStrip id={t.id} />}
      {failed && !pending && <p className="mt-1 text-[12px] text-state-blocked">Not taken: {failed}</p>}
    </Card>
  );
}

export function TaskList({ tasks, now }: { tasks: TaskCard[]; now: string }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const shown = tasks.filter((t) => !hidden.has(t.id));
  return (
    <>
      {shown.map((t) => <TaskCardView key={t.id} t={t} now={now} onHide={(id) => setHidden(new Set([...hidden, id]))} />)}
      {hidden.size > 0 && (
        <button type="button" className="px-1 text-left text-[13px] text-accent hover:underline" onClick={() => setHidden(new Set())} data-testid="show-hidden">
          {plural(hidden.size, "task")} set aside for this hour: show
        </button>
      )}
    </>
  );
}

// "Since you were away": what changed on the list since the last visit. The visit is recorded once the page has been
// open for a few seconds, at most once per 15 minutes (a local reminder only; the ledger keeps the visit itself).
const VISIT_KEY = "onloop.visit.posted";
const SEEN_KEY = "onloop.digest.seen";

function useRecordVisit() {
  useEffect(() => {
    const id = window.setTimeout(() => {
      let last = 0;
      try {
        last = Number(window.localStorage.getItem(VISIT_KEY) ?? 0);
      } catch {
        /* no storage: post anyway */
      }
      if (Date.now() - last < 15 * 60_000) return;
      uiPost("/ui/visits", {})
        .then(() => {
          try {
            window.localStorage.setItem(VISIT_KEY, String(Date.now()));
          } catch {
            /* fine */
          }
        })
        .catch(() => undefined);
    }, 4000);
    return () => window.clearTimeout(id);
  }, []);
}

function sinceText(d: DigestModel): string {
  if (!d.since) return "Since the start";
  const s = new Date(d.since);
  return `Since ${s.toLocaleDateString(undefined, { weekday: "long" })} ${s.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
}

export function DigestBand() {
  useRecordVisit();
  const q = useQuery({ queryKey: ["digest"], queryFn: tasksApi.digest, staleTime: Infinity });
  const d = q.data;
  // The band opens with what changed the first time this absence is seen in this browser, and is one line after that.
  const [open, setOpen] = useState<boolean | null>(null);
  useEffect(() => {
    if (!d?.since || open !== null) return;
    let seen = false;
    try {
      seen = window.localStorage.getItem(SEEN_KEY) === d.since;
      window.localStorage.setItem(SEEN_KEY, d.since);
    } catch {
      /* no storage: open it */
    }
    setOpen(!seen);
  }, [d?.since, open]);
  if (!d) return null;
  const away = d.since ? (Date.parse(d.now) - Date.parse(d.since)) / 1000 : Infinity;
  if (away < 8 * 3600) return null;
  const facts = [
    `${d.added.length} added`,
    `${d.resolved.length} resolved`,
    ...(d.resolved_without_you.length ? [`${d.resolved_without_you.length} resolved without you`] : []),
    ...(d.returned.length ? [`${d.returned.length} returned`] : []),
    ...(d.ticks.length ? [plural(d.ticks.filter((t) => t.kind === "tick").length, "goal tick")] : []),
    ...(d.gaps.length ? [`onloop was down ${plural(d.gaps.length, "time")}`] : []),
  ];
  return (
    <section data-testid="digest" aria-label="Since you were away" className="rounded-ui border border-line bg-surface px-3 py-1.5">
      <button type="button" className="flex w-full items-baseline gap-x-2 text-left" onClick={() => setOpen(!open)} aria-expanded={!!open}>
        <span className="shrink-0 text-[13px] font-semibold">{sinceText(d)}:</span>
        <span className="min-w-0 truncate text-[13px]">{facts.join(" · ")}</span>
        <span className="ml-auto shrink-0 text-[12.5px] text-accent">{open ? "hide" : "what changed"}</span>
      </button>
      {open && (
        <div className="mt-1 grid gap-0.5 pb-1 text-[12.5px] leading-snug" data-testid="digest-detail">
          {d.resolved_without_you.map((t) => (
            <p key={t.id}><span className="font-medium">Resolved without you:</span> {t.headline} <span className="text-muted">({t.resolution}, {t.evidence})</span></p>
          ))}
          {d.returned.map((t) => (
            <p key={t.id}><span className="font-medium">Returned:</span> {t.headline} <span className="text-muted">({t.stage.replace("_", " to the ")})</span></p>
          ))}
          {d.gaps.map((g) => (
            <p key={g.from}><span className="font-medium">onloop was down</span> {duration(g.seconds)} from {new Date(g.from).toLocaleString()} ({g.cause ?? "cause unknown"})</p>
          ))}
          <p className="text-muted">The {d.added.length} added are in the list below, in its order.</p>
        </div>
      )}
    </section>
  );
}


// The compact row form (vision round 3): one line per task, in the one order, so a person plans the next hour from the
// first screen. A row gives the weight, the effect, what waits, when the delay costs, the age and where it came from;
// opening it shows the full card with its actions.
export function costShort(t: TaskCard): string {
  if (t.days_left != null && t.cost_from) return t.days_left === 0 ? "costing now" : `in ${t.days_left} d`;
  return t.cost_unknown ? "date unknown" : "";
}

function originShort(t: TaskCard): string {
  switch (t.origin) {
    case "outcome":
    case "cli":
      return t.author_role ?? "asked";
    case "adapter":
      return "tracker";
    case "gate":
      return "merge gate";
    case "notification":
      return `${t.author_role} prompt`;
    case "pause":
      return "your pause";
    default:
      return "waits";
  }
}

function shortAge(seconds: number): string {
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} d`;
}

/** "Marked done · Undo (6 s)": the mark is sent when the count ends; Undo, or leaving the page, sends nothing. */
export function UndoStrip({ id }: { id: string }) {
  const p = usePending(id);
  const [, tick] = useState(0);
  useEffect(() => {
    const h = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(h);
  }, []);
  if (!p) return null;
  const left = Math.max(0, Math.ceil((p.sendAt - Date.now()) / 1000));
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-x-2 rounded-md bg-raised px-2 py-1 text-[13px]" role="status" data-testid="undo-strip">
      <span className="font-medium">{p.resolution === "done" ? "Marked done" : "Declined"}</span>
      <span className="text-muted">· sent in {left} s; leaving the page cancels it</span>
      <button type="button" onClick={() => marks.undo(id)} className="ml-auto font-medium text-accent hover:underline" data-testid="undo">
        Undo
      </button>
    </p>
  );
}

export function TaskRows({ tasks, now }: { tasks: TaskCard[]; now: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const shown = tasks.filter((t) => !hidden.has(t.id));
  return (
    <div className="flex flex-col">
      <ol className="divide-y divide-line overflow-hidden rounded-ui border border-line bg-surface" data-testid="task-rows">
        {shown.map((t, i) => {
          const band = BAND[t.band];
          const cost = costShort(t);
          const isOpen = open === t.id;
          return (
            <li key={t.id} data-testid={`row-${t.id}`} className={cn(isOpen && "bg-raised/40")}>
              <button
                type="button"
                onClick={() => setOpen(isOpen ? null : t.id)}
                aria-expanded={isOpen}
                className="grid w-full grid-cols-[1.25rem_5.5rem_minmax(0,1fr)_auto] items-baseline gap-x-2 px-3 py-[7px] text-left hover:bg-raised/60 max-sm:grid-cols-[1.1rem_minmax(0,1fr)_auto] max-sm:py-2"
              >
                <span className="text-right text-[12px] font-semibold tabular-nums text-muted">{i + 1}</span>
                <span className={cn("inline-flex items-center gap-1.5 text-[12px] font-medium max-sm:hidden", tone(band.t).text)} data-testid="row-band">
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", tone(band.t).dot)} aria-hidden />
                  {t.weight === "critical" && t.incomplete ? "critical!" : band.label}
                </span>
                <span className="min-w-0">
                  <span className="flex min-w-0 items-baseline gap-2">
                    <span className={cn("h-2 w-2 shrink-0 translate-y-[-1px] rounded-full sm:hidden", tone(band.t).dot)} aria-hidden />
                    <span className={cn("min-w-0 shrink truncate text-[14px] font-medium max-sm:line-clamp-2 max-sm:whitespace-normal max-sm:[overflow-wrap:anywhere]", !t.effect && "text-state-stall")} data-testid="row-effect" title={t.raw_line ? `The line it was asked with: ${t.raw_line}` : t.headline}>
                      {t.headline}
                    </span>
                    {t.holds && <span className="hidden max-w-[32%] shrink-[3] truncate text-[12.5px] text-muted xl:inline">{t.holds}</span>}
                  </span>
                  <span className="mt-0.5 flex gap-2 text-[12px] text-muted sm:hidden">
                    {cost && <span className={t.days_left === 0 ? "font-semibold text-state-blocked" : ""}>{cost}</span>}
                    <span>{originShort(t)}</span>
                    <span>{shortAge(t.age_s)}</span>
                    {t.goal && <span className="font-mono">{t.goal}</span>}
                  </span>
                </span>
                <span className="flex shrink-0 items-baseline gap-3 text-[12.5px] tabular-nums max-sm:hidden">
                  {t.goal && <span className="font-mono text-[11.5px] text-muted">{t.goal}</span>}
                  <span className={cn("w-[5.5rem] text-right", t.days_left === 0 ? "font-semibold text-state-blocked" : "text-muted")} data-testid="row-cost">{cost || "—"}</span>
                  <span className="w-[4.5rem] truncate text-right text-muted">{originShort(t)}</span>
                  <span className="w-10 text-right text-muted">{shortAge(t.age_s)}</span>
                </span>
              </button>
              {!isOpen && (
                <div className="px-3 pb-1 empty:hidden">
                  <UndoStrip id={t.id} />
                </div>
              )}
              {isOpen && (
                <div className="px-3 pb-3">
                  <TaskCardView t={t} now={now} onHide={(id) => { setHidden(new Set([...hidden, id])); setOpen(null); }} />
                </div>
              )}
            </li>
          );
        })}
      </ol>
      {hidden.size > 0 && (
        <button type="button" className="mt-1.5 px-1 text-left text-[13px] text-accent hover:underline" onClick={() => setHidden(new Set())} data-testid="show-hidden">
          {plural(hidden.size, "task")} set aside for this hour: show
        </button>
      )}
    </div>
  );
}
