// The ledger API as onloopd serves it (src/onloop/daemon/server.py, src/onloop/report/views.py). Read-only: every
// request here is a GET (decision 0009) but the page's writes: a message to a role (`sendMessage`, decision 0011) and a
// task's mark, keep or a visit (`uiPost`, decision 0013); the browser
// tests fail on any other method.

export type Seconds = number | null;

export interface Bottleneck {
  on_ref: string;
  on_kind: "role" | "resource" | "human" | "event";
  waiters: string[];
  direct_wait_s: number;
  downstream_wait_s: number;
  reasons: Record<string, number>;
  oldest_since: string;
  in_cycle: boolean;
  resource_state: string | null;
  waits: { id: string; waiter: string; item_id: string | null; reason: string; since: string; wait_s: number }[];
}

export interface WaitCard {
  kind: "wait" | "paused_role";
  id: string;
  item_id: string | null;
  title: string;
  reason: string;
  owner_role: string | null;
  ref: string | null;
  since: string;
  age_s: Seconds;
  default: string | null;
  expires: string | null;
  left_s: Seconds;
  queued?: number;
  command: string | null;
}

export interface Stall {
  id: string;
  title: string;
  detector: string | null;
  owner_role: string | null;
  ref: string | null;
  opened_at: string;
  age_s: Seconds;
  would: { action: string; target: string | null; at: string; label: string; acting?: boolean } | null;
  acknowledged: { at: string; outcome: string | null } | null;
  detail: Record<string, unknown>;
}

export interface ScheduleHealth {
  id: string;
  name: string;
  period: string | null;
  state: "failing" | "late" | "ok";
  last_status: string | null;
  last_success: string | null;
  last_result_at: string | null;
  has_log_tail: boolean;
  owner_role: string | null;
}

export interface YourWait {
  count: number;
  oldest_s: number | null;
  share: number; // of the window (cut at now) in which at least one of your tasks was open
  waited_s: number;
  by_role: Record<string, number>;
}

export interface NowModel {
  now: string;
  constraint: Bottleneck | null;
  related: { bottlenecks: { on_ref: string; on_kind: string; waiters: number }[]; conflicts: number; blocked_items: number } | null;
  next: { text: string; command: string | null; item_id: string | null; ref: string | null } | null;
  waiting_on_you: (WaitCard | TaskCard)[];
  quiet: { since: string; state: "idle" | "waiting"; waits: { waiter: string; on: string; since: string }[]; onloop_down_s?: number } | null;
  stalls: Stall[];
  /** The person's wait over the last day (decision 0019, a7): the one count, the oldest item, the share of the day. */
  your_wait?: YourWait;
  /** The map's cell that holds the constraint: Now's "Map" links open it. */
  constraint_map?: { path: string[]; who: string } | null;
  /** Open items whose expiry is past or within a day (moved from Flow's Roadmap). */
  expiring?: { id: string; title: string; ref: string | null; expires: string; in_s: number }[];
  health: {
    /** onloopd's own gaps in the last week (finding #14): time with no record, never shown as quiet. */
    gaps: { from: string; to: string; seconds: number; cause: string | null; while_running: boolean | null }[];
    schedules: ScheduleHealth[];
    schedules_ok: number;
    schedules_total: number;
    onloopd: { at: string; age_s: Seconds; pid: number | null; stale: boolean; errors: string[] } | null;
    dead_man: { configured: false } | { configured: true; schedule: string; state: string; last_success: string | null };
  };
}

export type ItemState = "open" | "ready" | "blocked" | "in_review" | "done";

