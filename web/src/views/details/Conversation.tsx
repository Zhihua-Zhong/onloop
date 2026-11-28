import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Send } from "lucide-react";
import { api, sendMessage, type ConvEntry, type ConvRole, type ConversationModel } from "@/lib/api";
import { clock, duration, plural } from "@/lib/format";
import { Command, Dot, Empty, Loading, Pill, Problem, Ticks, type Tone } from "@/components/kit";
import { setParams } from "@/lib/route";
import { cn } from "@/lib/utils";

// Conversation (blueprint section 4.4; decisions 0010 and 0011): the roles onloop carries, and one role's thread.
// The thread shows what onloop sent and how far each delivery got, each run, each turn by its outcome line, and what
// onloop would have sent where it only observes. A role's own words are never stored; "open in Claude Code" shows
// them in the person's own client.

const SEEN = "onloop-conversation-seen";

function seen(): Record<string, number> {
  try {
    return JSON.parse(window.localStorage.getItem(SEEN) ?? "{}") as Record<string, number>;
  } catch {
    return {};
  }
}

function markSeen(role: string, seq: number) {
  try {
    const s = seen();
    if ((s[role] ?? 0) < seq) window.localStorage.setItem(SEEN, JSON.stringify({ ...s, [role]: seq }));
  } catch {
    /* unread counts are a convenience */
  }
}

const DELIVERY: Record<string, { label: string; t: Tone }> = {
  created: { label: "queued", t: "neutral" },
  claimed: { label: "starting", t: "open" },
  delivered: { label: "delivered, not yet processing", t: "open" },
  processing: { label: "being processed", t: "open" },
  acked: { label: "acknowledged", t: "done" },
  failed: { label: "failed", t: "blocked" },
  escalated: { label: "escalated", t: "human" },
  cancelled: { label: "not sent", t: "void" },
};

const RUN: Record<string, Tone> = {
  ok: "done",
  running: "open",
  refused: "blocked",
  error: "blocked",
  timeout: "blocked",
  silent: "stall",
  killed: "void",
  usage_limited: "human",
  not_logged_in: "human",
  orphaned: "void",
  held_elsewhere: "external",
  contended: "blocked",
};

function Who({ entry }: { entry: ConvEntry }) {
  const name =
    entry.type === "message" && entry.author === "human" ? "You" : entry.type === "turn" || entry.type === "run" ? entry.role : "onloop";
  return <span className="font-semibold text-fg">{name}</span>;
}

function DeliveryLine({ e }: { e: Extract<ConvEntry, { type: "message" }> }) {
  const d = e.delivery;
  if (!d) return null;
  const observed = d.state === "cancelled" && (d.last_error ?? "").startsWith("observe-only");
  const s = observed
    ? { label: "not sent: observe-only (would deliver)", t: "void" as Tone }
    : (DELIVERY[d.state] ?? { label: d.state, t: "neutral" as Tone });
  const steps = [
    d.delivered_at && `delivered ${clock(d.delivered_at, undefined, true)}`,
    d.processing_at && `processing ${clock(d.processing_at, undefined, true)}`,
    d.acked_at && `acknowledged ${clock(d.acked_at, undefined, true)}${d.acked_by_turn ? " by the turn below" : " by the run's end (no Stop hook in time)"}`,
  ].filter(Boolean);
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-muted" data-testid="delivery">
      <Pill t={s.t}>{s.label}</Pill>
      {steps.map((x, i) => (
        <span key={i} className="inline-flex items-center gap-2">
          {i > 0 && <Dot />}
          {x}
        </span>
      ))}
      {d.resent && <span>· resent after an interruption</span>}
      {d.state === "cancelled" && d.last_error && (
        <span>
          · <Ticks text={observed ? d.last_error.replace(/^observe-only: /, "") : d.last_error} />
        </span>
      )}
      {d.escalated_to && <span>· sent on to {d.escalated_to === "human" ? "you" : d.escalated_to}</span>}
    </div>
  );
}

