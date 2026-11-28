import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ArrowUpRight, Check, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import { homeApi, type HomeDecision, type HomeModel } from "@/lib/api";
import { duration } from "@/lib/format";
import { href } from "@/lib/route";
import { isUrl } from "@/lib/url";
import { useAnswers } from "@/lib/answers";
import { askerName, avatarOf } from "@/lib/roleColor";
import { cn } from "@/lib/utils";
import { Problem } from "@/components/kit";
import { LanesLegend, LanesPicture, RangeTabs } from "@/components/Lanes";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Avatar, Card, CardContent, CardHeader, CardTitle, Skeleton } from "@/components/ui/basics";
import { Tooltip } from "@/components/ui/tooltip";

// Home (0.7.0 plan, sections 1 and 2): is it going well, what needs me, what came out. Prototype F's Home, on the
// ledger: the brief (facts only until the smart layer lands), three numbers against last week, the decisions in their
// one order, what is unusual this week, and the lanes as the evidence. Rule alerts are counted under Details, never
// listed here. Every number is GET /api/home's, which agrees with the views one click down (tests/test_agreement.py).

function greeting(d: Date): string {
  const h = d.getHours();
  return h < 5 ? "Good evening" : h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function fmtK(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

function Delta({ v, good, unit = "" }: { v: number | null; good: "up" | "down"; unit?: string }) {
  if (v == null) return null;
  if (Math.abs(v) < 0.5) return <span className="text-xs font-medium text-muted num">no change</span>;
  const better = good === "up" ? v > 0 : v < 0;
  const I = v > 0 ? ArrowUp : ArrowDown;
  return (
    <span className={cn("inline-flex items-center gap-0.5 whitespace-nowrap text-xs font-medium num", better ? "text-done" : "text-danger")} data-testid="delta">
      <I className="size-3" aria-hidden />
      <span className="sr-only">{v > 0 ? "up" : "down"} </span>
      {Math.round(Math.abs(v))}
      {unit}
    </span>
  );
}

function Spark({ values, color }: { values: number[]; color: string }) {
  const w = 96;
  const h = 28;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const pts = values.map((v, i) => `${(i / Math.max(1, values.length - 1)) * w},${h - 3 - ((v - min) / (max - min || 1)) * (h - 6)}`).join(" ");
  return (
    <svg width={w} height={h} aria-hidden className="shrink-0">
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function Kpi({ label, value, delta, sub, spark, color, to, testid }: { label: string; value: string; delta: ReactNode; sub: string; spark: number[]; color: string; to?: string; testid: string }) {
  const body = (
    <>
      <div className="flex items-center justify-between truncate text-xs text-muted sm:text-sm">
        {label}
        {to && <ArrowUpRight className="size-3.5 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />}
      </div>
      <div className="mt-1 flex items-end justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xl font-semibold tracking-tight num sm:text-2xl" data-testid="kpi-value">{value}</div>
          <div className="mt-0.5 flex min-h-4 items-center gap-2 text-xs text-muted">{delta}</div>
        </div>
        <span className="hidden md:block">
          <Spark values={spark} color={color} />
        </span>
      </div>
      <div className="mt-2 hidden truncate text-xs text-muted sm:block">{sub}</div>
    </>
  );
  const cls = "group min-w-0 rounded-lg border border-line bg-surface p-3 transition-colors sm:p-4";
  return to ? (
    <a href={to} className={cn(cls, "hover:bg-hover/60")} data-testid={testid}>
      {body}
    </a>
  ) : (
    <div className={cls} data-testid={testid}>
      {body}
    </div>
  );
}

function vsLastWeek(young: boolean, delta: ReactNode): ReactNode {
  return young ? (
    <span title="This ledger holds less than two weeks of data, so there is no week before to compare with">
      <span className="sm:hidden">new</span>
      <span className="max-sm:hidden">no week before yet</span>
    </span>
  ) : (
    <>
      {delta}
      <span className="hidden sm:inline">vs last week</span>
    </>
  );
}

function Numbers({ m, onWaited }: { m: HomeModel; onWaited: () => void }) {
  const { delivered: d, waited_h: w, cost_per_delivery: c } = m.numbers;
  const open = m.decisions.waiting_on_you;
  const oldest = m.decisions.oldest_s;
  return (
    <div className="grid grid-cols-3 gap-2 sm:gap-3" data-testid="numbers">
      <Kpi
        testid="kpi-delivered"
        label="Delivered"
        value={d.recorded ? String(d.value) : "—"}
        delta={d.recorded ? vsLastWeek(m.young, <Delta v={d.delta} good="up" />) : <span>none recorded</span>}
        sub={d.recorded ? `${d.value} ${d.value === 1 ? "change" : "changes"} merged · 7 days` : "connect the code host's ingest to count merges"}
        spark={d.by_day.reduce<number[]>((acc, n) => [...acc, (acc.at(-1) ?? 0) + n], [])}
        color="hsl(var(--done))"
        to={href("timeline")}
      />
      <button type="button" onClick={onWaited} className="contents text-left">
        <Kpi
          testid="kpi-waited"
          label="Waited on you"
          value={`${Math.round(w.value)} h`}
          delta={vsLastWeek(m.young, <Delta v={w.delta} good="down" unit=" h" />)}
          sub={`${open} open${oldest != null ? ` · oldest ${duration(oldest)}` : ""} · summed`}
          spark={w.by_day}
          color="hsl(var(--wait))"
        />
      </button>
      <Kpi
        testid="kpi-cost"
        label="Per delivery"
        value={c.value != null ? fmtK(c.value) : "—"}
        delta={c.value != null ? vsLastWeek(m.young, <Delta v={c.delta_pct} good="down" unit="%" />) : <span>no delivery to divide by</span>}
        sub={`${fmtK(c.tokens)} tokens this week`}
        spark={c.by_day}
        color="hsl(var(--work))"
        to={href("team")}
      />
    </div>
  );
}

function Brief({ m, onDecide }: { m: HomeModel; onDecide: (id: string) => void }) {
  const top = m.decisions.items[0];
  return (
    <Card className="relative overflow-hidden" data-testid="brief">
      <div className="absolute inset-y-0 left-0 w-0.5 bg-gradient-to-b from-work to-done" aria-hidden />
      <CardContent className="flex flex-col gap-4 pt-5 md:flex-row md:items-start md:justify-between">
        <div className="max-w-[760px]">
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted">
            Today's brief
            <Tooltip content="Until onloop writes the brief (a later step of 0.7.0), it is these facts, word for word from the numbers on this page.">
              <span tabIndex={0} className="rounded-sm">
                <Badge tone="soon">facts only</Badge>
              </span>
            </Tooltip>
          </div>
          <p className="text-lg leading-7 text-fg" data-testid="brief-facts">
            {m.brief.facts.map((s, i) => (
              <span key={i}>{s} </span>
            ))}
          </p>
          <p className="mt-2 text-xs text-muted">
            From the ledger, {m.frozen ? `a frozen copy up to ${new Date(m.to).toLocaleString(undefined, { hour12: false, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : "as of now"}.
          </p>
        </div>
        {top && (
          <div className="flex shrink-0 gap-2 md:flex-col md:items-stretch">
            {/* one primary action, with a short label that is never cut: the generated short form comes with step 5 */}
            <Button onClick={() => onDecide(top.id)} data-testid="decide-first" title={top.title}>
              Decide the first of {m.decisions.count}
              <ChevronRight aria-hidden />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[140px_1fr] sm:gap-3">
      <dt className="text-xs text-muted sm:pt-0.5">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function DecisionRow({ d, first, open, onToggle }: { d: HomeDecision; first: boolean; open: boolean; onToggle: () => void }) {
  const { answer, busy, error } = useAnswers();
  const who = askerName(d.asked_by);
  return (
    <li className={cn("rounded-md", open && "bg-hover/50")} data-testid="decision" data-id={d.id}>
      <button type="button" onClick={onToggle} aria-expanded={open} className="group flex w-full items-center gap-3 rounded-md px-2 py-2.5 text-left hover:bg-hover/60">
        <Avatar {...avatarOf(d.asked_by)} size={28} />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="min-w-0 max-w-full truncate text-sm font-medium" data-testid="decision-title">{d.title}</span>
            {first && <Badge tone="solid">Do first</Badge>}
            {d.asks > 1 && <Badge tone="outline">asked {d.asks}×</Badge>}
            {d.days_left === 0 && <Badge tone="wait">costing now</Badge>}
          </span>
          <span className="mt-0.5 block truncate text-sm text-muted">
            {d.holds ? `holds ${d.holds}` : d.cost_text ?? "nothing said to wait on it"} · {who} · {duration(d.age_s)}
          </span>
        </span>
        {open ? <ChevronDown className="size-4 shrink-0 text-muted" aria-hidden /> : <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden />}
      </button>
      {open && (
        <div className="space-y-3 px-2 pb-3 pl-12" data-testid="decision-facts">
          <dl className="space-y-2">
            {d.effect_text && d.effect_text !== d.title && <Fact label="What it changes">{d.effect_text}</Fact>}
            <Fact label="What waits on it">{d.holds ?? "not said"}</Fact>
            <Fact label="If nobody answers">{d.default ?? "not said"}</Fact>
            {d.cost_text && <Fact label="Cost of delay">{d.cost_text}</Fact>}
            <Fact label="Asked by">
              {who}, {duration(d.age_s)} ago
            </Fact>
            {d.ref && (
              <Fact label="About">
                {isUrl(d.ref) ? (
                  <a className="inline-flex items-center gap-1 text-accent hover:underline" href={d.ref} target="_blank" rel="noopener noreferrer">
                    open <ExternalLink className="size-3" aria-hidden />
                  </a>
                ) : (
                  <span className="font-mono text-xs">{d.ref}</span>
                )}
              </Fact>
            )}
          </dl>
          <div className="flex flex-wrap items-center gap-2">
            {d.origin === "gate" ? (
              <span className="text-sm text-muted">Approve it on the code host; onloop sees the approval.</span>
            ) : d.origin === "pause" ? (
              <code className="rounded bg-raised px-1.5 py-0.5 text-xs">onloop role resume {d.ref?.replace(/^role:/, "")}</code>
            ) : d.options.length ? (
              <>
                {d.options.map((o) => (
                  <Button key={o} size="sm" variant="outline" disabled={busy === d.id} onClick={() => void answer(d.id, d.asked_by, "done", o)} data-testid="option">
                    {o}
                  </Button>
                ))}
                <Button size="sm" variant="ghost" disabled={busy === d.id} onClick={() => void answer(d.id, d.asked_by, "declined")} data-testid="mark-declined">
                  Decline
                </Button>
              </>
            ) : (
              <>
                <Button size="sm" variant="outline" disabled={busy === d.id} onClick={() => void answer(d.id, d.asked_by, "done")} data-testid="mark-done">
                  <Check aria-hidden />
                  Done
                </Button>
                <Button size="sm" variant="ghost" disabled={busy === d.id} onClick={() => void answer(d.id, d.asked_by, "declined")} data-testid="mark-declined">
                  Decline
                </Button>
                <span className="text-xs text-muted">{who} sees your answer at its next prompt.</span>
              </>
            )}
          </div>
          {error?.id === d.id && <p className="text-sm text-danger" role="alert">{error.text}</p>}
        </div>
      )}
    </li>
  );
}

const SHOWN = 5;

function Decisions({ m, open, setOpen }: { m: HomeModel; open: string | null; setOpen: (id: string | null) => void }) {
  const [all, setAll] = useState(false);
  const { items, count, rule_asks } = m.decisions;
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <Card id="decisions" data-testid="decisions">
      <CardHeader>
        <CardTitle>
          Decisions <span className="ml-1 font-normal text-muted num" data-testid="decisions-count">{count}</span>
        </CardTitle>
        <span className="text-xs text-muted max-sm:hidden">in order: weight, then when the delay costs, then age</span>
      </CardHeader>
      <CardContent className="pb-3">
        {items.length ? (
          <ul className="-mx-2">
            {shown.map((d, i) => (
              <DecisionRow key={d.id} d={d} first={i === 0} open={open === d.id} onToggle={() => setOpen(open === d.id ? null : d.id)} />
            ))}
          </ul>
        ) : (
          <div className="flex flex-col items-center px-6 py-8 text-center" data-testid="decisions-empty">
            <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-raised text-muted">
              <Check className="size-5" aria-hidden />
            </div>
            <div className="text-sm font-medium">Nothing waits for you</div>
            <p className="mt-1 max-w-[320px] text-sm text-muted">New decisions appear here in their one order.</p>
          </div>
        )}
        <div className="mt-1 flex items-center gap-3">
          {items.length > SHOWN && (
            <Button variant="link" size="sm" onClick={() => setAll((a) => !a)} data-testid="decisions-all">
              {all ? "Show the first 5" : `Show all ${items.length}`}
            </Button>
          )}
          <a href={href("decisions")} className="ml-auto text-sm font-medium hover:underline" data-testid="all-decisions">
            All decisions
          </a>
        </div>
        {rule_asks > 0 && (
          <p className="mt-2 border-t border-line pt-3 text-xs text-muted" data-testid="rule-asks">
            {rule_asks} more {rule_asks === 1 ? "ask does" : "asks do"} not say what {rule_asks === 1 ? "it changes" : "they change"} or what waits on {rule_asks === 1 ? "it" : "them"}: counted under{" "}
            <a href={href("rules")} className="text-accent hover:underline">
              Details · Rule alerts
            </a>
            .
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Unusual({ m }: { m: HomeModel }) {
  return (
    <Card data-testid="unusual" className="self-start">
      <CardHeader>
        <CardTitle>Unusual this week</CardTitle>
      </CardHeader>
      <CardContent>
        {m.unusual.length ? (
          <ol className="space-y-3">
            {m.unusual.slice(0, 5).map((a, i) => (
              <li key={a.id}>
                <a href={a.kind === "wait" ? "#decisions" : href("team")} className="group flex gap-3" data-testid="unusual-item">
                  <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full border border-fg text-[11px] font-semibold">{i + 1}</span>
                  <span>
                    <span className="block text-sm font-medium group-hover:underline">{a.title}</span>
                    <span className="block text-sm text-muted">{a.text}</span>
                  </span>
                </a>
              </li>
            ))}
          </ol>
        ) : (
          <div data-testid="unusual-empty">
            <p className="text-sm font-medium">Nothing unusual this week</p>
            <p className="mt-1 text-sm text-muted">
              Each role is held to its own last 14 days: how long it goes quiet, its tokens in a day, and how long its asks wait. Three times its usual is unusual.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---- the lanes ----------------------------------------------------------------------------------------------------

const RANGES = [
  [24, "24 h"],
  [48, "48 h"],
  [168, "7 d"],
] as const;

function Lanes({ m }: { m: HomeModel }) {
  const [range, setRange] = useState<number>(168);
  return (
    <Card data-testid="lanes">
      <CardHeader>
        <CardTitle>Who worked when</CardTitle>
        <RangeTabs value={range} options={RANGES} onChange={setRange} />
      </CardHeader>
      <CardContent>
        <LanesPicture lanes={m.lanes.lanes} flags={m.numbers.delivered.items} to={m.to} hours={range} />
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <LanesLegend />
          <a href={href("timeline")} className="text-sm font-medium hover:underline">
            Open the timeline
          </a>
        </div>
      </CardContent>
    </Card>
  );
}

// ---- the page -----------------------------------------------------------------------------------------------------

function HomeLoading() {
  return (
    <div className="space-y-5" data-testid="loading">
      <Skeleton className="h-7 w-48" />
      <Skeleton className="h-28" />
      <div className="grid grid-cols-3 gap-3">
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

export function HomeView() {
  const q = useQuery({ queryKey: ["home"], queryFn: homeApi.home, refetchInterval: 60_000 });
  const [open, setOpen] = useState<string | null>(null);
  const decide = (id: string) => {
    setOpen(id);
    requestAnimationFrame(() => document.querySelector(`[data-testid="decision"][data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" }));
  };
  const m = q.data;
  return (
    <div className="mx-auto w-full max-w-[1320px] px-4 py-6 md:px-8" data-testid="view-home">
      {!m && q.error ? (
        <Problem error={q.error} />
      ) : !m ? (
        <HomeLoading />
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="text-xl font-semibold tracking-tight">{greeting(new Date())}</h1>
              <p className="mt-0.5 text-sm text-muted">
                {new Date(m.to).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })} · the last 7 days
              </p>
            </div>
          </div>
          <Brief m={m} onDecide={decide} />
          <Numbers m={m} onWaited={() => document.getElementById("decisions")?.scrollIntoView({ block: "start", behavior: "smooth" })} />
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
            <Decisions m={m} open={open} setOpen={setOpen} />
            <Unusual m={m} />
          </div>
          <Lanes m={m} />
        </div>
      )}
    </div>
  );
}
