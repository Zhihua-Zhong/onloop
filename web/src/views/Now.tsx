import { useMemo } from "react";
import { isUrl } from "@/lib/url";
import { useQuery } from "@tanstack/react-query";
import { useNowMs, useTicking } from "@/lib/clock";
import { useHeartbeat } from "@/lib/heartbeat";
import { currentBeat } from "@/lib/tick";
import { ArrowRight, ExternalLink } from "lucide-react";
import { api, type NowModel, type Stall, type TaskCard, type WaitCard, type YourWait } from "@/lib/api";
import { DigestBand, TaskList, TaskRows } from "@/views/Tasks";
import { ago, clock, duration, plural, reason } from "@/lib/format";
import { href } from "@/lib/route";
import { constraintSentence } from "@/lib/constraint";
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
            {constraintSentence({ on_kind: c.on_kind, on_ref: c.on_ref, waiters: n })}
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
              {isUrl(m.next.ref) && (
                <a href={m.next.ref} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-accent hover:underline" data-testid="next-link">
                  Open it <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                </a>
              )}
              <a href={href("map")} className="inline-flex items-center gap-1 text-accent hover:underline" data-testid="see-on-map">
                See it on the map <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              </a>
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

// When tasks wait on the person, the list is Now's primary area and the constraint is one line above it (vision round 3).
function CompactBanner({ m }: { m: NowModel }) {
  const c = m.constraint;
  if (!c) return null;
  const n = c.waiters.length;
  return (
    <section
      data-testid="constraint"
      aria-label="Biggest constraint"
      className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 rounded-ui border border-state-blocked/45 bg-state-blocked/[0.07] px-3 py-1.5 text-[13px] dark:bg-state-blocked/[0.12]"
    >
      <span className="text-[11px] font-semibold uppercase tracking-wide text-state-blocked">Constraint</span>
      <span className="font-semibold" data-testid="constraint-headline">
        {constraintSentence({ on_kind: c.on_kind, on_ref: c.on_ref, waiters: n })}
      </span>
      <span className="text-muted max-sm:hidden">{duration(c.direct_wait_s)} waiting in total</span>
      {m.next && (
        <span className="min-w-0 truncate max-sm:hidden" data-testid="next-move">
          <span className="text-muted">next: </span>
          <span data-testid="next-text">{m.next.text}</span>
          {isUrl(m.next.ref) && (
            <a href={m.next.ref} target="_blank" rel="noopener noreferrer" className="ml-1.5 inline-flex items-center gap-0.5 text-accent hover:underline" data-testid="next-link">
              open <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          )}
        </span>
      )}
      <a href={href("map")} className="ml-auto inline-flex items-center gap-1 text-accent hover:underline max-sm:hidden" data-testid="banner-map">
        Map <ArrowRight className="h-3 w-3" aria-hidden />
      </a>
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
            {isUrl(w.ref) && (
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
  if (s.detail.summary) {
    // a team without outcome lines: one fact about the team, not a stalled turn; activity shows whether it moves
    return (
      <Card t="stall" testid={`stall-${s.id}`}>
        <h3 className="text-[15px] font-semibold leading-snug">{s.title}</h3>
        <p className="mt-1 text-[13px] leading-snug text-muted">
          Without them onloop cannot tell what a turn decided or waits on, so it shows when each role worked instead.
        </p>
        <a href={href("replay", { hours: "48" })} className="mt-1.5 inline-block text-[13px] font-medium text-accent hover:underline" data-testid="see-activity">
          See when the team worked
        </a>
      </Card>
    );
  }
  return (
    <Card t="stall" testid={`stall-${s.id}`}>
      <h3 className="text-[15px] font-semibold leading-snug">{s.title}</h3>
      {why && <p className="mt-1 text-[13px] leading-snug"><Ticks text={why} /></p>}
      <Meta>
        <span className="font-medium text-fg">stalled {duration(s.age_s)}</span>
        <Dot />
        <span data-testid="would" className={s.would?.acting ? "font-medium text-state-open" : ""}>
          {s.would ? (s.would.acting ? s.would.label : `${s.would.label}${s.would.target ? `: ${s.would.target}` : ""}`) : "no wake scheduled"}
        </span>
        <Dot />
        <span data-testid="ack" className={s.acknowledged ? "text-state-done" : ""}>
          {s.acknowledged ? `acknowledged ${clock(s.acknowledged.at)}` : "not acknowledged"}
        </span>
      </Meta>
    </Card>
  );
}

const GAP_CAUSE: Record<string, string> = {
  "unclean shutdown": "after the machine lost power or crashed",
  "clean shutdown": "the machine was shut down",
  sleep: "the machine slept",
};

function Health({ h, now, quiet }: { h: NowModel["health"]; now: string; quiet: NowModel["quiet"] }) {
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
      <Card t={!beat || beat.stale ? "blocked" : h.gaps.length ? "human" : "done"} testid="health-onloopd">
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
        {h.gaps.slice(0, 3).map((g) => (
          <p key={g.from} className="mt-1 text-[13px] leading-snug" data-testid="gap">
            <span className="font-medium">
              {g.while_running ? "No record" : "onloop was down"} for {duration(g.seconds)}
            </span>{" "}
            <span className="text-muted">
              ({clock(g.from, now)} to {clock(g.to, now)}
              {g.cause ? `, ${GAP_CAUSE[g.cause] ?? g.cause}` : g.while_running ? ", the machine slept or onloop was paused" : ""}). Nothing was recorded then, so work in that
              time is missing, not quiet.
            </span>
          </p>
        ))}
        {quiet && (
          <p className="mt-1 text-[13px] leading-snug" data-testid="quiet">
            Quiet since {clock(quiet.since, now, true)}
            {quiet.onloop_down_s ? ` (onloop was down for ${duration(quiet.onloop_down_s)} of it)` : ""}:{" "}
            {quiet.state === "idle"
              ? "every role's last outcome is idle; nothing to do."
              : quiet.waits.length
                ? `and waiting: ${quiet.waits.slice(0, 3).map((w) => `${w.waiter} on ${w.on === "human" ? "you" : w.on}`).join(", ")}.`
                : "no role has said what it waits on."}
          </p>
        )}
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

// Tasks first: the one-line constraint, the one-line digest, then the list in its order as the primary area (about two
// thirds of the width), with Stalled and Health in the narrow column beside it, or below it on a phone.
function TasksFirst({ m }: { m: NowModel }) {
  const tasks = m.waiting_on_you as TaskCard[];
  const healthCount = m.health.schedules.length + (!m.health.onloopd || m.health.onloopd.stale ? 1 : 0);
  return (
    <>
      <CompactBanner m={m} />
      {m.your_wait && <YourWaitLine w={m.your_wait} />}
      <DigestBand />
      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:gap-5">
        <Section title="Waiting on you" count={tasks.length} t="human" testid="col-waiting" note={<span className="max-sm:hidden">in order: weight, then when the delay costs, then age</span>}>
          <TaskRows tasks={tasks} now={m.now} />
        </Section>
        <div className="flex min-w-0 flex-col gap-6">
          <Section title="Stalled" count={m.stalls.length} t="stall" testid="col-stalls">
            {m.stalls.length ? m.stalls.map((s) => <StallCard key={s.id} s={s} />) : <Empty>No work has stalled.</Empty>}
            <Expiring m={m} />
          </Section>
          <Section title="Health" count={healthCount} t={m.health.schedules.length ? "blocked" : "done"} testid="col-health">
            <Health h={m.health} now={m.now} quiet={m.quiet} />
          </Section>
        </div>
      </div>
    </>
  );
}

export function NowView() {
  const q = useQuery({ queryKey: ["now"], queryFn: api.now, refetchInterval: 30_000 });
  // the answer as it stands now: its ages rise every second between answers (lib/tick.ts)
  const ticked = useTicking(q.data, q.dataUpdatedAt) as NonNullable<typeof q.data>;
  // onloopd's heartbeat is the page's one reading (the header's), or this answer's when it is newer; never the
  // heartbeat this answer carried, aged on, while a newer one has arrived
  const hb = useHeartbeat();
  const nowMs = useNowMs();
  const m = useMemo(() => {
    if (!ticked) return ticked;
    const own = ticked.health.onloopd;
    const beat = currentBeat(
      [
        { at: own?.at ?? null, age_s: own?.age_s ?? null, fetchedAtMs: nowMs }, // already ticked to now
        { at: hb.data?.at ?? null, age_s: hb.data?.age_s ?? null, fetchedAtMs: hb.dataUpdatedAt },
      ],
      nowMs,
    );
    if (!beat || !own) return ticked;
    return { ...ticked, health: { ...ticked.health, onloopd: { ...own, ...beat } } };
  }, [ticked, hb.data, hb.dataUpdatedAt, nowMs]);
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-5 px-4 py-4 sm:px-6 sm:py-5" data-testid="view-now">
      {q.isPending ? (
        <Loading what="the ledger" />
      ) : q.isError && q.data === undefined ? (
        // a refresh that fails keeps the last answer on screen; the header says onloopd is not answering
        <Problem error={q.error} />
      ) : (
        m.waiting_on_you.length > 0 && m.waiting_on_you[0].kind === "task" ? (
          <TasksFirst m={m} />
        ) : (
        <>
          <DigestBand />
          <Banner m={m} />
          <Glance m={m} />
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)] lg:gap-5">
            <Section title="Waiting on you" count={m.waiting_on_you.length} t="human" testid="col-waiting">
              {m.waiting_on_you.length ? (
                m.waiting_on_you[0].kind === "task" ? (
                  <TaskList tasks={m.waiting_on_you as TaskCard[]} now={m.now} />
                ) : (
                  (m.waiting_on_you as WaitCard[]).map((w) => <Waiting key={w.id} w={w} />)
                )
              ) : (
                <Empty>Nothing waits on you.</Empty>
              )}
            </Section>
            <Section title="Stalled" count={m.stalls.length} t="stall" testid="col-stalls" note="acknowledged: a later turn by that role">
              {m.stalls.length ? m.stalls.map((s) => <StallCard key={s.id} s={s} />) : <Empty>No work has stalled.</Empty>}
            <Expiring m={m} />
            </Section>
            <Section
              title="Health"
              count={m.health.schedules.length + (!m.health.onloopd || m.health.onloopd.stale ? 1 : 0)}
              t={m.health.schedules.length ? "blocked" : "done"}
              testid="col-health"
            >
              <Health h={m.health} now={m.now} quiet={m.quiet} />
            </Section>
          </div>
        </>
        )
      )}
    </div>
  );
}

// The person's wait made visible (decision 0019, a7): the same count as the list below, how long the oldest has
// waited, and how much of the last day anything waited on you. The Replay view shows it for any window.
function YourWaitLine({ w }: { w: YourWait }) {
  return (
    <p className="mb-3 text-[13.5px]" data-testid="your-wait">
      <span className="font-semibold text-state-human">{w.count} waiting on you</span>
      {w.oldest_s != null && w.count > 0 && <span className="text-muted"> · the oldest for {duration(w.oldest_s)}</span>}
      <span className="text-muted">
        {" "}
        · something waited on you {Math.round(w.share * 100)}% of the last 24 h ({duration(w.waited_s)})
      </span>
      {" "}
      <a href={href("replay", { hours: "24" })} className="text-accent hover:underline">
        see it in Replay
      </a>
    </p>
  );
}

// Moved from Flow's Roadmap (decision 0019, section 4): an open item whose expiry is past or within a day.
function Expiring({ m }: { m: NowModel }) {
  if (!m.expiring?.length) return null;
  return (
    <div className="space-y-2" data-testid="expiring">
      <h3 className="pt-1 text-[13px] font-semibold text-muted">
        Expires within a day <span className="tabular-nums">{m.expiring.length}</span>
      </h3>
      {m.expiring.map((x) => (
        <Card key={x.id} t={x.in_s <= 0 ? "blocked" : "human"} testid={`expiring-${x.id}`}>
          <h3 className="text-[15px] font-semibold leading-snug">{x.title}</h3>
          <Meta>
            <span className={x.in_s <= 0 ? "font-medium text-state-blocked" : "font-medium text-state-human"}>
              {x.in_s <= 0 ? `expired ${duration(-x.in_s)} ago` : `expires in ${duration(x.in_s)}`}
            </span>
            {x.ref && (
              <>
                <Dot />
                {isUrl(x.ref) ? (
                  <a href={x.ref} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
                    open
                  </a>
                ) : (
                  <span className="font-mono">{x.ref}</span>
                )}
              </>
            )}
          </Meta>
        </Card>
      ))}
    </div>
  );
}
