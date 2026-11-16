import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { tasksApi, uiPost, type DigestModel, type TaskCard } from "@/lib/api";
import { ago, duration, plural } from "@/lib/format";
import { Card, Dot, Meta, Pill, type Tone } from "@/components/kit";
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

const MISSING: Record<string, string> = { no_effect: "no effect stated", no_holds: "no word on what waits" };

function isUrl(s: string | null): s is string {
  return !!s && /^https?:\/\//.test(s);
}

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

function stage(t: TaskCard, now: string): string | null {
  if (t.stage === "returned_author") return `back with ${t.author_role ?? "its author"} since ${ago((Date.parse(now) - Date.parse(t.returned_at ?? now)) / 1000)}`;
  if (t.stage === "returned_coordinator") return "with the coordinator: close it or keep it";
  const left = (Date.parse(t.return_author_at) - Date.parse(now)) / 1000;
  return t.author_role && left > 0 ? `returns to ${t.author_role} in ${duration(left)}` : null;
}

export function TaskCardView({ t, now, onHide }: { t: TaskCard; now: string; onHide: (id: string) => void }) {
  const qc = useQueryClient();
  const mark = useMutation({
    mutationFn: (resolution: "done" | "declined") => uiPost("/ui/tasks/resolve", { id: t.id, resolution }),
    onSuccess: () => qc.invalidateQueries(),
  });
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
      <h3 className="mt-1.5 text-[15px] font-semibold leading-snug" data-testid="effect">
        {t.effect ?? <span className="text-state-stall">Incomplete: {t.incomplete_reasons.map((r) => MISSING[r] ?? r).join(", ")}</span>}
      </h3>
      {(!t.effect || t.incomplete) && t.title && <p className="mt-0.5 text-[13px] leading-snug text-muted">{t.title}</p>}
      {t.effect && t.incomplete && <p className="mt-0.5 text-[13px] text-state-stall">Incomplete: {t.incomplete_reasons.map((r) => MISSING[r] ?? r).join(", ")}</p>}
      {(t.cost_text || t.holds) && (
        <p className="mt-1 text-[13px] leading-snug" data-testid="cost">
          {t.holds ? <span className="font-medium">{t.holds}</span> : <span className="text-muted">what waits is not said</span>}
          {t.cost_text && (
            <span className={t.days_left === 0 ? "font-semibold text-state-blocked" : ""}> · {t.cost_text}</span>
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
            <Button size="sm" variant="outline" disabled={mark.isPending} onClick={() => mark.mutate("done")} data-testid="mark-done">Done</Button>
            <Button size="sm" variant="ghost" disabled={mark.isPending} onClick={() => mark.mutate("declined")} data-testid="mark-declined">Decline</Button>
          </>
        )}
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => onHide(t.id)} data-testid="not-now">Not now</Button>
      </div>
      {mark.isError && <p className="mt-1 text-[12px] text-state-blocked">{String((mark.error as Error).message)}</p>}
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
  const [open, setOpen] = useState(false);
  const d = q.data;
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
    <section data-testid="digest" aria-label="Since you were away" className="rounded-zone border border-line bg-surface px-4 py-3">
      <button type="button" className="flex w-full flex-wrap items-baseline gap-x-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="text-sm font-semibold">{sinceText(d)}:</span>
        <span className="text-sm">{facts.join(" · ")}</span>
        <span className="ml-auto text-[13px] text-accent">{open ? "hide" : "show what changed"}</span>
      </button>
      {open && (
        <div className="mt-2 grid gap-1 text-[13px] leading-snug" data-testid="digest-detail">
          {d.resolved_without_you.map((t) => (
            <p key={t.id}><span className="font-medium">Resolved without you:</span> {t.effect ?? t.title} <span className="text-muted">({t.resolution}, {t.evidence})</span></p>
          ))}
          {d.returned.map((t) => (
            <p key={t.id}><span className="font-medium">Returned:</span> {t.effect ?? t.title} <span className="text-muted">({t.stage.replace("_", " to the ")})</span></p>
          ))}
          {d.added.map((t) => (
            <p key={t.id}><span className="font-medium">Added:</span> {t.effect ?? t.title}</p>
          ))}
          {d.gaps.map((g) => (
            <p key={g.from}><span className="font-medium">onloop was down</span> {duration(g.seconds)} from {new Date(g.from).toLocaleString()} ({g.cause ?? "cause unknown"})</p>
          ))}
        </div>
      )}
    </section>
  );
}