function Entry({ e }: { e: ConvEntry }) {
  const human = e.type === "message" && e.author === "human";
  return (
    <li
      data-testid={`entry-${e.type}`}
      data-seq={e.seq}
      className={cn("flex", human ? "justify-end" : "justify-start")}
    >
      <div
        className={cn(
          "min-w-0 max-w-[min(42rem,92%)] rounded-ui border px-3.5 py-2.5 text-sm",
          human ? "border-accent/30 bg-accent/5" : e.type === "would" ? "border-dashed border-line bg-surface" : "border-line bg-surface",
        )}
      >
        <p className="flex flex-wrap items-baseline gap-x-2 text-[13px] text-muted">
          <Who entry={e} />
          <span title={new Date(e.at).toLocaleString()}>{clock(e.at)}</span>
          {e.type === "message" && e.author === "human" && <span data-testid="unverified">· {e.channel === "ui" ? "this page" : "the CLI"} · local user, not verified</span>}
          {e.type === "message" && e.author === "onloop" && <span>· {kindLabel(e.delivery?.kind)}</span>}
          {e.type === "would" && <span className="font-medium text-state-external">· would send (observe only)</span>}
        </p>
        {e.type === "message" && (
          <>
            <p className="mt-1 whitespace-pre-wrap break-words leading-relaxed text-fg">
              {e.text == null ? (
                <span className="italic text-muted">This message's text expired; its fingerprint is kept.</span>
              ) : e.author === "onloop" ? (
                <Ticks text={e.text} />
              ) : (
                e.text
              )}
            </p>
            <DeliveryLine e={e} />
          </>
        )}
        {e.type === "would" && (
          <>
            <p className="mt-1 whitespace-pre-wrap break-words leading-relaxed text-fg">
              <Ticks text={e.text} />
            </p>
            <p className="mt-1.5 text-[13px] text-muted">
              Not sent: <Ticks text={e.why} />
            </p>
          </>
        )}
        {e.type === "turn" && (
          <p className="mt-1 leading-relaxed">
            {e.outcome ? (
              <>
                Ended its turn: <code className="rounded bg-raised px-1 font-mono text-[12.5px]">{e.line}</code>
              </>
            ) : (
              <>
                <span className="font-medium text-state-stall">Ended its turn without an outcome line</span>
                {e.parse_error && (
                  <span className="mt-0.5 block text-[13px] text-muted">
                    <Ticks text={e.parse_error} />
                  </span>
                )}
              </>
            )}
          </p>
        )}
        {e.type === "run" && (
          <p className="mt-1 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <Pill t={RUN[e.status] ?? "neutral"}>run {e.status.replace(/_/g, " ")}</Pill>
            {e.wall_s != null && <span>{duration(e.wall_s)}</span>}
            {e.num_turns != null && (
              <span>
                <Dot /> {plural(e.num_turns, "model turn")}
              </span>
            )}
            {e.denials > 0 && (
              <span className="text-state-blocked">
                <Dot /> {plural(e.denials, "tool refused", "tools refused")}
              </span>
            )}
          </p>
        )}
        {e.type === "note" && <p className="mt-1 leading-relaxed text-fg">{e.text}</p>}
      </div>
    </li>
  );
}

function kindLabel(kind: string | undefined) {
  return kind === "corrective" ? "asked for the outcome line" : kind === "dead_waker" ? "a dead waker" : kind === "event" ? "an event for this role" : "a note";
}

const ATTENTION: Record<string, Tone> = {
  paused: "human",
  "run refused": "blocked",
  "run contended": "blocked",
  "run error": "blocked",
  "run timeout": "blocked",
  "run silent": "stall",
  "no outcome line": "stall",
  "observe only": "external",
  running: "open",
};

function RoleList({ roles, selected, unread }: { roles: ConvRole[]; selected: string; unread: Record<string, number> }) {
  return (
    <nav aria-label="Roles" className="flex flex-wrap gap-1 md:flex-col md:flex-nowrap" data-testid="roles">
      {roles.map((r) => (
        <button
          key={r.role}
          type="button"
          data-testid={`role-${r.role}`}
          aria-current={r.role === selected ? "true" : undefined}
          onClick={() => setParams("conversation", { role: r.role })}
          className={cn(
            "flex shrink-0 flex-col items-start gap-1 rounded-md px-3 py-2 text-left text-sm md:w-full",
            r.role === selected ? "bg-accent/10 text-accent" : "text-fg hover:bg-raised",
          )}
        >
          <span className="flex w-full items-center gap-2">
            <span className={cn("min-w-0 truncate", r.role === selected && "font-semibold")}>{r.role}</span>
            {unread[r.role] > 0 && (
              <span
                data-testid="unread"
                aria-label={`${unread[r.role]} new`}
                className="ml-auto rounded-full bg-accent px-1.5 text-[11px] font-semibold tabular-nums text-bg"
              >
                {unread[r.role]}
              </span>
            )}
          </span>
          {r.attention && (
            <Pill t={ATTENTION[r.attention] ?? "neutral"} testid={r.attention === "paused" ? "paused" : "attention"}>
              {r.attention}
            </Pill>
          )}
        </button>
      ))}
    </nav>
  );
}