export interface Role { id: string; session_id: string | null; state: "idle" | "working" | "paused_by_human" | "blocked"; paused_by: string | null; budget: Record<string, unknown> }
export interface Turn { id: string; role: string; ended_at: string; outcome: string | null; ref: string | null; owner: string | null; waker: string | null; line: string | null; parse_error: string | null }
export interface Wait { id: string; item_id: string | null; waiter: string; on_kind: string; on_ref: string; reason: string; ref: string | null; since: string; cleared_at: string | null }
export interface Item { id: string; kind: string; title: string | null; owner_role: string | null; state: ItemState; ref: string | null; parent_id: string | null; opened_at: string | null; updated_at: string | null; detail: Record<string, unknown> }
export interface Schedule { id: string; name: string; period: string | null; grace: string | null; last_success: string | null; last_status: string | null; last_result_at: string | null; log_tail: string | null; owner_role: string | null }
export interface LedgerEvent { seq: number; id: string; ts_system: string; source: string; kind: string; subject_ref: string; actor: string | null; at: string | null; payload: Record<string, unknown> }

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { method: "GET", headers: { Accept: "application/json" }, cache: "no-store" });
  if (!r.ok) {
    let reason = `${r.status}`;
    try {
      reason = ((await r.json()) as { error?: string }).error ?? reason;
    } catch {
      /* not JSON: keep the status */
    }
    throw new ApiError(r.status, `${path} could not be read: ${reason}`);
  }
  return (await r.json()) as T;
}

export const api = {
  now: () => get<NowModel>("/api/now"),
  roles: () => get<{ roles: Role[] }>("/api/roles").then((b) => b.roles),
  turns: () => get<{ turns: Turn[] }>("/api/turns?limit=500").then((b) => b.turns),
  waits: () => get<{ waits: Wait[] }>("/api/waits").then((b) => b.waits),
  items: () => get<{ items: Item[] }>("/api/items").then((b) => b.items),
  alerts: () => get<{ alerts: Item[] }>("/api/alerts").then((b) => b.alerts),
  schedules: () => get<{ schedules: Schedule[] }>("/api/schedules").then((b) => b.schedules),
  conversation: (role?: string) => get<ConversationModel>(`/api/conversation${role ? `?role=${encodeURIComponent(role)}` : ""}`),
  events: (params: URLSearchParams) =>
    get<{ events: LedgerEvent[]; next_before?: number | null }>(`/api/events?${params.toString()}`),
};

// The Conversation view (decisions 0010 and 0011). A role's own words are never in it: a turn is its outcome line.
export interface ConvDelivery {
  id: string;
  kind: string;
  state: string;
  delivered_at: string | null;
  processing_at: string | null;
  acked_at: string | null;
  acked_by_turn: string | null;
  attempts: number;
  resent: boolean;
  last_error: string | null;
  escalated_to: string | null;
}
export type ConvEntry =
  | { type: "message"; seq: number; at: string; author: "human" | "onloop"; channel: string; text: string | null; delivery: ConvDelivery | null }
  | { type: "would"; seq: number; at: string; kind: string; text: string; why: string; command: string }
  | { type: "run"; seq: number; at: string; role: string; run_id: string; status: string; wall_s: number | null; num_turns: number | null; denials: number; hook_missing: boolean }
  | { type: "turn"; seq: number; at: string; role: string; outcome: string | null; line: string | null; parse_error: string | null }
  | { type: "note"; seq: number; at: string; text: string };
export interface ConvRole {
  role: string;
  session_id: string;
  opted_in: string[];
  paused: boolean;
  entry_seqs: number[];
  attention: string | null;
  open_command: string;
  compose: { enabled: boolean; why: string | null; command: string | null };
}
export interface ConversationModel {
  now: string;
  carrier: { enabled: boolean; paused_reason: string | null; paused_until: string | null; kill_switch: boolean; configured: boolean };
  roles: ConvRole[];
  role: string | null;
  thread: ConvEntry[];
}

// The UI's one write (decision 0011): a message to a role, with the page's session cookie and its CSRF token.
let csrf: string | null = null;

async function csrfToken(): Promise<string> {
  if (!csrf) csrf = (await get<{ csrf: string }>("/api/ui/csrf")).csrf;
  return csrf;
}

export async function sendMessage(role: string, text: string): Promise<{ delivery: string }> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await fetch("/ui/messages", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": await csrfToken() },
      body: JSON.stringify({ role, text }),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (r.ok) return (await r.json()) as { delivery: string };
    if (r.status === 403 && attempt === 0) {
      csrf = null; // onloopd restarted: ask for a fresh token once
      continue;
    }
    let reason = `${r.status}`;
    try {
      reason = ((await r.json()) as { error?: string }).error ?? reason;
    } catch {
      /* keep the status */
    }
    throw new ApiError(r.status, `The message was not taken: ${reason}`);
  }
  throw new ApiError(403, "The message was not taken: reload the page");
}

