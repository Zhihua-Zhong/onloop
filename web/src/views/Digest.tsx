import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Sparkles } from "lucide-react";
import { homeApi, tasksApi } from "@/lib/api";
import { duration } from "@/lib/format";
import { href } from "@/lib/route";
import { decisionText, useTexts } from "@/lib/texts";
import { askerName, avatarOf } from "@/lib/roleColor";
import { useRoleColor } from "@/lib/useRoleColor";
import { Problem } from "@/components/kit";
import { WrittenMark } from "@/components/Written";
import { Badge } from "@/components/ui/badge";
import { Avatar, Card, CardContent, CardHeader, CardTitle, Skeleton } from "@/components/ui/basics";

// The morning digest, as a preview (0.7.0 plan, section 5): what to open first today. Nothing is sent: a notification,
// Slack or email waits on the project lead's choice of channel. Everything here is Home's (GET /api/home), the smart
// layer's texts (GET /api/texts) and the visits' digest (GET /api/digest, read only: opening this page records no visit).

const SHOWN = 3;

export function DigestView() {
  const home = useQuery({ queryKey: ["home"], queryFn: homeApi.home, refetchInterval: 60_000 });
  const since = useQuery({ queryKey: ["digest"], queryFn: tasksApi.digest, refetchInterval: 60_000 });
  const t = useTexts();
  const color = useRoleColor();
  const m = home.data;
  const s = since.data;
  const written = t?.brief.text?.sentences ?? null;
  const day = Date.now() - 86_400_000;
  const yesterday = m ? m.numbers.delivered.items.filter((i) => Date.parse(i.at) >= day) : [];
  return (
    <div className="mx-auto w-full max-w-[760px] px-4 py-6 md:px-8" data-testid="view-digest">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Morning digest</h1>
          <p className="mt-0.5 text-sm text-muted">What to open first today.</p>
        </div>
        <Badge tone="soon" data-testid="digest-preview">
          Preview · not sent anywhere yet
        </Badge>
      </div>
      {!m && home.error ? (
        <Problem error={home.error} />
      ) : !m ? (
        <Skeleton className="h-96" />
      ) : (
        <div className="space-y-5">
          <Card data-testid="digest-brief">
            <CardContent className="pt-5">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted">
                {written && <Sparkles className="size-3.5" aria-hidden />}
                Today's brief
              </div>
              <p className="text-base leading-7">{written ? written.map((x) => x.text).join(" ") : m.brief.facts.join(" ")}</p>
              {written && t && (
                <p className="mt-2 text-xs text-muted">
                  <WrittenMark hash={t.brief.facts_hash} />.
                </p>
              )}
            </CardContent>
          </Card>
          <Card data-testid="digest-first">
            <CardHeader>
              <CardTitle>Open first</CardTitle>
              <a href={href("decisions")} className="text-xs text-accent hover:underline">
                All {m.decisions.count} decisions
              </a>
            </CardHeader>
            <CardContent className="pb-3">
              <ol className="-mx-2">
                {m.decisions.items.slice(0, SHOWN).map((d) => {
                  const w = decisionText(t, d);
                  return (
                    <li key={d.id}>
                      <a href={href("decisions", { id: d.id })} className="flex items-center gap-3 rounded-md px-2 py-2.5 hover:bg-hover/60" data-testid="digest-decision">
                        <Avatar {...avatarOf(d.asked_by, color)} size={28} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">{w?.title ?? d.title}</span>
                          <span className="block truncate text-sm text-muted">
                            {askerName(d.asked_by)} · waiting {duration(d.age_s)}
                            {w?.suggested ? ` · suggested: ${w.suggested}` : ""}
                          </span>
                        </span>
                        <ArrowRight className="size-4 shrink-0 text-muted" aria-hidden />
                      </a>
                    </li>
                  );
                })}
              </ol>
            </CardContent>
          </Card>
          <div className="grid gap-5 sm:grid-cols-2">
            <Card data-testid="digest-since">
              <CardHeader>
                <CardTitle>Since your last visit</CardTitle>
              </CardHeader>
              <CardContent>
                {s ? (
                  <dl className="grid grid-cols-2 gap-3 text-sm">
                    {(
                      [
                        ["New asks", s.added.length],
                        ["Answered", s.resolved.length],
                        ["Closed without you", s.resolved_without_you.length],
                        ["Came back", s.returned.length],
                      ] as const
                    ).map(([k, v]) => (
                      <div key={k}>
                        <dt className="text-xs text-muted">{k}</dt>
                        <dd className="text-lg font-semibold num">{v}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <Skeleton className="h-20" />
                )}
                {s?.since && <p className="mt-3 text-xs text-muted">Your last visit: {new Date(s.since).toLocaleString(undefined, { hour12: false, weekday: "short", hour: "2-digit", minute: "2-digit" })}</p>}
              </CardContent>
            </Card>
            <Card data-testid="digest-out">
              <CardHeader>
                <CardTitle>Came out in the last day</CardTitle>
              </CardHeader>
              <CardContent>
                {yesterday.length ? (
                  <ul className="space-y-1.5 text-sm">
                    {yesterday.slice(0, 5).map((i) => (
                      <li key={i.ref} className="truncate">
                        {i.title}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted">No change merged in the last 24 hours.</p>
                )}
              </CardContent>
            </Card>
          </div>
          {m.unusual.length > 0 && (
            <Card data-testid="digest-unusual">
              <CardHeader>
                <CardTitle>Unusual this week</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="space-y-1.5 text-sm">
                  {m.unusual.slice(0, 3).map((u) => (
                    <li key={u.id}>{u.title}</li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
          <p className="text-xs text-muted">
            This is the page a digest would bring you. Where it goes (a Windows notification, Slack, email) is the project lead's choice; until then it stays
            here, and on Home.
          </p>
        </div>
      )}
    </div>
  );
}
