import { useEffect, useState, type FormEvent } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import { api, type LedgerEvent } from "@/lib/api";
import { href, setParams } from "@/lib/route";
import { cn } from "@/lib/utils";
import { Empty, Loading, Problem, Ticks } from "@/components/kit";

// What happened, who did it, and what it rests on (blueprint section 4.6). Every row's time is the ledger's own
// (`ts_system`); a time the sender claimed is shown apart and never used to order or filter. A run of the same routine
// event (a heartbeat ping each minute) is one row with its count, opened on a click.

const FILTERS = ["kind", "subject", "source", "actor", "since", "until"] as const;
const SOURCES = ["github", "hook", "healthchecks", "ui", "cli", "carrier", "detector", "gate", "legacy", "config"];
const SOURCE_LABEL: Record<string, string> = {
  github: "GitHub",
  hook: "role hook",
  healthchecks: "job result",
  ui: "person, page",
  cli: "person, CLI",
  carrier: "carrier",
  gate: "merge gate",
  detector: "detector",
  legacy: "import",
  config: "config",
};

function evidence(e: LedgerEvent): string[] {
  const p = e.payload ?? {};
  const out: string[] = [];
  const pick = (k: string, label = k) => {
    const v = p[k];
    if (typeof v === "string" && v) out.push(`${label} ${v}`);
  };
  pick("status");
  pick("reason");
  pick("ref");
  pick("url");
  pick("line");
  pick("parse_error", "parse error:");
  pick("title");
  pick("state");
  const outcome = p.outcome as { value?: string } | undefined;
  if (outcome?.value) out.push(`outcome ${outcome.value}`);
  if (typeof p.action === "string") out.push(`would ${p.action}${typeof p.target === "string" ? ` ${p.target}` : ""} (observe only)`);
  return out.slice(0, 4);
}

function claimed(e: LedgerEvent): string | null {
  if (e.at && e.at !== e.ts_system) return e.at;
  const c = (e.payload?.claimed_at ?? null) as Record<string, string> | null;
  return c ? Object.values(c)[0] ?? null : null;
}