function Compose({ role, model }: { role: ConvRole; model: ConversationModel }) {
  const [text, setText] = useState("");
  const [state, setState] = useState<{ busy: boolean; error?: string; sent?: string }>({ busy: false });
  const qc = useQueryClient();
  const c = model.carrier;
  const open = role.compose.enabled;
  useEffect(() => {
    if (!open) setState({ busy: false }); // nothing about an earlier send is left under a closed box
  }, [open, role.role]);
  // A command the banner above already shows (start the carrier) is not repeated here.
  const command = role.compose.command && !(role.compose.command === "onloop carrier start" && (c.kill_switch || !c.enabled)) ? role.compose.command : null;
  // What will really happen to the message. The box is open only when a message can go somewhere (the server says so
  // in `compose`); otherwise it is closed and says why, with the command or setting that opens it.
  const promise = role.paused
    ? `${role.role} is paused: your message waits until you resume it.`
    : c.paused_reason
      ? `The carrier is paused (${c.paused_reason.replace(/_/g, " ")}): your message waits until it resumes.`
      : `An idle ${role.role} is woken with it; a busy one gets it queued. It counts as received when ${role.role}'s next turn ends.`;
  const send = async () => {
    if (!open || !text.trim()) return;
    setState({ busy: true });
    try {
      const out = await sendMessage(role.role, text);
      setText("");
      setState({ busy: false, sent: out.delivery });
      await qc.invalidateQueries({ queryKey: ["conversation"] });
    } catch (err) {
      setState({ busy: false, error: err instanceof Error ? err.message : "The message was not taken." });
    }
  };
  return (
    <form
      data-testid="compose"
      data-open={open ? "1" : "0"}
      className="border-t border-line bg-surface px-4 py-3"
      onSubmit={(ev) => {
        ev.preventDefault();
        void send();
      }}
    >
      <label htmlFor="compose-text" className="sr-only">
        Message to {role.role}
      </label>
      <div className="flex items-end gap-2">
        <textarea
          id="compose-text"
          data-testid="compose-text"
          rows={2}
          maxLength={8000}
          disabled={!open}
          aria-describedby="compose-note"
          value={text}
          onChange={(ev) => setText(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) void send();
          }}
          placeholder={open ? `A message to ${role.role}` : "Messages are off"}
          className="min-h-[2.75rem] min-w-0 flex-1 resize-y rounded-md border border-line bg-bg px-3 py-2 text-sm text-fg placeholder:text-muted focus:border-accent focus:outline-none disabled:resize-none disabled:cursor-not-allowed disabled:border-dashed disabled:bg-raised disabled:text-muted"
        />
        <button
          type="submit"
          data-testid="compose-send"
          disabled={!open || state.busy || !text.trim()}
          className={cn(
            "inline-flex h-10 shrink-0 items-center gap-1.5 rounded-md px-3.5 text-sm font-semibold",
            open ? "bg-accent text-bg disabled:opacity-50" : "cursor-not-allowed border border-dashed border-line bg-raised text-muted",
          )}
        >
          {open ? <Send className="h-4 w-4" aria-hidden /> : <Lock className="h-4 w-4" aria-hidden />} Send
        </button>
      </div>
      {open ? (
        <p id="compose-note" className="mt-1.5 text-[12.5px] leading-snug text-muted" data-testid="compose-promise">
          {promise} A message is a request from a local user, not verified, and never an approval.
        </p>
      ) : (
        <div id="compose-note" className="mt-1.5 text-[13px] leading-snug text-fg" data-testid="compose-off">
          <p>
            <Ticks text={role.compose.why ?? "Messages are off for this role."} />
          </p>
          {command && <Command cmd={command} className="max-w-md" />}
        </div>
      )}
      {state.error && (
        <p role="alert" className="mt-1 text-[13px] text-state-blocked" data-testid="compose-error">
          {state.error}
        </p>
      )}
      {state.sent && !state.error && <p className="mt-1 text-[13px] text-state-done">Sent to the carrier. It shows in the thread above.</p>}
    </form>
  );
}

