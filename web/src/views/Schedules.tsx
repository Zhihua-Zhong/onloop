import { useQueries } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api, type Item, type LedgerEvent, type Schedule } from "@/lib/api";
import { ago, clock } from "@/lib/format";
import { href, setParams } from "@/lib/route";
import { Command, Empty, Loading, Pill, Problem, type Tone } from "@/components/kit";
import { cn } from "@/lib/utils";

// Did every scheduled job run, and which are late or failing (blueprint section 4.5). The state is the schedule
// detector's: failing when the last result failed, overdue while its alert is open.

type State = { label: string; t: Tone; rank: number };

function stateOf(s: Schedule, alerts: Item[]): State {
  if (s.last_status === "fail" || s.last_status === "down") return { label: s.last_status === "down" ? "reported down" : "failing", t: "blocked", rank: 0 };
  if (alerts.some((a) => a.id === `alert:schedule:${s.id}`)) return { label: "overdue", t: "human", rank: 1 };
  if (!s.last_result_at) return { label: "no run yet", t: "void", rank: 2 };
  if (s.last_status === "start") return { label: "running", t: "open", rank: 3 };
  return { label: "OK", t: "done", rank: 4 };
}

// How the last runs went, from the job's own results: "3 failures in a row", or "did not run in time".
function lastRun(s: Schedule, st: State, results: LedgerEvent[] | undefined): string {
  if (!s.last_result_at) return "never ran";
  if (st.rank === 1) return `did not run in time (last ${clock(s.last_result_at)})`;
  if (st.rank === 0 && results) {
    let n = 0;
    for (const e of results) {
      const status = (e.payload as { status?: string }).status;
      if (status === "start") continue;
      if (status === "fail" || status === "down") n += 1;
      else break;
    }
    return `${n > 1 ? `${n} failures in a row` : "failed"} (last ${clock(s.last_result_at)})`;
  }
  return `${s.last_status === "success" ? "OK" : s.last_status} at ${clock(s.last_result_at)}`;
}

const COLS = "md:grid-cols-[minmax(11rem,1fr)_9rem_minmax(14rem,1.3fr)_9rem_7rem]";

export function SchedulesView({ params }: { params: URLSearchParams }) {
  const [sq, aq] = useQueries({ queries: [{ queryKey: ["schedules"], queryFn: api.schedules }, { queryKey: ["alerts"], queryFn: api.alerts }] });
  const failing = (sq.data ?? []).filter((s) => s.last_status === "fail" || s.last_status === "down");
  const runs = useQueries({
    queries: failing.map((s) => ({
      queryKey: ["events", "results", s.id],
      queryFn: () => api.events(new URLSearchParams({ kind: "schedule.result", subject: `schedule:${s.id}`, latest: "1", limit: "20" })).then((b) => b.events),
    })),
  });
  const resultsOf = new Map(failing.map((s, i) => [s.id, runs[i]?.data] as const));
  const open = params.get("open");
  const failed = sq.error ?? aq.error;
  const rows = sq.data && aq.data ? sq.data.map((s) => ({ s, st: stateOf(s, aq.data!) })).sort((a, b) => a.st.rank - b.st.rank || a.s.id.localeCompare(b.s.id)) : [];
  const now = Date.now();
  return (
    <div className="mx-auto max-w-[1400px] px-4 py-4 sm:px-6 sm:py-5" data-testid="view-schedules">
      <h1 className="text-lg font-semibold">Schedules</h1>
      <p className="mt-0.5 text-[13px] text-muted">The jobs onloopd.yaml declares and what each last reported, failing first. Open a row for its log tail.</p>
      <div className="mt-4">
        {failed ? (
          <Problem error={failed} />
        ) : !sq.data || !aq.data ? (
          <Loading what="the schedules" />
        ) : rows.length === 0 ? (
          <Empty>No schedules are declared. Add them under <code>schedules:</code> in onloopd.yaml.</Empty>
        ) : (
          <div role="table" aria-label="Schedules" className="overflow-hidden rounded-zone border border-line bg-surface">
            <div role="row" className={cn("grid gap-4 border-b border-line bg-raised px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted max-md:hidden", COLS)}>
              <span role="columnheader">Schedule</span>
              <span role="columnheader">State</span>
              <span role="columnheader">Last run</span>
              <span role="columnheader">Last success</span>
              <span role="columnheader">Period</span>
            </div>
            {rows.map(({ s, st }) => {
              const isOpen = open === s.id;
              const sinceSuccess = s.last_success ? (now - Date.parse(s.last_success)) / 1000 : null;
              const run = lastRun(s, st, resultsOf.get(s.id));
              return (
                <div key={s.id} role="rowgroup" data-testid={`schedule-${s.id}`} className="border-b border-line last:border-b-0">
                  <button
                    type="button"
                    role="row"
                    aria-expanded={isOpen}
                    onClick={() => setParams("schedules", { open: isOpen ? undefined : s.id })}
                    className={cn("grid w-full gap-x-4 gap-y-1 px-4 py-3 text-left hover:bg-raised md:items-center", COLS, st.rank === 0 && "bg-state-blocked/[0.05]")}
                  >
                    <span role="cell" className="flex items-center gap-1.5 font-mono text-[15px] font-semibold">
                      {isOpen ? <ChevronDown className="h-4 w-4 shrink-0 text-muted" aria-hidden /> : <ChevronRight className="h-4 w-4 shrink-0 text-muted" aria-hidden />}
                      <span className="truncate">{s.name}</span>
                      <span className="ml-auto md:hidden"><Pill t={st.t}>{st.label}</Pill></span>
                    </span>
                    <span role="cell" className="max-md:hidden"><Pill t={st.t} testid="job-state">{st.label}</Pill></span>
                    <span role="cell" className={cn("text-[14px] max-md:pl-[22px]", st.rank <= 1 ? "font-medium" : "text-muted")} data-testid="last-run">{run}</span>
                    <span role="cell" className={cn("text-[14px] max-md:pl-[22px] max-md:text-[13px]", st.rank <= 1 ? "" : "text-muted")}>
                      <span className="md:hidden">last success </span>{s.last_success ? ago(sinceSuccess) : "never"}
                      <span className="text-muted md:hidden"> · every {s.period}</span>
                    </span>
                    <span role="cell" className="text-[14px] text-muted max-md:hidden">{s.period ? `every ${s.period}` : "—"}</span>
                  </button>
                  {isOpen && (
                    <div className="border-t border-line bg-raised px-4 py-3" data-testid="log-tail">
                      <p className="text-[13px] text-muted">
                        Owner <span className="font-mono text-fg">{s.owner_role ?? "none"}</span> · grace {s.grace ?? "—"} ·{" "}
                        <a className="text-accent hover:underline" href={href("audit", { subject: `schedule:${s.id}` })}>its events in Audit</a>
                      </p>
                      {s.log_tail ? (
                        <pre className="mt-2 max-h-72 overflow-auto rounded-md border border-line bg-surface p-3 font-mono text-[12.5px] leading-relaxed">{s.log_tail}</pre>
                      ) : (
                        <p className="mt-2 text-[13px] text-muted">The last result sent no log tail.</p>
                      )}
                      <Command cmd={`onloop events --subject schedule:${s.id} --kind schedule.result`} className="max-w-xl" />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