// The person's task list (decision 0013), the digest since the last visit, and the outcome KPIs (decision 0014).
export type Band = "critical" | "supporting" | "unweighted" | "skippable" | "incomplete";
export interface TaskCard {
  kind?: "task";
  id: string;
  band: Band;
  origin: "outcome" | "cli" | "gate" | "adapter" | "notification" | "node" | "pause" | "wait";
  author_role: string | null;
  ref: string | null;
  title: string | null;
  headline: string;
  asked_by: string | null;
  raw_line: string | null;
  effect: string | null;
  goal: string | null;
  weight: string | null;
  holds: string | null;
  cost_from: string | null;
  cost_unknown: boolean;
  days_left: number | null;
  cost_text: string | null;
  default: string | null;
  stage: "open" | "returned_author" | "returned_coordinator" | "resolved";
  opened_at: string;
  age_s: number;
  return_author_at: string;
  return_coordinator_at: string;
  returned_at: string | null;
  incomplete: boolean;
  incomplete_reasons: string[];
  flags: string[];
  kept_reason: string | null;
  resolution: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
  evidence: string | null;
  without_you: boolean;
  action: string;
}
export interface DigestModel {
  since: string | null;
  now: string;
  added: TaskCard[];
  resolved: TaskCard[];
  resolved_without_you: TaskCard[];
  returned: TaskCard[];
  ticks: { goal_id: string; number: number; kind: string; at: string }[];
  gaps: { from: string; to: string; seconds: number; cause: string | null; at: string }[];
  open: TaskCard[];
}
export interface GoalKpi {
  title: string | null;
  ticks: number;
  unticks: number;
  ticks_without_evidence: number;
  denominator: number | null;
  denominator_at: string | null;
  denominators_in_window: { count: number; at: string }[];
  denominator_before: number | null;
  per_merged_pr: number | null;
  merged_prs_per_tick: number | null;
}
export interface Spread { n: number; median: number | null; p90: number | null }
export interface KpiModel {
  since: string;
  until: string;
  window: string;
  merged_prs: number;
  goals: Record<string, GoalKpi>;
  linked_share: { merged: number; linked: number; none: number; unlinked: number };
  fixed_cost: { queue_hours: Spread; review_rounds: Spread; human_touches: Spread; unmeasured: string[] };
  flow_load: { open: number; idle_7d: number };
  definitions: Record<string, string>;
}
export const tasksApi = {
  digest: () => get<DigestModel>("/api/digest?since=last"),
  kpi: (window = "7d") => get<KpiModel>(`/api/kpi?window=${encodeURIComponent(window)}`),
};

// The page's task writes (decision 0013): evidence from a local user, never an approval. Same rules as a message.
export async function uiPost(
  path: "/ui/tasks/resolve" | "/ui/tasks/keep" | "/ui/tasks/withdraw" | "/ui/tasks/tidy" | "/ui/tasks/merge" | "/ui/tasks/unmerge" | "/ui/tasks/keep_apart" | "/ui/ask" | "/ui/visits",
  body: Record<string, unknown>,
): Promise<unknown> {
  for (let attempt = 0; attempt < 2; attempt++) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": await csrfToken() },
      body: JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (r.ok) return r.json();
    if (r.status === 403 && attempt === 0) {
      csrf = null;
      continue;
    }
    let reason = `${r.status}`;
    try {
      reason = ((await r.json()) as { error?: string }).error ?? reason;
    } catch {
      /* keep the status */
    }
    throw new ApiError(r.status, `Not taken: ${reason}`);
  }
  throw new ApiError(403, "Not taken: reload the page");
}

// -- 0.7.0: Home and Details · Rule alerts (src/onloop/home.py) -------------------------------------------------------

export interface HomeDecision {
  id: string;
  title: string;
  asked_by: string | null;
  askers: string[];
  asks: number;
  ask_log: { at: string; by: string | null; new_fact: boolean }[];
  effect_text: string | null;
  opened_at: string;
  options: string[];
  resolution: string | null;
  answer: string | null;
  answer_text: string | null;
  resolved_at: string | null;
  delivered_at: string | null;
  can_withdraw: boolean;
  can_unmerge?: boolean; // merged into another decision, still open: the merge can be undone
  merged_into?: string | null;
  age_s: number;
  weight: string | null;
  band: string;
  holds: string | null;
  default: string | null;
  cost_text: string | null;
  days_left: number | null;
  ref: string | null;
  goal: string | null;
  origin: string;
  action: string;
}