function CarrierBanner({ model }: { model: ConversationModel }) {
  const c = model.carrier;
  if (!c.configured)
    return (
      <Banner t="external" testid="carrier-state">
        The carrier is not configured: onloop sends nothing now. The threads below are what the ledger recorded
        before. See docs/carrier.md to turn it on.
      </Banner>
    );
  if (c.kill_switch)
    return (
      <Banner t="blocked" testid="carrier-state">
        The carrier is stopped (the kill switch). Nothing is sent until it is started again.
        <Command cmd="onloop carrier start" />
      </Banner>
    );
  if (!c.enabled)
    return (
      <Banner t="external" testid="carrier-state">
        Observe only: the carrier is not started, so onloop records what it would send and sends nothing.
        <Command cmd="onloop carrier start" />
      </Banner>
    );
  if (c.paused_reason)
    return (
      <Banner t="human" testid="carrier-state">
        The carrier is paused: {c.paused_reason.replace(/_/g, " ")}
        {c.paused_until ? ` until ${clock(c.paused_until)}` : ""}.
      </Banner>
    );
  return null;
}

function Banner({ t, children, testid }: { t: Tone; children: React.ReactNode; testid?: string }) {
  return (
    <div data-testid={testid} className={cn("border-b px-4 py-2 text-[13px] text-fg", t === "blocked" ? "border-state-blocked/40 bg-state-blocked/10" : t === "human" ? "border-state-human/40 bg-state-human/10" : "border-state-external/40 bg-state-external/10")}>
      {children}
    </div>
  );
}

export function ConversationView({ params }: { params: URLSearchParams }) {
  const wanted = params.get("role") ?? undefined;
  const q = useQuery({ queryKey: ["conversation", wanted ?? ""], queryFn: () => api.conversation(wanted) });
  const [unread, setUnread] = useState<Record<string, number>>({});
  const end = useRef<HTMLLIElement>(null);
  const firstNew = useRef<number | null>(null); // the first entry the person has not seen, where the thread opens
  const model = q.data;
  const selected = model?.role ?? null;
  useEffect(() => {
    if (!model) return;
    const s = seen();
    if (model.role) {
      const before = s[model.role] ?? 0;
      firstNew.current = model.thread.find((e) => e.seq > before && !(e.type === "message" && e.author === "human"))?.seq ?? null;
      const top = Math.max(0, ...model.thread.map((e) => e.seq));
      markSeen(model.role, top);
      s[model.role] = Math.max(s[model.role] ?? 0, top);
    }
    setUnread(Object.fromEntries(model.roles.map((r) => [r.role, r.entry_seqs.filter((x) => x > (s[r.role] ?? 0)).length])));
  }, [model]);
  useEffect(() => {
    const target = firstNew.current != null ? document.querySelector(`[data-seq="${firstNew.current}"]`) : null;
    if (target) target.scrollIntoView({ block: "start" });
    else end.current?.scrollIntoView({ block: "end" });
  }, [model?.role, model?.thread.length]);
  const role = useMemo(() => model?.roles.find((r) => r.role === selected), [model, selected]);
  if (q.isError) return <div className="p-4"><Problem error={q.error} /></div>;
  if (!model) return <div className="p-4"><Loading what="the conversations" /></div>;
  if (!model.roles.length)
    return (
      <div className="mx-auto max-w-2xl p-4">
        <Empty testid="no-roles">
          onloop carries no role yet. A role is carried when onloopd.yaml names it under <code className="font-mono">carrier.roles</code>, with its session id.
        </Empty>
      </div>
    );
  return (
    <div className="flex h-full min-h-0 flex-col md:flex-row" data-testid="conversation">
      <aside className="shrink-0 border-b border-line bg-surface p-2 md:w-56 md:border-b-0 md:border-r">
        <RoleList roles={model.roles} selected={selected ?? ""} unread={unread} />
      </aside>
      <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label={`${selected}'s thread`}>
        <CarrierBanner model={model} />
        {role && (
          <header className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2 text-[13px] text-muted">
            <span className="text-base font-semibold text-fg">{role.role}</span>
            <span data-testid="acts-on">
              acts on:{" "}
              {!model.carrier.configured
                ? "nothing (no carrier configured)"
                : role.opted_in.length
                  ? role.opted_in.join(", ").replace(/_/g, " ")
                  : "nothing (observe only)"}
            </span>
            <span className="ml-auto min-w-0 max-w-full" title="Read the full turns, in your own client">
              <Command cmd={role.open_command} className="mt-0" />
            </span>
          </header>
        )}
        <ol className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 pb-8 pt-4" data-testid="thread">
          {model.thread.length === 0 && <Empty>Nothing yet between onloop and {selected}.</Empty>}
          {model.thread.map((e) => (
            <Entry key={`${e.type}-${e.seq}`} e={e} />
          ))}
          <li ref={end} aria-hidden />
        </ol>
        {role && <Compose role={role} model={model} />}
      </section>
    </div>
  );
}
