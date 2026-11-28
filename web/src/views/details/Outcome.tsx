import { useQuery } from "@tanstack/react-query";
import { tasksApi, type GoalKpi, type KpiModel, type Spread } from "@/lib/api";
import { Card, Empty, Loading, Meta, Problem, Section } from "@/components/kit";
import { cn } from "@/lib/utils";
import { href } from "@/lib/route";

// The outcome KPIs (decision 0014): progress per unit of work, not a count of units. Every number on this page is shown
// with what it is counted over and when that was set, so a split piece of work shows as a cost and is never rewarded.

const WINDOWS = ["7d", "14d", "28d"] as const;

function day(ts: string | null): string {
  return ts ? new Date(ts).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "not read yet";
}

function Big({ value, unit, testid }: { value: string; unit?: string; testid?: string }) {
  return (
    <p className="text-[28px] font-semibold leading-none tabular-nums" data-testid={testid}>
      {value}
      {unit && <span className="ml-1 text-base font-medium text-muted">{unit}</span>}
    </p>
  );
}

function GoalCard({ id, g, merged }: { id: string; g: GoalKpi; merged: number }) {
  return (
    <Card t="done" testid={`goal-${id}`}>
      <h3 className="text-[15px] font-semibold">Goal ticks per merged PR <span className="font-mono text-muted">{id}</span></h3>
      <div className="mt-2 flex flex-wrap items-end gap-x-6 gap-y-2">
        <Big
          value={g.merged_prs_per_tick == null ? "—" : String(g.merged_prs_per_tick)}
          unit={g.merged_prs_per_tick == null ? undefined : "merged PRs per tick"}
          testid="prs-per-tick"
        />
        <p className="text-[14px] leading-snug" data-testid="ticks-denominator">
          <span className="font-semibold">{g.ticks}</span> {g.ticks === 1 ? "tick" : "ticks"} /{" "}
          <span className="font-semibold">{merged}</span> merged PRs
          {g.per_merged_pr != null && <span className="text-muted"> = {g.per_merged_pr.toFixed(2)} ticks per PR</span>}
          {merged === 0 && <span className="text-muted"> (none merged in the window)</span>}
          {merged > 0 && g.ticks === 0 && <span className="text-muted"> (no tick in the window)</span>}
        </p>
      </div>
      <Meta>
        <span data-testid="checklist-size">
          {g.denominators_in_window.length > 0 && g.denominator_before != null
            ? `checklist: ${g.denominator_before} items, then ${g.denominators_in_window.map((d) => `${d.count} from ${day(d.at)}`).join(", then ")}`
            : `checklist of ${g.denominator ?? "?"} items, as of ${day(g.denominator_at)}`}
        </span>
      </Meta>
      {(g.ticks_without_evidence > 0 || g.unticks > 0) && (
        <p className="mt-1 text-[13px] text-state-stall">
          {g.ticks_without_evidence > 0 && (
            <>
              {g.ticks_without_evidence} of the {g.ticks} ticks {g.ticks_without_evidence === 1 ? "has" : "have"} no comment that names the item.{" "}
            </>
          )}
          {g.unticks > 0 && <>{g.unticks} unticked in the window.</>}
        </p>
      )}
    </Card>
  );
}

function SpreadLine({ label, s, unit }: { label: string; s: Spread; unit?: string }) {
  return (
    <p className="flex flex-wrap items-baseline gap-x-2 text-[14px]">
      <span className="w-[170px] shrink-0 text-muted max-sm:w-full">{label}</span>
      <span className="font-semibold tabular-nums">{s.median == null ? "—" : `${s.median}${unit ?? ""}`}</span>
      <span className="text-[13px] text-muted">median · p90 {s.p90 == null ? "—" : `${s.p90}${unit ?? ""}`} · over {s.n} PRs</span>
    </p>
  );
}