function stamp(ts: string): string {
  const d = new Date(ts);
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${d.toLocaleTimeString(undefined, { hour12: false })}`;
}

type Group = { key: string; rows: LedgerEvent[] };

// Consecutive events that say the same thing about the same subject (kind, subject, source and evidence) are one group.
function grouped(rows: LedgerEvent[]): Group[] {
  const out: Group[] = [];
  for (const e of rows) {
    const key = `${e.kind}|${e.subject_ref}|${e.source}|${evidence(e).join("|")}`;
    const last = out[out.length - 1];
    if (last && last.key === key && !claimed(e)) last.rows.push(e);
    else out.push({ key, rows: [e] });
  }
  return out;
}

function Filters({ params }: { params: URLSearchParams }) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const active = FILTERS.filter((k) => params.get(k)).length;
  const [open, setOpen] = useState(active > 0);
  useEffect(() => setDraft(Object.fromEntries(FILTERS.map((k) => [k, params.get(k) ?? ""]))), [params]);
  const submit = (ev: FormEvent) => {
    ev.preventDefault();
    setParams("audit", Object.fromEntries(FILTERS.map((k) => [k, draft[k]?.trim() || undefined])));
  };
  const field = "h-9 w-full min-w-0 rounded-md border border-line bg-surface px-2.5 text-[14px] text-fg placeholder:text-muted/70";
  // Called as a function, not mounted as a component, so a field keeps its focus while the person types.
  const F = ({ k, label, hint }: { k: (typeof FILTERS)[number]; label: string; hint: string }) => (
    <label key={k} className="flex min-w-0 flex-col gap-1 text-[12px] font-medium text-muted">
      {label}
      <input aria-label={label} placeholder={hint} className={field} value={draft[k] ?? ""} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} />
    </label>
  );
  return (
    <div className="mt-4">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} className="inline-flex h-9 items-center gap-1.5 rounded-md border border-line bg-surface px-3 text-[14px] sm:hidden">
        <SlidersHorizontal className="h-4 w-4" aria-hidden /> Filters{active ? ` (${active})` : ""}
      </button>
      <form onSubmit={submit} data-testid="audit-filters" className={cn("grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-[1fr_1fr_9rem_1fr_1fr_1fr_auto] lg:items-end", !open && "max-sm:hidden", "max-sm:mt-2")}>
        {F({ k: "kind", label: "Kind", hint: "pr.merged" })}
        {F({ k: "subject", label: "Subject", hint: "pr#12" })}
        <label className="flex min-w-0 flex-col gap-1 text-[12px] font-medium text-muted">
          Source
          <select aria-label="Source" className={field} value={draft.source ?? ""} onChange={(e) => setDraft({ ...draft, source: e.target.value })}>
            <option value="">any</option>
            {SOURCES.map((s) => <option key={s} value={s}>{SOURCE_LABEL[s]} ({s})</option>)}
          </select>
        </label>
        {F({ k: "actor", label: "Who", hint: "builder" })}
        {F({ k: "since", label: "Since", hint: "2026-11-09T09:00Z" })}
        {F({ k: "until", label: "Until", hint: "2026-11-09T18:00Z" })}
        <div className="col-span-2 flex gap-2 sm:col-span-3 lg:col-span-1">
          <button type="submit" className="h-9 flex-1 rounded-md bg-fg px-3 text-[14px] font-medium text-bg hover:bg-fg/90 lg:flex-none">Filter</button>
          <a href={href("audit")} className="grid h-9 place-items-center rounded-md border border-line px-3 text-[14px] text-muted hover:bg-raised">Clear</a>
        </div>
      </form>
    </div>
  );
}

const COLS = "md:grid-cols-[9.5rem_minmax(9rem,13rem)_minmax(0,1.1fr)_minmax(0,1fr)]";

function Row({ g, filters }: { g: Group; filters: Record<string, string> }) {
  const [open, setOpen] = useState(false);
  const e = g.rows[0];
  const c = claimed(e);
  const ev = evidence(e);
  const more = g.rows.length - 1;
  return (
    <li data-testid="event" data-count={g.rows.length} className={cn("grid gap-x-4 gap-y-0.5 border-b border-line px-4 py-2.5 last:border-b-0", COLS)}>
      <span className="font-mono text-[13px] tabular-nums text-muted" title={e.ts_system}>
        {stamp(e.ts_system)}
        {more > 0 && <span className="block text-[11.5px]">to {stamp(g.rows[g.rows.length - 1].ts_system)}</span>}
        {c && <span className="block text-[11.5px] text-muted/80" title="The time the sender claimed. Not used to order or filter.">claimed {stamp(c)}</span>}
      </span>
      <span className="min-w-0 text-[13px]">
        <span className="block truncate font-medium text-fg" title={e.actor ?? ""}>{e.actor ?? "—"}</span>
        <span className="block text-[12px] text-muted">{SOURCE_LABEL[e.source] ?? e.source}</span>
      </span>
      <span className="min-w-0 text-[14px] leading-snug">
        <span className="font-mono text-[13px] font-semibold">{e.kind}</span>{" "}
        <a className="font-mono text-[13px] text-accent hover:underline" href={href("audit", { ...filters, subject: e.subject_ref })}>{e.subject_ref}</a>
        {more > 0 && (
          <button type="button" onClick={() => setOpen((o) => !o)} className="ml-2 rounded-full border border-line px-2 py-px text-[12px] text-muted hover:bg-raised" aria-expanded={open}>
            ×{g.rows.length}
          </button>
        )}
        {open && (
          <span className="mt-1 block font-mono text-[12px] text-muted">
            {g.rows.map((r) => stamp(r.ts_system).split(" ")[2]).join(" · ")}
          </span>
        )}
      </span>
      <span className="min-w-0 break-words text-[13px] text-muted" data-testid="evidence">
        {ev.length ? <Ticks text={ev.join(" · ")} /> : <span className="text-muted/70">—</span>}
      </span>
    </li>
  );
}

export function AuditView({ params }: { params: URLSearchParams }) {
  const filters = Object.fromEntries(FILTERS.map((k) => [k, params.get(k) ?? ""]).filter(([, v]) => v));
  const q = useInfiniteQuery({
    queryKey: ["events", filters],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const p = new URLSearchParams({ ...filters, latest: "1", limit: "100" });
      if (pageParam) p.set("before", String(pageParam));
      return api.events(p);
    },
    getNextPageParam: (last) => last.next_before ?? null,
  });
  const rows = q.data?.pages.flatMap((p) => p.events) ?? [];
  const groups = grouped(rows);
  return (
    <div className="mx-auto max-w-[1400px] px-4 py-4 sm:px-6 sm:py-5" data-testid="view-audit" data-events={rows.length}>
      <h1 className="text-lg font-semibold">Audit</h1>
      <p className="mt-0.5 text-[13px] text-muted">Every event in the ledger, newest first. The time is the ledger's own; agents cannot set it.</p>
      <Filters params={params} />
      <div className="mt-4">
        {q.isPending ? (
          <Loading what="the events" />
        ) : q.isError ? (
          <Problem error={q.error} />
        ) : rows.length === 0 ? (
          <Empty testid="audit-empty">No event matches these filters.</Empty>
        ) : (
          <div className="overflow-hidden rounded-zone border border-line bg-surface">
            <div className={cn("grid gap-4 border-b border-line bg-raised px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted max-md:hidden", COLS)}>
              <span>Time</span>
              <span>Who</span>
              <span>What</span>
              <span>Detail</span>
            </div>
            <ol aria-label="Events">
              {groups.map((g) => <Row key={`${g.rows[0].seq}`} g={g} filters={filters} />)}
            </ol>
          </div>
        )}
        {q.hasNextPage && (
          <button
            type="button"
            onClick={() => q.fetchNextPage()}
            disabled={q.isFetchingNextPage}
            data-testid="older"
            className="mt-3 h-9 w-full rounded-md border border-line bg-surface text-[14px] text-muted hover:bg-raised"
          >
            {q.isFetchingNextPage ? "Reading…" : "Older events"}
          </button>
        )}
      </div>
    </div>
  );
}
