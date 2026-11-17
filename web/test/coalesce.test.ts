// The live refetch's timing (src/lib/coalesce.ts), on Node's own test runner and mock timers:
//   node --test --experimental-strip-types test/
import { test } from "node:test";
import assert from "node:assert/strict";
import { coalescer, type Timers } from "../src/lib/coalesce.ts";

// A clock the test moves by hand, so every time below is exact.
function fakeTimers() {
  let now = 0;
  let next = 1;
  const pending = new Map<number, { at: number; fn: () => void }>();
  const timers: Timers = {
    set: (fn, ms) => {
      const id = next++;
      pending.set(id, { at: now + ms, fn });
      return id;
    },
    clear: (h) => {
      pending.delete(h as number);
    },
    now: () => now,
  };
  const advanceTo = (t: number) => {
    for (;;) {
      const due = [...pending.entries()].filter(([, p]) => p.at <= t).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at;
      pending.delete(due[0]);
      due[1].fn();
    }
    now = t;
  };
  return { timers, advanceTo };
}

test("a steady stream, one event every 300 ms, is refetched within 2 s", () => {
  const { timers, advanceTo } = fakeTimers();
  const fired: number[] = [];
  const c = coalescer(() => fired.push(timers.now()), 400, 2_000, timers);
  const events: number[] = [];
  for (let t = 0; t <= 6_000; t += 300) {
    advanceTo(t);
    events.push(t);
    c.poke();
  }
  advanceTo(6_000);
  assert.ok(fired.length >= 1, "the page refetched while the stream ran");
  assert.ok(fired[0] <= 2_000, `the first refetch came at ${fired[0]} ms`);
  for (const e of events.filter((x) => x <= 4_000)) {
    // every event the page was sent is on screen within 2 s of its arrival
    assert.ok(fired.some((f) => f >= e && f - e <= 2_000), `the event at ${e} ms waited more than 2 s`);
  }
});

test("a burst is one refetch, 400 ms after its last event", () => {
  const { timers, advanceTo } = fakeTimers();
  const fired: number[] = [];
  const c = coalescer(() => fired.push(timers.now()), 400, 2_000, timers);
  for (let t = 0; t < 500; t += 50) {
    advanceTo(t);
    c.poke();
  }
  advanceTo(5_000);
  assert.deepEqual(fired, [850]);
});

test("a single event is refetched after 400 ms, and cancel stops it", () => {
  const { timers, advanceTo } = fakeTimers();
  const fired: number[] = [];
  const c = coalescer(() => fired.push(timers.now()), 400, 2_000, timers);
  c.poke();
  advanceTo(1_000);
  assert.deepEqual(fired, [400]);
  c.poke();
  c.cancel();
  advanceTo(5_000);
  assert.deepEqual(fired, [400]);
});
