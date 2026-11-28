import { useQuery } from "@tanstack/react-query";
import { agentsApi } from "@/lib/api";
import { duration } from "@/lib/format";
import { href, setParams } from "@/lib/route";
import { roleColor } from "@/lib/roleColor";
import { Problem } from "@/components/kit";
import { LanesLegend, LanesPicture, RangeTabs } from "@/components/Lanes";
import { Card, CardContent, CardHeader, CardTitle, Skeleton } from "@/components/ui/basics";
import { fmtK } from "@/views/Agents";

// The Timeline (0.7.0 plan, sections 1 and 7.3): who worked when, where work waited on you, what came out when. 0.6's
// Replay lanes, restyled, with F's flags and numbered callouts (Home's "Unusual this week", placed where each
// happened), over GET /api/timeline, which reads the window only and sums each role's figures from a per-day cache.
// The playback, the cursor and the table stay under Details · Replay.

const RANGES = [
  [1, "24 h"],
  [2, "48 h"],
  [7, "7 d"],
  [14, "14 d"],
] as const;

export function TimelineView({ params }: { params: URLSearchParams }) {
  const days = Number(params.get("days") ?? "7");
  const d = RANGES.some(([v]) => v === days) ? days : 7;
  const q = useQuery({ queryKey: ["timeline", d], queryFn: () => agentsApi.timeline(d), refetchInterval: 30_000 });
  const m = q.data;
  return (
    <div className="mx-auto w-full max-w-[1320px] px-4 py-6 md:px-8" data-testid="view-timeline">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Timeline</h1>
          <p className="mt-0.5 text-sm text-muted">Who worked when, where work waited on you, and what came out.</p>
        </div>
        <RangeTabs value={d} options={RANGES} onChange={(v) => setParams("timeline", { days: v === 7 ? undefined : String(v) })} />
      </div>
      {!m && q.error ? (
        <Problem error={q.error} />
      ) : !m ? (
        <Skeleton className="h-80" />
      ) : (
        <div className="space-y-5">
          <Card data-testid="timeline-lanes">
            <CardContent className="pt-5">
              <LanesPicture lanes={m.lanes} flags={m.flags} to={m.to} hours={m.days * 24} callouts={m.callouts} />
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                <LanesLegend callouts={m.callouts.length > 0} />
                <a href={href("replay", { hours: String(Math.min(168, m.days * 24)) })} className="text-sm font-medium hover:underline" data-testid="to-replay">
                  Play it back, or see it as a table: Details · Replay
                </a>
              </div>
            </CardContent>
          </Card>
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
            <Card data-testid="figures">
              <CardHeader className="flex-wrap">
                <CardTitle>So far in this window</CardTitle>
                <span className="text-xs text-muted max-sm:w-full">
                  {m.your_wait.count} {m.your_wait.count === 1 ? "ask waits" : "asks wait"} on you · something waited on you {Math.round(m.your_wait.share * 100)}% of the time
                </span>
              </CardHeader>
              <CardContent className="overflow-x-auto">
                <table className="w-full min-w-[480px] text-sm">
                  <thead>
                    <tr className="border-b border-line text-left text-xs text-muted">
                      <th className="py-2 font-medium">Role</th>
                      <th className="py-2 text-right font-medium">Working</th>
                      <th className="py-2 text-right font-medium">Waiting on you</th>
                      <th className="py-2 text-right font-medium">Turns</th>
                      <th className="py-2 text-right font-medium">Tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {m.figures.map((f) => (
                      <tr key={f.role} className="border-b border-line last:border-0" data-testid={`figure-${f.role}`}>
                        <td className="py-2">
                          <a href={href("team", {}, [f.role])} className="inline-flex items-center gap-1.5 hover:underline">
                            <span className="size-2 rounded-full" style={{ background: roleColor(f.role) }} aria-hidden />
                            {f.role}
                          </a>
                        </td>
                        <td className="py-2 text-right num">{f.working_s ? duration(f.working_s) : "—"}</td>
                        <td className="py-2 text-right num">{f.waiting_s ? duration(f.waiting_s) : "—"}</td>
                        <td className="py-2 text-right num" data-testid="figure-turns">{f.turns}</td>
                        <td className="py-2 text-right num" data-testid="figure-tokens" data-n={f.tokens}>{fmtK(f.tokens)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
            <Card data-testid="callouts">
              <CardHeader>
                <CardTitle>Unusual in this window</CardTitle>
              </CardHeader>
              <CardContent>
                {m.callouts.length ? (
                  <ol className="space-y-3">
                    {m.callouts.map((c) => (
                      <li key={c.id} className="flex gap-3">
                        <span className="mt-0.5 inline-flex size-5 shrink-0 items-center justify-center rounded-full border border-fg text-[11px] font-semibold">{c.n}</span>
                        <span>
                          <span className="block text-sm font-medium">{c.title}</span>
                          <span className="block text-sm text-muted">{c.text}</span>
                        </span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="text-sm text-muted">Nothing unusual: each role stayed within three times its own usual quiet, tokens in a day, and wait on its asks.</p>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
