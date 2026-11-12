import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ExternalLink } from "lucide-react";
import { api, type NowModel, type Stall, type WaitCard } from "@/lib/api";
import { ago, clock, duration, plural, reason } from "@/lib/format";
import { href } from "@/lib/route";
import { Card, Command, Dot, Empty, Loading, Meta, Pill, Problem, Section, Ticks, tone, type Tone } from "@/components/kit";
import { cn } from "@/lib/utils";

// The first screen. In five seconds it answers: what is the biggest constraint, what waits on me, and where has work
// stalled (blueprint section 4.1). The banner is the one loud thing on the page; each column is one question.

function Banner({ m }: { m: NowModel }) {
  const c = m.constraint;
  if (!c) {
    return (
      <section data-testid="constraint" aria-label="Biggest constraint" className="rounded-zone border border-state-done/40 bg-state-done/10 px-5 py-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-state-done">Biggest constraint</p>
        <p className="mt-1 text-lg font-semibold">Nothing is held up right now.</p>
      </section>
    );
  }
  const n = c.waiters.length;
  const what = c.on_kind === "human" ? "you" : c.on_ref;
  const oldest = Math.max(0, ...c.waits.map((w) => w.wait_s));
  const noun = c.on_kind === "human" ? "item" : "change";
  const facts = [
    `${plural(n, noun)} ${c.on_kind === "resource" ? "queued" : "waiting"}`,
    `${duration(c.direct_wait_s)} waiting in total`,
    `oldest ${duration(oldest)}`,
    ...(c.downstream_wait_s > c.direct_wait_s ? [`${duration(c.downstream_wait_s)} with what waits behind them`] : []),
  ];
  const r = m.related;
  return (
    <section
      data-testid="constraint"
      aria-label="Biggest constraint"
      className="rounded-zone border border-state-blocked/45 bg-state-blocked/[0.07] px-5 py-4 max-sm:px-4 max-sm:py-3 dark:bg-state-blocked/[0.12]"
    >
      <div className="flex flex-wrap items-start gap-x-8 gap-y-3">
        <div className="min-w-0 flex-[2_1_420px]">
          <p className="text-xs font-semibold uppercase tracking-wide text-state-blocked">Biggest constraint</p>
          <h1 className="mt-1 text-[22px] font-semibold leading-tight max-sm:text-[19px]" data-testid="constraint-headline">
            <span className="font-mono text-[0.92em]">{what}</span> holds {plural(n, c.on_kind === "human" ? "item" : "change")}
            {c.on_kind === "resource" ? " in its queue" : ""}
          </h1>
          <p className="mt-1.5 flex flex-wrap gap-x-2 text-[15px] leading-snug" data-testid="constraint-numbers">
            {facts.map((f, i) => (
              <span key={f} className={i === 0 ? "font-semibold" : ""}>
                {i > 0 && <span aria-hidden className="mr-2 text-muted">·</span>}
                {f}
              </span>
            ))}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {Object.entries(c.reasons).map(([k, v]) => (
              <Pill key={k} t="blocked">{v} {reason(k)}</Pill>
            ))}
            {c.resource_state === "dead" && <Pill t="blocked">dead</Pill>}
          </div>
          {r && (r.conflicts > 0 || r.bottlenecks.length > 0) && (
            <p className="mt-2.5 text-[13px] text-muted max-sm:mt-2" data-testid="constraint-related">
              {r.conflicts > 0 && <>Also: {plural(r.conflicts, "change")} conflict with main and cannot enter the queue. </>}
              {r.bottlenecks.length > 0 && (
                <>Next in line: {r.bottlenecks.map((b) => `${b.on_kind === "human" ? "you" : b.on_ref} (${b.waiters})`).join(", ")}.</>
              )}
            </p>
          )}
        </div>
        {m.next && (
          <div className="min-w-0 flex-[1_1_280px] rounded-ui border border-line bg-surface px-3.5 py-3 max-sm:py-2.5" data-testid="next-move">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted">Next move</p>
            <p className="mt-1 text-[15px] font-medium leading-snug" data-testid="next-text">{m.next.text}</p>
            {m.next.command && <Command cmd={m.next.command} />}
            <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
              {m.next.ref && (
                <a href={m.next.ref} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-accent hover:underline" data-testid="next-link">
                  Open it <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                </a>
              )}
              <a href={href("flow")} className="inline-flex items-center gap-1 text-accent hover:underline">
                See it on Flow <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              </a>
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

function Waiting({ w }: { w: WaitCard }) {
  const urgent = w.left_s != null && w.left_s < 3600;
  return (
    <Card t="human" testid={`wait-${w.id}`}>
      <h3 className="text-[15px] font-semibold leading-snug">{w.kind === "paused_role" ? <>Resume <span className="font-mono">{w.owner_role}</span>, which you paused</> : w.title}</h3>
      {w.kind === "paused_role" ? (
        <Meta>
          <span className="font-medium text-fg">{plural(w.queued ?? 0, "item")} queued behind it</span>
          <Dot />
          <span>paused {ago(w.age_s)}</span>
        </Meta>
      ) : (
        <>
          <p className="mt-1 text-[13px] leading-snug">
            {w.default ? (
              <>If nobody answers: <span className="font-medium">{w.default}</span></>
            ) : (
              <span className="text-muted">No default: nothing happens until you answer.</span>
            )}
          </p>
          <Meta>
            {w.left_s != null && (
              <span className={cn("font-medium", urgent ? "text-state-blocked" : "text-fg")} data-testid="left">
                {w.left_s > 0 ? `${duration(w.left_s)} left` : "expired"}
              </span>
            )}
            {w.left_s != null && <Dot />}
            <span>{reason(w.reason)}</span>
            <Dot />
            <span>waiting {duration(w.age_s)}</span>
            {w.ref && (
              <>
                <Dot />
                <a className="inline-flex items-center gap-0.5 text-accent hover:underline" href={w.ref} target="_blank" rel="noopener noreferrer">
                  open <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              </>
            )}
          </Meta>
        </>
      )}
      {w.command && <Command cmd={w.command} />}
    </Card>
  );
}

function StallCard({ s }: { s: Stall }) {
  const why = (s.detail.parse_error as string | undefined) ?? (s.detail.reason as string | undefined);
  return (
    <Card t="stall" testid={`stall-${s.id}`}>
      <h3 className="text-[15px] font-semibold leading-snug">{s.title}</h3>
      {why && <p className="mt-1 text-[13px] leading-snug"><Ticks text={why} /></p>}
      <Meta>
        <span className="font-medium text-fg">stalled {duration(s.age_s)}</span>
        <Dot />
        <span data-testid="would">{s.would ? `${s.would.label}${s.would.target ? `: ${s.would.target}` : ""}` : "no wake scheduled"}</span>
        <Dot />
        <span data-testid="ack" className={s.acknowledged ? "text-state-done" : ""}>
          {s.acknowledged ? `acknowledged ${clock(s.acknowledged.at)}` : "not acknowledged"}
        </span>
      </Meta>
    </Card>
  );
}

function Health({ h, now }: { h: NowModel["health"]; now: string }) {
  const beat = h.onloopd;
  const dm = h.dead_man;
  return (
    <div className="flex flex-col gap-3">
      {h.schedules.map((s) => (
        <Card key={s.id} t={s.state === "failing" ? "blocked" : "human"} testid={`health-${s.id}`}>
          <h3 className="flex items-center gap-2 text-[15px] font-semibold">
            <span className="font-mono">{s.name}</span>
            <Pill t={s.state === "failing" ? "blocked" : "human"}>{s.state === "failing" ? "failing" : "overdue"}</Pill>
          </h3>
          <Meta>
            <span>last success {s.last_success ? ago((Date.parse(now) - Date.parse(s.last_success)) / 1000) : "never"}</span>
            {s.period && (
              <>
                <Dot />
                <span>every {s.period}</span>
              </>
            )}
            {s.has_log_tail && (
              <>
                <Dot />
                <a className="text-accent hover:underline" href={href("schedules", { open: s.id })}>open the log tail</a>
              </>
            )}
          </Meta>
        </Card>
      ))}
      <Card t={!beat || beat.stale ? "blocked" : "done"} testid="health-onloopd">
        <h3 className="text-[15px] font-semibold">onloop itself</h3>
        <Meta>
          <span data-testid="heartbeat">
            {beat ? `onloopd heartbeat ${clock(beat.at, now, true)} (${ago(beat.age_s)})${beat.stale ? ", stale" : ""}` : "no heartbeat file: onloopd may not be running"}
          </span>
          <Dot />
          <span data-testid="dead-man" className={dm.configured ? "" : "text-muted"}>
            {dm.configured ? `dead-man switch ${dm.state === "ok" ? "OK" : dm.state} (${dm.schedule})` : "dead-man not configured"}
          </span>
        </Meta>
      </Card>
      <p className="px-1 text-[13px] text-muted" data-testid="health-ok">
        {h.schedules_ok
          ? `${plural(h.schedules_ok, h.schedules.length ? "other schedule" : "schedule")} within ${h.schedules_ok === 1 ? "its" : "their"} period.`
          : h.schedules_total
            ? ""
            : "No schedules declared in onloopd.yaml."}{" "}
        <a className="text-accent hover:underline" href={href("schedules")}>All schedules</a>
      </p>
    </div>
  );
}

// On a phone the three columns stack; this strip keeps all three answers on the first screen, one tap from each.
function Glance({ m }: { m: NowModel }) {
  const health = m.health.schedules.length + (!m.health.onloopd || m.health.onloopd.stale ? 1 : 0);
  const chips: { id: string; n: number; label: string; t: Tone }[] = [
    { id: "col-waiting", n: m.waiting_on_you.length, label: "waiting on you", t: "human" },
    { id: "col-stalls", n: m.stalls.length, label: "stalled", t: "stall" },
    { id: "col-health", n: health, label: health === 1 ? "health problem" : "health problems", t: "blocked" },
  ];
  return (
    <nav aria-label="At a glance" data-testid="glance" className="sticky top-0 z-10 -mx-4 -my-2 flex gap-2 bg-bg/95 px-4 py-2 backdrop-blur lg:hidden">
      {chips.map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => document.querySelector(`[data-testid="${c.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" })}
          className={cn("flex min-w-0 flex-1 flex-col items-start rounded-ui border border-line bg-surface px-2.5 py-1.5 text-left", c.n === 0 && "opacity-70")}
        >
          <span className={cn("text-lg font-semibold leading-none tabular-nums", c.n ? tone(c.t).text : "text-muted")}>{c.n}</span>
          <span className="mt-0.5 truncate text-[11.5px] text-muted">{c.label}</span>
        </button>
      ))}
    </nav>
  );
}

export function NowView() {
  const q = useQuery({ queryKey: ["now"], queryFn: api.now, refetchInterval: 30_000 });
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-5 px-4 py-4 sm:px-6 sm:py-5" data-testid="view-now">
      {q.isPending ? (
        <Loading what="the ledger" />
      ) : q.isError ? (
        <Problem error={q.error} />
      ) : (
        <>
          <Banner m={q.data} />
          <Glance m={q.data} />
          <div className="grid gap-6 lg:grid-cols-3 lg:gap-5">
            <Section title="Waiting on you" count={q.data.waiting_on_you.length} t="human" testid="col-waiting">
              {q.data.waiting_on_you.length ? q.data.waiting_on_you.map((w) => <Waiting key={w.id} w={w} />) : <Empty>Nothing waits on you.</Empty>}
            </Section>
            <Section title="Stalled" count={q.data.stalls.length} t="stall" testid="col-stalls" note="acknowledged: a later turn by that role">
              {q.data.stalls.length ? q.data.stalls.map((s) => <StallCard key={s.id} s={s} />) : <Empty>No work has stalled.</Empty>}
            </Section>
            <Section
              title="Health"
              count={q.data.health.schedules.length + (!q.data.health.onloopd || q.data.health.onloopd.stale ? 1 : 0)}
              t={q.data.health.schedules.length ? "blocked" : "done"}
              testid="col-health"
            >
              <Health h={q.data.health} now={q.data.now} />
            </Section>
          </div>
        </>
      )}
    </div>
  );
}
