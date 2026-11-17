// Coalescing a stream of live events into refetches. A burst becomes one refetch, 400 ms after its last event; but a
// steady stream (an event every few hundred milliseconds) must not hold the page back for ever: the first event of a
// run is refetched at most `maxWaitMs` later, whatever keeps arriving. Without that cap, a trailing debounce fires only
// when the stream stops, and every view froze for as long as the team was busy (spike O-S3).
//
// Plain TypeScript with no imports, so `node --test --experimental-strip-types` runs its test without a bundler.

export interface Timers {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
  now(): number;
}

const browserTimers: Timers = {
  set: (fn, ms) => globalThis.setTimeout(fn, ms),
  clear: (h) => globalThis.clearTimeout(h as ReturnType<typeof setTimeout>),
  now: () => Date.now(),
};

export interface Coalescer {
  poke(): void;
  cancel(): void;
}

export function coalescer(fire: () => void, waitMs = 400, maxWaitMs = 2_000, timers: Timers = browserTimers): Coalescer {
  let handle: unknown;
  let firstAt: number | null = null;
  const run = () => {
    handle = undefined;
    firstAt = null;
    fire();
  };
  return {
    poke() {
      const now = timers.now();
      if (firstAt === null) firstAt = now;
      if (handle !== undefined) timers.clear(handle);
      handle = timers.set(run, Math.max(0, Math.min(waitMs, firstAt + maxWaitMs - now)));
    },
    cancel() {
      if (handle !== undefined) timers.clear(handle);
      handle = undefined;
      firstAt = null;
    },
  };
}
