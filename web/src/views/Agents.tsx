import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Copy } from "lucide-react";
import { agentsApi, type AgentCard, type AgentState } from "@/lib/api";
import { duration } from "@/lib/format";
import { href } from "@/lib/route";
import { roleColor } from "@/lib/roleColor";
import { cn } from "@/lib/utils";
import { Problem } from "@/components/kit";
import { LanesLegend, LanesPicture } from "@/components/Lanes";
import { Badge } from "@/components/ui/badge";
import { Avatar, Card, CardContent, CardHeader, CardTitle, Skeleton, StatusDot, type Status } from "@/components/ui/basics";

// Agents (0.7.0 plan, sections 1 and 2): who is working, on what, at what cost, and is anyone stuck? Prototype F's
// cards on the ledger (GET /api/agents), and one role's page (GET /api/agents/<role>): its week, its decisions, its lane.
// "Stopped" is the only stuck: a carried run that started over an hour ago and did nothing since. A turn that broke a
// format rule never makes a role look stuck; it is counted under Details · Rule alerts.

const DOT: Record<AgentState, Status> = { working: "working", waiting: "waiting", idle: "idle", paused: "quiet", stopped: "quiet" };

export function fmtK(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1000) return `${Math.round(n / 1000)}k`;
  return String(n);
}

function change(now: number, before: number, unit = ""): string | null {
  if (!before && !now) return null;
  if (!before) return "new this week";
  const d = now - before;
  if (d === 0) return "same as last week";
  return `${d > 0 ? "+" : "−"}${unit === "k" ? fmtK(Math.abs(d)) : Math.abs(d)} vs last week`;
}

export function StateLine({ a }: { a: AgentCard }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs", a.state === "stopped" ? "font-medium text-danger" : a.state === "waiting" ? "font-medium text-state-human" : "text-muted")} data-testid="agent-state" data-state={a.state}>
      <StatusDot status={DOT[a.state]} label={a.words} />
      {a.state === "stopped" ? "stopped" : a.words}
    </span>
  );
}

