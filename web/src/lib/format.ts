// How times, durations and reasons read on every view. One place, so the five views say things the same way.

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "unknown";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  if (h < 24) return rm ? `${h} h ${rm} min` : `${h} h`;
  const d = Math.floor(h / 24);
  const rh = h % 24;
  return rh ? `${d} d ${rh} h` : `${d} d`;
}

export function ago(seconds: number | null | undefined): string {
  return seconds == null ? "unknown" : `${duration(seconds)} ago`;
}

export function secondsSince(ts: string | null | undefined, now: string): number | null {
  if (!ts) return null;
  return Math.max(0, (Date.parse(now) - Date.parse(ts)) / 1000);
}

// Clock time in the viewer's zone; the date too when it is not today.
export function clock(ts: string | null | undefined, now?: string, seconds = false): string {
  if (!ts) return "never";
  const d = new Date(ts);
  const ref = now ? new Date(now) : new Date();
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}), hour12: false });
  if (d.toDateString() === ref.toDateString()) return time;
  return `${d.toLocaleDateString(undefined, { month: "short", day: "numeric" })} ${time}`;
}

export const REASON: Record<string, string> = {
  running: "queued for the check",
  conflict: "conflicts with main",
  needs_human: "needs your answer",
  paused_by_human: "paused by you",
  permission: "permission prompt open",
  missing_evidence: "needs evidence",
  missing_verdict: "needs a review",
  continue: "waits for its waker",
  dead_waker: "its waker is dead",
  blocked: "blocked",
  handoff: "handed off",
  schedule: "waits for an event",
};

export const reason = (r: string) => REASON[r] ?? r.replace(/_/g, " ");

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