export interface HomeUnusual { id: string; kind: "quiet" | "tokens" | "wait"; role: string; value: number; norm: number; title: string; text: string; at: string }

export interface HomeLane { role: string; segments: [string, string, string][]; turns: string[]; turn_count: number }

export interface HomeModel {
  now: string;
  from: string;
  to: string;
  frozen: boolean;
  young: boolean;
  brief: { facts: string[]; generated: null | { text: string } };
  numbers: {
    delivered: { value: number; last_week: number; delta: number | null; by_day: number[]; recorded: boolean; items: { ref: string; title: string; at: string }[] };
    waited_h: { value: number; last_week: number; delta: number | null; by_day: number[] };
    cost_per_delivery: { value: number | null; tokens: number; last_week: number | null; delta_pct: number | null; by_day: number[] };
  };
  decisions: { waiting_on_you: number; oldest_s: number | null; count: number; items: HomeDecision[]; rule_asks: number };
  unusual: HomeUnusual[];
  lanes: { from: string; to: string; lanes: HomeLane[] };
}

export interface RulesModel {
  now: string;
  asks_missing: { count: number; by_role: Record<string, number>; missing: Record<string, number>; ids: string[] };
  alerts: { detector: string; words: string; count: number; by_role: Record<string, number> }[];
  since_visit: { since: string | null; added: number; resolved: number; resolved_without_you: number; returned: number; gaps: number };
  constraint: { on: string | null; waiters: number } | null;
  expiring: number;
  health: { failing: number; late: number; ok: number; total: number; dead_man: { configured: boolean; schedule?: string; state?: string } };
}

export interface DecisionsModel {
  now: string;
  open: HomeDecision[];
  count: number;
  rule_asks: number;
  waiting_on_you: number;
  done: HomeDecision[];
  asks_per_decision: number | null;
}

export const homeApi = {
  home: () => get<HomeModel>("/api/home"),
  rules: () => get<RulesModel>("/api/rules"),
  decisions: () => get<DecisionsModel>("/api/decisions"),
};

// -- 0.7.0: Agents and the Timeline (src/onloop/agents.py, src/onloop/timeline_page.py) ------------------------------

export type AgentState = "working" | "waiting" | "idle" | "paused" | "stopped";

export interface AgentCard {
  role: string;
  description: string | null;
  state: AgentState;
  words: string;
  command?: string;
  since?: string;
  on_now: string | null;
  model: string | null;
  turns: number;
  turns_before: number;
  tokens: number;
  tokens_before: number;
  delivered: number;
  open_asks: number;
  turns_by_day: number[];
}

export interface AgentsModel { now: string; from: string; to: string; frozen: boolean; agents: AgentCard[]; tokens: number; turns: number }

export interface AgentDetailModel {
  now: string;
  from: string;
  to: string;
  card: AgentCard;
  by_day: { from: string; turns: number; tokens: number }[];
  delivered: { ref: string; title: string; at: string }[];
  decisions: { open: HomeDecision[]; answered: HomeDecision[] };
  lane: HomeLane | null;
  flags: { ref: string; title: string; at: string }[];
}

export interface TimelineModel {
  now: string;
  from: string;
  to: string;
  days: number;
  frozen: boolean;
  lanes: HomeLane[];
  flags: { ref: string; title: string; at: string }[];
  callouts: { n: number; id: string; role: string; at: string; title: string; text: string }[];
  figures: { role: string; working_s: number; waiting_s: number; turns: number; tokens: number }[];
  your_wait: { count: number; oldest_s: number | null; share: number; waited_s: number };
}

export const agentsApi = {
  all: () => get<AgentsModel>("/api/agents"),
  one: (role: string) => get<AgentDetailModel>(`/api/agents/${encodeURIComponent(role)}`),
  timeline: (days: number) => get<TimelineModel>(`/api/timeline?days=${days}`),
};