function AgentTile({ a }: { a: AgentCard }) {
  return (
    <a href={href("team", {}, [a.role])} className="group block rounded-lg border border-line bg-surface p-4 transition-colors hover:border-fg/20" data-testid={`agent-${a.role}`}>
      <div className="flex items-start gap-3">
        <Avatar name={a.role} color={roleColor(a.role)} size={36} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-medium">{a.role}</span>
            <StateLine a={a} />
          </div>
          <div className="mt-0.5 truncate text-sm text-muted" data-testid="on-now">
            {a.state === "stopped" ? a.words : a.on_now ?? "nothing recorded yet"}
          </div>
        </div>
        {a.model && <Badge tone="outline">{a.model}</Badge>}
      </div>
      <div className="mt-3 flex items-end justify-between gap-3" data-testid="agent-week">
        <span className="text-xs text-muted">turns by day, 7 days</span>
        <Bars values={a.turns_by_day} label={`${a.role}'s turns`} />
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 border-t border-line pt-3 text-sm">
        <div>
          <dt className="text-xs text-muted">Turns</dt>
          <dd className="font-semibold num" data-testid="agent-turns">{a.turns}</dd>
          <dd className="text-[11px] text-muted">{change(a.turns, a.turns_before)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Tokens</dt>
          <dd className="font-semibold num" data-testid="agent-tokens" data-n={a.tokens}>{fmtK(a.tokens)}</dd>
          <dd className="text-[11px] text-muted">{change(a.tokens, a.tokens_before, "k")}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">{a.delivered ? "Merged" : "Waits on you"}</dt>
          <dd className="font-semibold num">{a.delivered || a.open_asks}</dd>
          <dd className="text-[11px] text-muted">{a.delivered ? "changes, 7 days" : a.open_asks === 1 ? "decision" : "decisions"}</dd>
        </div>
      </dl>
      {a.command && (
        <div className="mt-3 flex items-center gap-2 rounded-md bg-raised px-2 py-1 font-mono text-xs" data-testid="command">
          <Copy className="size-3 text-muted" aria-hidden />
          {a.command}
        </div>
      )}
    </a>
  );
}

export function AgentsView() {
  const q = useQuery({ queryKey: ["agents"], queryFn: agentsApi.all, refetchInterval: 30_000 });
  const m = q.data;
  return (
    <div className="mx-auto w-full max-w-[1320px] px-4 py-6 md:px-8" data-testid="view-agents">
      <div className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight">Agents</h1>
        {m && (
          <p className="mt-0.5 text-sm text-muted" data-testid="agents-sub">
            {m.agents.length} {m.agents.length === 1 ? "role" : "roles"} · the last 7 days · {m.turns} turns · {fmtK(m.tokens)} tokens
          </p>
        )}
      </div>
      {!m && q.error ? (
        <Problem error={q.error} />
      ) : !m ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {m.agents.map((a) => (
            <AgentTile key={a.role} a={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function Bars({ values, label }: { values: number[]; label: string }) {
  const max = Math.max(1, ...values);
  return (
    <svg width={7 * 14} height={28} role="img" aria-label={`${label} by day: ${values.join(", ")}`} className="shrink-0">
      {values.map((v, i) => (
        <rect key={i} x={i * 14} y={28 - Math.max(v ? 3 : 1, (v / max) * 28)} width={10} height={Math.max(v ? 3 : 1, (v / max) * 28)} rx={2} fill={v ? "hsl(var(--work) / .6)" : "hsl(var(--line))"} />
      ))}
    </svg>
  );
}

export function AgentDetailView({ role }: { role: string }) {
  const q = useQuery({ queryKey: ["agent", role], queryFn: () => agentsApi.one(role), refetchInterval: 30_000 });
  const m = q.data;
  return (
    <div className="mx-auto w-full max-w-[1320px] px-4 py-6 md:px-8" data-testid="view-agent">
      <a href={href("team")} className="mb-4 inline-flex items-center gap-1 text-sm text-muted hover:text-fg">
        <ArrowLeft className="size-4" aria-hidden />
        All agents
      </a>
      {!m && q.error ? (
        <Problem error={q.error} />
      ) : !m ? (
        <Skeleton className="h-64" />
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-start gap-3">
            <Avatar name={m.card.role} color={roleColor(m.card.role)} size={44} />
            <div className="min-w-0 flex-1">
              <h1 className="text-xl font-semibold tracking-tight">{m.card.role}</h1>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
                <StateLine a={m.card} />
                <span className="text-sm text-muted" data-testid="on-now">{m.card.state === "stopped" ? m.card.words : m.card.on_now ?? "nothing recorded yet"}</span>
              </div>
            </div>
            {m.card.model && <Badge tone="outline">{m.card.model}</Badge>}
          </div>
          <div className="grid gap-3 sm:grid-cols-3">
            <Card className="p-4">
              <div className="text-xs text-muted">Turns, 7 days</div>
              <div className="mt-1 flex items-end justify-between gap-2">
                <span className="text-2xl font-semibold num" data-testid="detail-turns">{m.card.turns}</span>
                <Bars values={m.by_day.map((d) => d.turns)} label="Turns" />
              </div>
            </Card>
            <Card className="p-4">
              <div className="text-xs text-muted">Tokens, 7 days</div>
              <div className="mt-1 flex items-end justify-between gap-2">
                <span className="text-2xl font-semibold num">{fmtK(m.card.tokens)}</span>
                <Bars values={m.by_day.map((d) => d.tokens)} label="Tokens" />
              </div>
            </Card>
            <Card className="p-4">
              <div className="text-xs text-muted">Changes merged, 7 days</div>
              <div className="mt-1 text-2xl font-semibold num">{m.delivered.length}</div>
              {!m.delivered.length && <div className="text-xs text-muted">none recorded as its own</div>}
            </Card>
          </div>
          <Card data-testid="agent-decisions">
            <CardHeader>
              <CardTitle>
                Its decisions with you <span className="ml-1 font-normal text-muted num">{m.decisions.open.length} open</span>
              </CardTitle>
              <a href={href("decisions")} className="text-sm font-medium hover:underline">
                All decisions
              </a>
            </CardHeader>
            <CardContent>
              {m.decisions.open.length || m.decisions.answered.length ? (
                <ul className="divide-y divide-line">
                  {[...m.decisions.open, ...m.decisions.answered.slice(0, 5)].map((d) => (
                    <li key={d.id} className="flex items-start gap-3 py-2.5 text-sm">
                      <span className="min-w-0 flex-1">
                        <span className="block font-medium">{d.title}</span>
                        <span className="block text-xs text-muted">
                          {d.resolution ? `${d.answer_text}, ${d.delivered_at ? "reached it" : "it sees this at its next prompt"}` : `waiting ${duration(d.age_s)}`}
                          {d.asks > 1 ? ` · asked ${d.asks}×` : ""}
                        </span>
                      </span>
                      {d.resolution ? <Badge tone="done">answered</Badge> : <Badge tone="wait">open</Badge>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted">It has asked you nothing in the last two weeks.</p>
              )}
            </CardContent>
          </Card>
          <Card data-testid="agent-lane">
            <CardHeader>
              <CardTitle>Its week</CardTitle>
              <a href={href("timeline")} className="text-sm font-medium hover:underline">
                Open the timeline
              </a>
            </CardHeader>
            <CardContent>
              {m.lane ? <LanesPicture lanes={[m.lane]} flags={m.delivered} to={m.to} hours={168} label={`${m.card.role}'s week`} /> : <p className="text-sm text-muted">No turn or model request in the last 7 days.</p>}
              <div className="mt-3">
                <LanesLegend />
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
