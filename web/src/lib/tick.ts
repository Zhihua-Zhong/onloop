// The page's own clock (the project lead's finding on 8796): an age said "1 s ago" two minutes later, and a stalled
// page looked the same as a quiet one. Every age on every page now moves with the clock, and the header says when
// onloopd has gone quiet or stopped answering. Pure, so node:test can hold the rules.
//
// - The server sends an age as of the moment it answered. The page adds the seconds since it received that answer,
//   so an age keeps rising with no new data.
// - onloopd writes a heartbeat every tick (`period_s`, 10 s by default). The page reads it every HEARTBEAT_POLL_MS.
//   News is the newest of a stream event and a heartbeat. With no news for QUIET_PERIODS periods, the page says
//   "no news for N s" (amber): the server may answer while its tick has stopped.
// - When neither the stream nor the heartbeat answers for DOWN_AFTER_MS, the page says "onloopd not answering" (red).

export const TICK_MS = 1_000;
export const HEARTBEAT_POLL_MS = 10_000;
export const QUIET_PERIODS = 2;
export const DOWN_AFTER_MS = 5_000;

// Seconds since the page received an answer (0 before it has one).
export function sinceFetch(fetchedAtMs: number, nowMs: number): number {
  return fetchedAtMs > 0 ? Math.max(0, (nowMs - fetchedAtMs) / 1000) : 0;
}

// An age the server gave, as it stands now.
export function liveAge(ageS: number | null | undefined, fetchedAtMs: number, nowMs: number): number | null {
  return ageS == null ? null : ageS + sinceFetch(fetchedAtMs, nowMs);
}

// The server's `now`, moved on by the time since the answer, for ages the page works out from two times.
export function liveNow(nowIso: string, fetchedAtMs: number, nowMs: number): string {
  const t = Date.parse(nowIso);
  return Number.isFinite(t) ? new Date(t + sinceFetch(fetchedAtMs, nowMs) * 1000).toISOString() : nowIso;
}

// The map's words for an age, as the server's plain_age says them (derive.py), so a ticking age reads the same.
export function plainAge(ageS: number | null | undefined, lowerBound = false): string {
  if (ageS == null) return "an unknown time";
  let text: string;
  if (ageS < 3600) text = `${Math.max(1, Math.floor(ageS / 60))} min`;
  else if (ageS < 10 * 3600) {
    const h = Math.floor(ageS / 3600);
    const m = Math.floor((ageS % 3600) / 60);
    text = m ? `${h} h ${m} min` : `${h} h`;
  } else if (ageS < 86400) text = `${Math.floor(ageS / 3600)} h`;
  else {
    const d = Math.floor(ageS / 86400);
    text = d === 1 ? "1 day" : `${d} days`;
  }
  return lowerBound ? `≥ ${text}` : text;
}

export type LiveState = "connecting" | "live" | "quiet" | "down";

export interface LivenessInput {
  nowMs: number;
  newsAtMs: number | null; // the newest of a stream event (or the stream opening) and onloopd's last heartbeat
  downSinceMs: number | null; // since when neither the stream nor the heartbeat answers; null while either does
  periodS: number; // onloopd's tick, which writes the heartbeat
}

export function liveness({ nowMs, newsAtMs, downSinceMs, periodS }: LivenessInput): { state: LiveState; forS: number | null } {
  if (downSinceMs != null && nowMs - downSinceMs >= DOWN_AFTER_MS) return { state: "down", forS: (nowMs - downSinceMs) / 1000 };
  if (newsAtMs == null) return { state: "connecting", forS: null };
  const quietS = Math.max(0, (nowMs - newsAtMs) / 1000);
  if (quietS > QUIET_PERIODS * periodS) return { state: "quiet", forS: quietS };
  return { state: "live", forS: quietS };
}

// When the heartbeat was written, on the page's clock: the server's age of it, counted back from when it answered.
export function beatAtMs(beatAgeS: number | null | undefined, fetchedAtMs: number): number | null {
  return beatAgeS == null || fetchedAtMs <= 0 ? null : fetchedAtMs - beatAgeS * 1000;
}

// A view's whole answer, as it stands now: every wall-clock age rises by `addS`, a time left falls by it, and the
// map's words for an age are said again from the risen age. `now` at the top moves on too. Ages counted against a
// replay's end (Activity's quiet times while it shows an export) are left alone: the export's end does not move.
const RISING = new Set(["age_s", "oldest_s", "data_age_s"]);
const FALLING = new Set(["left_s"]);

export function tickModel<T>(data: T, addS: number, opts: { liveQuiet?: boolean } = {}): T {
  if (!(addS > 0) || data === null || typeof data !== "object") return data; // no answer yet: nothing to move
  const rising = opts.liveQuiet ? new Set([...RISING, "quiet_for_s"]) : RISING;
  const walk = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(walk);
    if (v === null || typeof v !== "object") return v;
    const o = v as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(o)) {
      if (typeof x === "number" && rising.has(k)) out[k] = x + addS;
      else if (typeof x === "number" && FALLING.has(k)) out[k] = x - addS;
      else out[k] = walk(x);
    }
    if (typeof o.age_text === "string") {
      const s = typeof out.age_s === "number" ? out.age_s : typeof out.oldest_s === "number" ? out.oldest_s : null;
      if (s != null) out.age_text = plainAge(s, o.lower_bound === true);
    }
    return out;
  };
  const moved = walk(data) as Record<string, unknown>;
  if (typeof moved.now === "string") {
    const t = Date.parse(moved.now);
    if (Number.isFinite(t)) moved.now = new Date(t + addS * 1000).toISOString();
  }
  return moved as T;
}
