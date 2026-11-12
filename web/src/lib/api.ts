// The ledger API as onloopd serves it (src/onloop/daemon/server.py, src/onloop/report/views.py). Read-only: every
// request here is a GET (decision 0009) but one: a message to a role (`sendMessage`, decision 0011), and the browser
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

export interface NowModel {
  now: string;
  constraint: Bottleneck | null;
  related: { bottlenecks: { on_ref: string; on_kind: string; waiters: number }[]; conflicts: number; blocked_items: number } | null;
  next: { text: string; command: string | null; item_id: string | null; ref: string | null } | null;
  waiting_on_you: WaitCard[];
  stalls: Stall[];
  health: {
    schedules: ScheduleHealth[];
    schedules_ok: number;
    schedules_total: number;
    onloopd: { at: string; age_s: Seconds; pid: number | null; stale: boolean; errors: string[] } | null;
    dead_man: { configured: false } | { configured: true; schedule: string; state: string; last_success: string | null };
  };
}

export type ItemState = "open" | "ready" | "blocked" | "in_review" | "done";

export interface FlowCard {
  id: string;
  title: string;
  kind: "task" | "decision" | "followup";
  state: ItemState;
  owner_role: string | null;
  ref: string | null;
  zone: string;
  opened_at: string | null;
  updated_at: string | null;
  age_s: Seconds;
  default: string | null;
  expires: string | null;
  class: string | null;
}
export interface FlowZone { id: string; label: string; source: "direction" | "owner" | "none"; cards: number }
export interface FlowResource {
  id: string;
  kind: "human" | "lock" | "queue" | "waker";
  state: string | null;
  capacity: number | null;
  holders: string[];
  waiting: number;
  waiting_roles: string[];
}
export interface FlowWait { id: string; card: string | null; waiter: string; on_kind: string; on_ref: string; reason: string; since: string; age_s: Seconds }
export interface FlowEdge { id: string; kind: "parent" | "depends"; source: string; target: string }
export interface FlowModel {
  now: string;
  zones: FlowZone[];
  cards: FlowCard[];
  edges: FlowEdge[];
  resources: FlowResource[];
  waits: FlowWait[];
  constraint: { node: string; on_kind: string; downstream_wait_s: number; cards: string[] } | null;
  schedules: { id: string; name: string; period: string | null; last_result_at: string | null; last_success: string | null; next_due: string | null }[];
}

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
  flow: () => get<FlowModel>("/api/flow"),
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
