// A person's mark on a task (Done, Decline) waits a few seconds before it is sent, so a misclick can be undone
// (finding #19). The ledger keeps a resolved task resolved, so the undo has to come before the write, not after it.
// Leaving the page before the delay ends sends nothing: the task stays open, which is the safe side of a mistake.

export type Resolution = "done" | "declined";
export interface Pending { id: string; resolution: Resolution; sendAt: number }

export interface Timers {
  now: () => number;
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
}

const realTimers: Timers = {
  now: () => Date.now(),
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
};

export const UNDO_MS = 8000;

export function pendingMarks(
  send: (id: string, resolution: Resolution) => void,
  delayMs = UNDO_MS,
  timers: Timers = realTimers,
) {
  const items = new Map<string, { p: Pending; handle: unknown }>();
  const listeners = new Set<() => void>();
  let snapshot: Pending[] = [];
  const changed = () => {
    snapshot = [...items.values()].map((x) => x.p);
    listeners.forEach((l) => l());
  };
  return {
    /** Mark a task; a second mark of the same task replaces the first and restarts the delay. */
    mark(id: string, resolution: Resolution) {
      const old = items.get(id);
      if (old) timers.clear(old.handle);
      const p = { id, resolution, sendAt: timers.now() + delayMs };
      const handle = timers.set(() => {
        items.delete(id);
        changed();
        send(id, resolution);
      }, delayMs);
      items.set(id, { p, handle });
      changed();
    },
    /** Undo a mark not yet sent. Returns false when it was already sent (or never made). */
    undo(id: string): boolean {
      const x = items.get(id);
      if (!x) return false;
      timers.clear(x.handle);
      items.delete(id);
      changed();
      return true;
    },
    get: (id: string) => items.get(id)?.p ?? null,
    list: () => snapshot,
    subscribe(fn: () => void) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
}
