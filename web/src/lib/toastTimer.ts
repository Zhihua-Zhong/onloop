// When a toast goes (0.7.0 plan, section 2, "Undo"): an outside reviewer of F missed the undo because the toast went
// before they reached it. So a toast with an undo stays 9 s, one without 4.5 s; it does not go while the pointer is on
// it or it has focus; and once released, it stays at least 5 s more. Pure, so node's test runner checks it as is.

export const WITH_UNDO_MS = 9000;
export const PLAIN_MS = 4500;
export const AFTER_RELEASE_MS = 5000;

export interface ToastClock {
  /** when the toast goes, in ms; null while it is held */
  endsAt: number | null;
  /** how many holders (the pointer, focus) keep it */
  held: number;
}

export function shown(nowMs: number, undo: boolean): ToastClock {
  return { endsAt: nowMs + (undo ? WITH_UNDO_MS : PLAIN_MS), held: 0 };
}

/** The pointer entered it, or it took focus. */
export function hold(c: ToastClock): ToastClock {
  return { endsAt: null, held: c.held + 1 };
}

/** The pointer left, or focus left: it goes no sooner than 5 s from now, and never sooner than it would have. */
export function release(c: ToastClock, nowMs: number, plannedEndMs: number): ToastClock {
  const held = Math.max(0, c.held - 1);
  if (held > 0) return { endsAt: null, held };
  return { endsAt: Math.max(plannedEndMs, nowMs + AFTER_RELEASE_MS), held: 0 };
}

export function gone(c: ToastClock, nowMs: number): boolean {
  return c.endsAt !== null && nowMs >= c.endsAt;
}
