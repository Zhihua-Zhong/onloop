// The ledger API as onloopd serves it (src/onloop/daemon/server.py, src/onloop/report/views.py). Read-only: every
// request here is a GET (decision 0009), and the browser tests fail on any other method.

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
  would: { action: string; target: string | null; at: string; label: string } | null;
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
  events: (params: URLSearchParams) =>
    get<{ events: LedgerEvent[]; next_before?: number | null }>(`/api/events?${params.toString()}`),
};
