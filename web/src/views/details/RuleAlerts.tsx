import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { homeApi, type RulesModel } from "@/lib/api";
import { useAnswers } from "@/lib/answers";
import { Button } from "@/components/ui/button";
import { href } from "@/lib/route";
import { Loading, Problem } from "@/components/kit";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/basics";

// Details · Rule alerts (0.7.0): the system's own rule alerts, counted, one click down from Home. What 0.6's Now showed
// as rows on the first screen (asks with no effect stated, turns that ended without a valid outcome line, the log since
// the last visit, the constraint in the system's words) is here as counts, each with where its rows are.

function byRole(m: Record<string, number>): string {
  return Object.entries(m)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([r, n]) => `${r} ${n}`)
    .join(" · ");
}

function Row({ count, words, sub, to, testid }: { count: number; words: string; sub?: ReactNode; to?: string; testid: string }) {
  return (
    <li className="flex items-baseline gap-3 py-2.5" data-testid={testid}>
      <span className="w-10 shrink-0 text-right text-lg font-semibold tabular-nums" data-testid="count">
        {count}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{words}</span>
        {sub && <span className="block text-sm text-muted">{sub}</span>}
      </span>
      {to && (
        <a href={to} className="shrink-0 text-sm text-accent hover:underline">
          See them
        </a>
      )}
    </li>
  );
}

function Tidy({ ids }: { ids: string[] }) {
  const { tidy, busy, error } = useAnswers();
  if (!ids.length) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-3 pl-[52px] pb-2">
      <Button size="sm" variant="outline" disabled={busy === "tidy"} onClick={() => void tidy(ids)} data-testid="tidy">
        Set aside these {ids.length}
      </Button>
      <span className="text-xs text-muted">The asks with no effect: each is closed as "format rule, no effect stated", with an undo; onloop never writes an effect for them.</span>
      {error?.id === "tidy" && <span className="text-xs text-danger" role="alert">{error.text}</span>}
    </div>
  );
}

function Body({ m }: { m: RulesModel }) {
  const s = m.since_visit;
  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>Asks and turns that broke a format rule</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-line">
            <Row
              testid="rule-asks-missing"
              count={m.asks_missing.count}
              words="asks that do not say what they change or what waits on them"
              sub={
                m.asks_missing.count
                  ? `${byRole(m.asks_missing.by_role)}. Missing: ${Object.entries(m.asks_missing.missing)
                      .map(([w, n]) => `${w} ${n}`)
                      .join(", ")}. They still wait on you; Home lists only the asks that say what they change.`
                  : "Every open ask says what it changes."
              }
              to={href("audit", { kind: "task.opened" })}
            />
            <li className="list-none">
              <Tidy ids={m.asks_missing.ids} />
            </li>
            {m.alerts.map((a) => (
              <Row key={a.detector} testid={`rule-${a.detector}`} count={a.count} words={a.words} sub={byRole(a.by_role)} to={href("audit", { kind: "alert.fire" })} />
            ))}
            {!m.alerts.length && <Row testid="rule-none" count={0} words="open rule alerts" sub="No detector has an open alert." />}
            <Row testid="rule-expiring" count={m.expiring} words="items that expire within a day, or already have" to={href("map")} />
          </ul>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Scheduled jobs and the dead-man switch</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-line" data-testid="health">
            <Row testid="health-failing" count={m.health.failing} words="scheduled jobs failing" to={href("schedules")} />
            <Row testid="health-late" count={m.health.late} words="scheduled jobs overdue" to={href("schedules")} />
            <Row testid="health-ok" count={m.health.ok} words={`of ${m.health.total} jobs within their period`} />
            <li className="py-2.5 pl-[52px] text-sm" data-testid="dead-man">
              {m.health.dead_man.configured
                ? `The dead-man switch (${m.health.dead_man.schedule}) is ${m.health.dead_man.state === "ok" ? "OK" : m.health.dead_man.state}.`
                : "The dead-man switch is not configured (heartbeat_schedule in onloopd.yaml)."}
            </li>
          </ul>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Since you last looked</CardTitle>
          <span className="text-xs text-muted">{s.since ? new Date(s.since).toLocaleString(undefined, { hour12: false, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "no visit recorded"}</span>
        </CardHeader>
        <CardContent>
          <ul className="divide-y divide-line" data-testid="since-visit">
            <Row testid="since-added" count={s.added} words="asks added" />
            <Row testid="since-resolved" count={s.resolved} words="resolved by you" />
            <Row testid="since-without-you" count={s.resolved_without_you} words="resolved without you" />
            <Row testid="since-returned" count={s.returned} words="returned to their author" />
            <Row testid="since-gaps" count={s.gaps} words="times onloop was not running" />
          </ul>
        </CardContent>
      </Card>
      {m.constraint && (
        <Card>
          <CardHeader>
            <CardTitle>The constraint, in the ledger's terms</CardTitle>
          </CardHeader>
          <CardContent>
            <ul>
              <Row testid="rule-constraint" count={m.constraint.waiters} words={`waits on ${m.constraint.on ?? "one holder"}, the most waited-on holder`} sub="The map shows who holds what, and why." to={href("map")} />
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export function RuleAlertsView() {
  const q = useQuery({ queryKey: ["rules"], queryFn: homeApi.rules });
  return (
    <div className="mx-auto max-w-[960px] px-4 py-4 sm:px-6 sm:py-5" data-testid="view-rules">
      <h1 className="text-lg font-semibold">Rule alerts</h1>
      <p className="mb-4 text-[13px] text-muted">The system's own checks, counted. None of it is on Home: each count says where its rows are.</p>
      {q.error && !q.data ? <Problem error={q.error} /> : q.data ? <Body m={q.data} /> : <Loading what="the rule alerts" />}
    </div>
  );
}