function Body({ k }: { k: KpiModel }) {
  const ls = k.linked_share;
  const goals = Object.entries(k.goals);
  return (
    <div className="grid gap-6 lg:grid-cols-2 lg:gap-5">
      <Section title="Progress" t="done" testid="col-progress">
        {goals.length ? goals.map(([id, g]) => <GoalCard key={id} id={id} g={g} merged={k.merged_prs} />) : <Empty>No goal checklist is read yet (goals: in onloopd.yaml).</Empty>}
        <Card t="neutral" testid="linked-share">
          <h3 className="text-[15px] font-semibold">What the merged PRs moved</h3>
          {ls.merged ? (
            <>
              <div className="mt-2 flex h-3 overflow-hidden rounded-full bg-raised" aria-hidden>
                <span className="bg-state-done" style={{ width: `${(100 * ls.linked) / ls.merged}%` }} />
                <span className="bg-state-void" style={{ width: `${(100 * ls.none) / ls.merged}%` }} />
              </div>
              <p className="mt-2 text-[14px]" data-testid="linked-text">
                <span className="font-semibold">{ls.linked}</span> of {ls.merged} linked to a goal item · <span className="font-semibold">{ls.none}</span> process (<code>Goal: none</code>) ·{" "}
                <span className={cn("font-semibold", ls.unlinked && "text-state-stall")}>{ls.unlinked}</span> said nothing
              </p>
            </>
          ) : (
            <p className="mt-1 text-[14px] text-muted">No merged PR in the window.</p>
          )}
        </Card>
      </Section>
      <Section title="Fixed cost per PR" t="stall" testid="col-cost">
        <Card t="stall" testid="fixed-cost">
          {k.merged_prs ? (
            <div className="flex flex-col gap-1.5">
              <SpreadLine label="queue (ready → merged)" s={k.fixed_cost.queue_hours} unit=" h" />
              <SpreadLine label="review rounds" s={k.fixed_cost.review_rounds} />
              <SpreadLine label="human touches" s={k.fixed_cost.human_touches} />
              {k.fixed_cost.unmeasured.length > 0 && <p className="text-[13px] text-muted">{k.fixed_cost.unmeasured.length} PR(s) without a start in the log, not measured.</p>}
            </div>
          ) : (
            <p className="text-[14px] text-muted">No merged PR in the window.</p>
          )}
        </Card>
        <Card t="neutral" testid="flow-load">
          <h3 className="text-[15px] font-semibold">Flow load</h3>
          <p className="mt-1 text-[14px]">
            <span className="font-semibold">{k.flow_load.open}</span> open PRs at the window's end ·{" "}
            <span className={cn("font-semibold", k.flow_load.idle_7d && "text-state-stall")}>{k.flow_load.idle_7d}</span> with no push for 7 days
          </p>
        </Card>
        <details className="px-1 text-[13px] text-muted">
          <summary className="cursor-pointer">How each number is counted</summary>
          <ul className="mt-1 list-disc pl-5">
            {Object.entries(k.definitions).map(([name, text]) => <li key={name}>{text}</li>)}
          </ul>
        </details>
      </Section>
    </div>
  );
}

export function OutcomeView({ params }: { params: URLSearchParams }) {
  const window = (WINDOWS as readonly string[]).includes(params.get("window") ?? "") ? (params.get("window") as string) : "7d";
  const q = useQuery({ queryKey: ["kpi", window], queryFn: () => tasksApi.kpi(window), refetchInterval: 60_000 });
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-5 px-4 py-4 sm:px-6 sm:py-5" data-testid="view-outcome">
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[22px] font-semibold max-sm:text-[19px]">Outcome</h1>
        {q.data && <p className="text-[13px] text-muted" data-testid="window">{day(q.data.since)} to {day(q.data.until)}, {q.data.merged_prs} merged PRs</p>}
        <nav className="ml-auto flex gap-1" aria-label="Window">
          {WINDOWS.map((w) => (
            <a key={w} href={href("outcome", { window: w })} className={cn("rounded-ui px-2.5 py-1 text-[13px]", w === window ? "bg-raised font-medium" : "text-muted hover:bg-raised")}>
              {w}
            </a>
          ))}
        </nav>
      </header>
      {q.isPending ? <Loading what="the KPIs" /> : q.isError ? <Problem error={q.error} /> : <Body k={q.data} />}
    </div>
  );
}
