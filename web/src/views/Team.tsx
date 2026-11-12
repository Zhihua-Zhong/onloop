import { useQueries } from "@tanstack/react-query";
import { api, type Item, type Role, type Turn, type Wait } from "@/lib/api";
import { clock, duration, plural, reason } from "@/lib/format";
import { Command, Empty, Loading, Pill, Problem, Ticks, type Tone } from "@/components/kit";
import { cn } from "@/lib/utils";

// One lane per role: its state, what it is on, and what happens next (blueprint section 4.3).

interface Lane {
  role: string;
  state: string;
  t: Tone;
  what: string;
  whatDetail?: string;
  raw?: string; // the outcome line as the role wrote it, kept for the tooltip
  next: string;
  command?: string;
}

// The outcome line a role wrote (decision 0007), said as a sentence.
function said(t: Turn): string {
  const ref = t.ref ? ` ${t.ref}` : "";
  switch (t.outcome) {
    case "done":
      return `Finished${ref}`;
    case "idle":
      return "Ended its turn with nothing to do";
    case "in_review":
      return `Put${ref || " its work"} up for review${t.owner ? ` by ${t.owner}` : ""}`;
    case "blocked":
      return `Blocked on${ref || " something"}${t.owner ? `; ${t.owner} holds the next step` : ""}`;
    case "handoff":
      return `Handed${ref || " its work"} to ${t.owner ?? "nobody"}`;
    case "continue":
      return `Continues${ref}${t.waker ? `; ${t.waker} wakes it` : ""}`;
    default:
      return t.line ?? "";
  }
}

function lane(r: Role, turns: Turn[], waits: Wait[], items: Item[], alerts: Item[]): Lane {
  const last = turns.find((t) => t.role === r.id);
  const owned = items.filter((i) => i.owner_role === r.id && i.state !== "done" && i.kind !== "alert");
  const onRole = waits.find((w) => w.waiter === r.id && w.on_kind === "role");
  const onEvent = waits.find((w) => w.waiter === r.id && w.on_kind === "event");
  const stall = alerts.find((a) => a.owner_role === r.id && (a.detail?.detector as string) === "no_outcome");
  const lastText = last
    ? last.outcome
      ? { what: said(last), detail: `turn ended ${clock(last.ended_at)}`, raw: last.line ?? undefined }
      : { what: "turn ended without an outcome line", detail: `${clock(last.ended_at)}: ${last.parse_error ?? "no outcome"}`, raw: undefined }
    : { what: "no turn yet", detail: undefined, raw: undefined };
  if (r.state === "paused_by_human") {
    return {
      role: r.id,
      state: "paused by you",
      t: "human",
      what: `${plural(owned.length, "item")} queued behind it`,
      whatDetail: lastText.detail,
      next: "waits for you to resume it",
      command: `onloop role resume ${r.id}`,
    };
  }
  if (stall) {
    return {
      role: r.id,
      state: "stalled",
      t: "stall",
      what: lastText.what,
      whatDetail: lastText.detail,
      next: `would wake (observe only); not acknowledged for ${duration((Date.now() - Date.parse(stall.opened_at ?? "")) / 1000)}`,
    };
  }
  if (r.state === "working") {
    const budget = Number((r.budget as { minutes?: number }).minutes);
    return {
      role: r.id,
      state: "working",
      t: "open",
      what: owned[0]?.title ?? "on its current turn",
      whatDetail: Number.isFinite(budget) ? `a ${budget} min budget` : undefined,
      next: "—",
    };
  }
  if (onRole || onEvent || r.state === "blocked") {
    const w = onRole ?? onEvent;
    return {
      role: r.id,
      state: w?.on_kind === "role" ? `waiting on ${w.on_ref}` : "blocked",
      t: "external",
      what: lastText.what,
      whatDetail: lastText.detail,
      raw: lastText.raw,
      next: w ? (w.on_kind === "event" ? `waits for the event ${w.on_ref}` : `waits for ${w.on_ref}: ${reason(w.reason)}`) : "—",
    };
  }
  return {
    role: r.id,
    state: owned.length ? "idle" : "idle, queue empty",
    t: "void",
    what: owned.length ? `${plural(owned.length, "item")} in its queue` : "nothing to do",
    whatDetail: lastText.detail,
    next: owned.length ? `next: ${owned[0].title ?? owned[0].id}` : "idle is normal",
  };
}

const ORDER: Tone[] = ["human", "stall", "blocked", "external", "open", "void", "neutral"];

export function TeamView() {
  const qs = useQueries({
    queries: [
      { queryKey: ["roles"], queryFn: api.roles },
      { queryKey: ["turns"], queryFn: api.turns },
      { queryKey: ["waits"], queryFn: api.waits },
      { queryKey: ["items"], queryFn: api.items },
      { queryKey: ["alerts"], queryFn: api.alerts },
    ],
  });
  const [roles, turns, waits, items, alerts] = qs;
  const failed = qs.find((q) => q.isError);
  const ready = qs.every((q) => q.data);
  const lanes = ready
    ? roles.data!.map((r) => lane(r, turns.data!, waits.data!, items.data!, alerts.data!)).sort((a, b) => ORDER.indexOf(a.t) - ORDER.indexOf(b.t) || a.role.localeCompare(b.role))
    : [];
  return (
    <div className="mx-auto max-w-[1400px] px-4 py-4 sm:px-6 sm:py-5" data-testid="view-team">
      <h1 className="text-lg font-semibold">Team</h1>
      <p className="mt-0.5 text-[13px] text-muted">Each role, what it is on, and what happens next. Who needs you comes first.</p>
      <div className="mt-4">
        {failed ? (
          <Problem error={failed.error} />
        ) : !ready ? (
          <Loading what="the roles" />
        ) : lanes.length === 0 ? (
          <Empty>No roles have reported yet. A role appears with its first hook or declaration.</Empty>
        ) : (
          <div role="table" aria-label="Roles" className="overflow-hidden rounded-zone border border-line bg-surface">
            <div role="row" className="grid grid-cols-[10rem_11rem_1fr_1fr] gap-4 border-b border-line bg-raised px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted max-md:hidden">
              <span role="columnheader">Role</span>
              <span role="columnheader">State</span>
              <span role="columnheader">What</span>
              <span role="columnheader">Next</span>
            </div>
            {lanes.map((l) => (
              <div
                role="row"
                key={l.role}
                data-testid={`lane-${l.role}`}
                className={cn("grid gap-x-4 gap-y-1 border-b border-line px-4 py-3 last:border-b-0 md:grid-cols-[10rem_11rem_1fr_1fr] md:items-start", l.t === "human" && "bg-state-human/[0.06]")}
              >
                <span role="cell" className="font-mono text-[15px] font-semibold">{l.role}</span>
                <span role="cell" className="md:pt-[2px]"><Pill t={l.t} testid="role-pill">{l.state}</Pill></span>
                <span role="cell" className="min-w-0 text-[14px] leading-snug">
                  <span className="block break-words" title={l.raw}>{l.what}</span>
                  {l.whatDetail && <span className="block text-[13px] text-muted"><Ticks text={l.whatDetail} /></span>}
                </span>
                <span role="cell" className="min-w-0 text-[14px] leading-snug text-muted">
                  <span className="max-md:before:content-['Next:_'] max-md:before:text-muted">{l.next}</span>
                  {l.command && <Command cmd={l.command} />}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
