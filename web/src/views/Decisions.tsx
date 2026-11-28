import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ExternalLink, Inbox, Merge, Search, Sparkles, Undo2 } from "lucide-react";
import { homeApi, type DecisionsModel, type HomeDecision } from "@/lib/api";
import { useAnswers } from "@/lib/answers";
import { duration } from "@/lib/format";
import { href } from "@/lib/route";
import { isUrl } from "@/lib/url";
import { askerName, avatarOf } from "@/lib/roleColor";
import { useRoleColor } from "@/lib/useRoleColor";
import { decisionText, useTexts, type MergeText, type TextsModel } from "@/lib/texts";
import { OptionButtons, Suggested, WrittenMark } from "@/components/Written";
import { cn } from "@/lib/utils";
import { Problem } from "@/components/kit";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Avatar, Card, Input, Skeleton } from "@/components/ui/basics";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Tooltip } from "@/components/ui/tooltip";

// Decisions (0.7.0 plan, sections 1 and 4): what must I decide, in what order? Prototype F's split view: the list on
// the left, the decision on the right (a sheet on a phone). One subject, one decision: every ask about it, from any
// role, is one row with its count. The answers are the ask's recorded options, else Done and Decline. The smart layer
// (src/onloop/brief.py) may write a short title and subject, suggest one of the recorded options with its grounds (the
// one primary button), and propose merging two near-duplicate decisions, which only a person confirms. Answers stay
// withdrawable under Done until they reach the asker.

type Sort = "order" | "waiting" | "asked";

function useWide(): boolean {
  const q = "(min-width: 1024px)";
  const [wide, setWide] = useState(() => window.matchMedia?.(q).matches ?? true);
  useEffect(() => {
    const mq = window.matchMedia?.(q);
    if (!mq) return;
    const on = (e: MediaQueryListEvent) => setWide(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

function at(ts: string | null): string {
  return ts ? new Date(ts).toLocaleString(undefined, { hour12: false, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[150px_1fr] sm:gap-3">
      <dt className="text-xs text-muted sm:pt-0.5">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

export function DecisionPanel({ d, first, onDone, t }: { d: HomeDecision; first: boolean; onDone?: () => void; t?: TextsModel }) {
  const color = useRoleColor();
  const { answer, withdraw, busy, error } = useAnswers();
  const who = askerName(d.asked_by);
  const open = d.resolution == null;
  const w = open ? decisionText(t, d) : null;
  const title = w?.title ?? d.title;
  const act = async (resolution: "done" | "declined", option?: string) => {
    await answer(d.id, d.asked_by, resolution, option);
    onDone?.();
  };
  return (
    <div className="flex h-full min-h-0 flex-col" data-testid="decision-panel" data-id={d.id}>
      <div className="border-b border-line px-5 py-4 pr-12">
        <div className="flex items-center gap-2 text-xs text-muted">
          <Avatar {...avatarOf(d.asked_by, color)} size={18} />
          {w?.subject && (
            <>
              <span data-testid="panel-subject">{w.subject}</span> ·{" "}
            </>
          )}
          {d.askers.length > 1 ? d.askers.join(", ") : who} · {open ? `waiting ${duration(d.age_s)}` : `answered ${at(d.resolved_at)}`}
        </div>
        <h2 className="mt-1.5 text-lg font-semibold leading-6" data-testid="panel-title">
          {title}
        </h2>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {open && first && <Badge tone="solid">Do first</Badge>}
          {open && d.days_left === 0 && <Badge tone="wait">costing now</Badge>}
          {d.asks > 1 && (
            <Badge tone="outline" data-testid="asked">
              asked {d.asks}× · one decision
            </Badge>
          )}
        </div>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-auto px-5 py-5">
        {!open && (
          <div className="rounded-lg border border-line bg-raised/50 p-4 text-sm" data-testid="answered">
            You answered <b>{d.answer_text}</b> at {at(d.resolved_at)}.{" "}
            {d.resolution === "dismissed"
              ? "It was set aside: it said nothing of what it changes."
              : d.delivered_at
                ? `It reached ${who} at ${at(d.delivered_at)}.`
                : `${who} sees it at its next prompt.`}
            {d.can_withdraw && (
              <Button size="sm" variant="outline" className="ml-2" disabled={busy === d.id} onClick={() => void withdraw(d.id)} data-testid="withdraw">
                <Undo2 aria-hidden />
                Withdraw
              </Button>
            )}
          </div>
        )}
        {w && t && <Suggested t={w} hash={t.decisions.facts_hash} />}
        <dl className="space-y-2.5" data-testid="facts">
          {/* the ask's own words, unless the title already is them */}
          {(d.effect_text ?? d.title) !== title && <Fact label="What it changes">{d.effect_text ?? d.title}</Fact>}
          <Fact label="What waits on it">{d.holds ?? "not said"}</Fact>
          <Fact label="If nobody answers">{d.default ?? "not said"}</Fact>
          {d.cost_text && <Fact label="Cost of delay">{d.cost_text}</Fact>}
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
        {d.asks > 1 && (
          <section>
            <h3 className="mb-2 text-xs font-medium text-muted">Asks, merged into this decision</h3>
            <ul className="space-y-1 text-sm text-muted" data-testid="ask-log">
              {d.ask_log.map((a, i) => (
                <li key={i} className="num">
                  {a.by ?? "someone"} asked · {at(a.at)}
                  {i > 0 && !a.new_fact && " · nothing new"}
                </li>
              ))}
            </ul>
          </section>
        )}
        {error?.id === d.id && <p className="text-sm text-danger" role="alert">{error.text}</p>}
      </div>
      {open && (
        <div className="border-t border-line px-5 py-4" data-testid="answers">
          {d.origin === "gate" ? (
            <p className="text-sm text-muted">Approve it on the code host; onloop sees the approval.</p>
          ) : d.origin === "pause" ? (
            <code className="rounded bg-raised px-1.5 py-0.5 text-xs">onloop role resume {d.ref?.replace(/^role:/, "")}</code>
          ) : d.options.length ? (
            <>
              <div className="mb-2 text-xs text-muted">{who} offered these answers</div>
              <OptionButtons d={d} suggested={w?.suggested ?? null} busy={busy === d.id} onAnswer={(r, o) => void act(r, o)} />
            </>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <Button disabled={busy === d.id} onClick={() => void act("done")} data-testid="done">
                <Check aria-hidden />
                Done
              </Button>
              <Button variant="ghost" disabled={busy === d.id} onClick={() => void act("declined")} data-testid="decline">
                Decline
              </Button>
              <span className="text-xs text-muted">No answers were offered: Done says you did it.</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Row({ d, rank, on, onPick, t }: { d: HomeDecision; rank: number | null; on: boolean; onPick: () => void; t?: TextsModel }) {
  const color = useRoleColor();
  const who = askerName(d.asked_by);
  const w = d.resolution == null ? decisionText(t, d) : null;
  return (
    <li>
      <button
        type="button"
        onClick={onPick}
        aria-current={on ? "true" : undefined}
        data-testid="decision-row"
        data-id={d.id}
        className={cn("flex w-full items-start gap-3 border-b border-line px-4 py-3 text-left transition-colors hover:bg-hover/60", on && "bg-hover")}
      >
        <Avatar {...avatarOf(d.asked_by, color)} size={28} className="mt-0.5" />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="min-w-0 text-sm font-medium" data-testid="row-title">{w?.title ?? d.title}</span>
            {rank === 1 && <Badge tone="solid">Do first</Badge>}
          </span>
          <span className="mt-0.5 block truncate text-sm text-muted">{d.holds ? `holds ${d.holds}` : d.cost_text ?? "nothing said to wait on it"}</span>
          <span className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted">
            {w?.subject && <span>{w.subject}</span>}
            <span>{d.askers.length > 1 ? d.askers.join(", ") : who}</span>
            <span className="num">{d.resolution ? `answered ${at(d.resolved_at)}` : `waiting ${duration(d.age_s)}`}</span>
            {d.asks > 1 && <span className="num">asked {d.asks}×</span>}
          </span>
        </span>
        <span className="mt-0.5 shrink-0">
          {d.resolution ? (
            <Badge tone={d.resolution === "dismissed" ? "default" : "done"}>{d.answer_text}</Badge>
          ) : w?.suggested ? (
            <Badge tone="outline" className="max-sm:hidden" title="Suggested by onloop, one of the answers offered">
              <Sparkles className="mr-1 size-3" aria-hidden />
              {w.suggested}
            </Badge>
          ) : d.options.length ? (
            <Badge tone="outline">{d.options.length} answers</Badge>
          ) : null}
        </span>
      </button>
    </li>
  );
}

function List({ m, show, setShow, pick, current, wide, t }: { m: DecisionsModel; show: "open" | "done"; setShow: (v: "open" | "done") => void; pick: (id: string) => void; current: string | null; wide: boolean; t?: TextsModel }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<Sort>("order");
  const base = show === "open" ? m.open : m.done;
  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const hit = base.filter((d) => !ql || `${d.title} ${d.asked_by ?? ""} ${d.holds ?? ""} ${d.ref ?? ""}`.toLowerCase().includes(ql));
    if (sort === "waiting") return [...hit].sort((a, b) => b.age_s - a.age_s);
    if (sort === "asked") return [...hit].sort((a, b) => b.asks - a.asks);
    return hit;
  }, [base, q, sort]);
  return (
    <Card className="overflow-hidden" data-testid="decision-list">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <div className="inline-flex rounded-md bg-raised p-0.5" role="tablist" aria-label="Show">
          {(["open", "done"] as const).map((v) => (
            <button key={v} type="button" role="tab" aria-selected={show === v} onClick={() => setShow(v)} data-testid={`tab-${v}`} className={cn("rounded px-2.5 py-0.5 text-xs font-medium capitalize text-muted", show === v && "bg-bg text-fg shadow-sm")}>
              {v} <span className="num">{v === "open" ? m.count : m.done.length}</span>
            </button>
          ))}
        </div>
        <Tooltip content="In order: weight, then when the delay costs, then age.">
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="h-7 rounded-md border border-line bg-bg px-1.5 text-xs text-fg">
            <option value="order">in order</option>
            <option value="waiting">longest waiting</option>
            <option value="asked">most asked</option>
          </select>
        </label>
        </Tooltip>
        <div className="relative w-full sm:w-[200px]">
          <Search className="absolute left-2.5 top-2 size-4 text-muted" aria-hidden />
          <Input placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" aria-label="Filter decisions" />
        </div>
      </div>
      {rows.length ? (
        <ul>
          {rows.map((d) => (
            <Row key={d.id} d={d} t={t} rank={show === "open" && sort === "order" ? m.open.indexOf(d) + 1 : null} on={wide && d.id === current} onPick={() => pick(d.id)} />
          ))}
        </ul>
      ) : (
        <div className="flex flex-col items-center px-6 py-10 text-center" data-testid="list-empty">
          <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-raised text-muted">{q ? <Search className="size-5" aria-hidden /> : <Check className="size-5" aria-hidden />}</div>
          <div className="text-sm font-medium">{q ? "No match" : show === "open" ? "You are up to date" : "No answers in the last 14 days"}</div>
          <p className="mt-1 max-w-[320px] text-sm text-muted">{q ? `No decision matches "${q}".` : show === "open" ? "Nothing waits for you. New decisions appear here in their one order." : "Your answers appear here, each withdrawable until it reaches the asker."}</p>
        </div>
      )}
    </Card>
  );
}

const asksTotal = (m: DecisionsModel) => m.open.reduce((n, d) => n + d.asks, 0);

const KEPT = "onloop.merges.kept"; // this viewer's "Keep apart": a convenience in this browser, never the ledger's

function keptApart(): string[] {
  try {
    return JSON.parse(localStorage.getItem(KEPT) ?? "[]") as string[];
  } catch {
    return [];
  }
}

/** The smart layer's near-duplicate proposals between open decisions (0.7.0 plan, section 4.3). Nothing merges by
 * itself: Merge is the person's, and it closes the second into the first with every ask counted. */
function MergeProposals({ m, t }: { m: DecisionsModel; t?: TextsModel }) {
  const { merge, busy, error } = useAnswers();
  const [kept, setKept] = useState<string[]>(keptApart);
  const byId = new Map(m.open.map((d) => [d.id, d]));
  const pairs = (t?.merges.text?.merges ?? []).filter(
    (p: MergeText) => byId.has(p.keep) && byId.has(p.merge) && !kept.includes(`${p.keep}|${p.merge}`),
  );
  if (!t || !pairs.length) return null;
  const title = (id: string) => decisionText(t, byId.get(id)!)?.title ?? byId.get(id)!.title;
  const keepApart = (p: MergeText) => {
    const next = [...kept, `${p.keep}|${p.merge}`];
    setKept(next);
    try {
      localStorage.setItem(KEPT, JSON.stringify(next));
    } catch {
      /* this browser keeps nothing: it shows again on reload */
    }
  };
  return (
    <div className="mb-5 space-y-3">
      {pairs.map((p) => (
        <Card key={`${p.keep}|${p.merge}`} className="p-4" data-testid="merge-proposal">
          <div className="flex items-center gap-1.5 text-xs font-medium text-muted">
            <Sparkles className="size-3.5" aria-hidden />
            These two look like one decision
          </div>
          <ul className="mt-2 space-y-1 text-sm">
            <li>
              <b>{title(p.keep)}</b> <span className="text-muted">· {askerName(byId.get(p.keep)!.asked_by)}</span>
            </li>
            <li>
              <b>{title(p.merge)}</b> <span className="text-muted">· {askerName(byId.get(p.merge)!.asked_by)}</span>
            </li>
          </ul>
          <p className="mt-2 text-sm text-muted">{p.why}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" disabled={busy === "merge"} onClick={() => void merge(p.keep, p.merge)} data-testid="merge">
              <Merge aria-hidden />
              Merge into the first
            </Button>
            <Button size="sm" variant="ghost" onClick={() => keepApart(p)} data-testid="keep-apart">
              Keep apart
            </Button>
            <span className="text-xs text-muted">
              <WrittenMark hash={t.merges.facts_hash} what="the open decisions" />. Nothing merges unless you do.
            </span>
          </div>
          {error?.id === "merge" && <p className="mt-2 text-sm text-danger" role="alert">{error.text}</p>}
        </Card>
      ))}
    </div>
  );
}

export function DecisionsView() {
  const q = useQuery({ queryKey: ["decisions"], queryFn: homeApi.decisions, refetchInterval: 60_000 });
  const t = useTexts();
  const wide = useWide();
  const [show, setShow] = useState<"open" | "done">("open");
  const [sel, setSel] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string | null>(null);
  const m = q.data;
  const rows = m ? (show === "open" ? m.open : m.done) : [];
  const current = rows.find((r) => r.id === sel) ? sel : rows[0]?.id ?? null;
  const pick = (id: string) => (wide ? setSel(id) : setSheet(id));
  const all = m ? [...m.open, ...m.done] : [];
  const inSheet = all.find((d) => d.id === sheet) ?? null;
  const shown = all.find((d) => d.id === current) ?? null;
  return (
    <div className="mx-auto w-full max-w-[1320px] px-4 py-6 md:px-8" data-testid="view-decisions">
      <div className="mb-5">
        <h1 className="text-xl font-semibold tracking-tight">Decisions</h1>
        {m && (
          <p className="mt-0.5 text-sm text-muted" data-testid="decisions-sub">
            {show === "done"
              ? "Your answers of the last 14 days. Each can be withdrawn until it reaches the role that asked."
              : `${asksTotal(m)} ${asksTotal(m) === 1 ? "ask" : "asks"} became ${m.count} ${m.count === 1 ? "decision" : "decisions"}: one subject, one decision.`}
            {show === "open" && m.rule_asks > 0 && (
              <>
                {" "}
                <a href={href("rules")} className="text-accent hover:underline" data-testid="rule-asks">
                  {m.rule_asks} more {m.rule_asks === 1 ? "ask is" : "asks are"} missing what {m.rule_asks === 1 ? "it changes" : "they change"} or what waits on {m.rule_asks === 1 ? "it" : "them"}
                </a>
                .
              </>
            )}
          </p>
        )}
      </div>
      {m && show === "open" && <MergeProposals m={m} t={t} />}
      {!m && q.error ? (
        <Problem error={q.error} />
      ) : !m ? (
        <div className="grid gap-5 lg:grid-cols-2">
          <Skeleton className="h-96" />
          <Skeleton className="hidden h-96 lg:block" />
        </div>
      ) : wide ? (
        <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] gap-5">
          <List m={m} t={t} show={show} setShow={setShow} pick={pick} current={current} wide />
          <Card className="sticky top-4 h-[calc(100vh-170px)] min-h-[520px] overflow-hidden">
            {shown ? (
              <DecisionPanel key={shown.id} d={shown} t={t} first={m.open[0]?.id === shown.id} />
            ) : (
              <div className="flex h-full flex-col items-center justify-center p-6 text-center text-sm text-muted">
                <Inbox className="mb-2 size-5" aria-hidden />
                Nothing to show here.
              </div>
            )}
          </Card>
        </div>
      ) : (
        <>
          <List m={m} t={t} show={show} setShow={setShow} pick={pick} current={null} wide={false} />
          <Dialog open={!!inSheet} onOpenChange={(o) => !o && setSheet(null)}>
            <DialogContent side="right" className="p-0" aria-describedby={undefined} data-testid="decision-sheet">
              <DialogTitle className="sr-only">{inSheet?.title ?? "Decision"}</DialogTitle>
              {inSheet && <DecisionPanel d={inSheet} t={t} first={m.open[0]?.id === inSheet.id} onDone={() => setSheet(null)} />}
            </DialogContent>
          </Dialog>
        </>
      )}
    </div>
  );
}
