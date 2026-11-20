// The replay's index (decision 0019, spike S3): a frame from the index equals the ledger's own timeline at that time
// (the fixture's frames, written by tests/test_replay_api.py from derive.timeline) and the naive fold of the window.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildIndex, columns, frameAt, jump, KINDS, naiveFrame, type ReplayWindow } from "../src/lib/replay.ts";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/replay_window.json", import.meta.url), "utf8")) as {
  window: ReplayWindow;
  expected: { t: number; lanes: { time_s: Record<string, number>; tokens: number }[] }[];
};

const close = (a: number, b: number, what: string) => assert.ok(Math.abs(a - b) < 0.01, `${what}: ${a} != ${b}`);

test("every frame from the index is the ledger's own timeline at that time", () => {
  const ix = buildIndex(fixture.window);
  for (const e of fixture.expected) {
    const f = frameAt(ix, e.t);
    e.lanes.forEach((want, i) => {
      for (const k of KINDS) close(f[i].time_s[k], want.time_s[k], `${f[i].name} ${k} at ${e.t}`);
      assert.equal(f[i].tokens, want.tokens, `${f[i].name} tokens at ${e.t}`);
    });
  }
});

// a made-up window: lanes that change state often, and requests, to hold the index to the naive fold
function synthetic(seed: number, lanes: number, changes: number, requests: number): ReplayWindow {
  let s = seed;
  const rnd = () => ((s = (s * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
  const from = Date.UTC(2026, 10, 2);
  const to = from + 86_400_000;
  const ch = Array.from({ length: changes }, () => ({ t: from + 1 + Math.floor(rnd() * (to - from - 1)), lane: Math.floor(rnd() * lanes), state: Math.floor(rnd() * 5) - 1 }));
  ch.sort((a, b) => a.t - b.t || a.lane - b.lane);
  const rq = Array.from({ length: requests }, () => ({ t: from + Math.floor(rnd() * (to - from)), lane: Math.floor(rnd() * lanes), total: 1 + Math.floor(rnd() * 5000) }));
  rq.sort((a, b) => a.t - b.t);
  const at = () => ({ state: Math.floor(rnd() * 5) - 1, time_s: { working: 10, harness: 2, waiting: 30, queued: 1 }, tokens: 100, cache_read: 0, requests: 1 });
  return {
    from: new Date(from).toISOString(),
    to: new Date(to).toISOString(),
    now: new Date(to - 3_600_000).toISOString(), // the last hour is after now: nothing known
    from_ms: from,
    to_ms: to,
    kinds: [...KINDS],
    lanes: Array.from({ length: lanes }, (_, i) => `lane-${i}`),
    at_from: Array.from({ length: lanes }, at),
    at_to: [],
    changes: { t: ch.map((c) => c.t), lane: ch.map((c) => c.lane), state: ch.map((c) => c.state) },
    requests: { t: rq.map((r) => r.t), lane: rq.map((r) => r.lane), total: rq.map((r) => r.total), cache_read: rq.map(() => 0), step: rq.map(() => -1) },
    steps: [],
  };
}

test("the index agrees with the naive fold everywhere, and stops at now", () => {
  const w = synthetic(11, 6, 3000, 5000);
  const ix = buildIndex(w);
  for (let k = 0; k <= 200; k++) {
    const t = w.from_ms + Math.floor((k * (w.to_ms - w.from_ms)) / 200);
    const a = frameAt(ix, t);
    const b = naiveFrame(w, t);
    a.forEach((x, i) => {
      assert.equal(x.state, b[i].state, `state ${i} at ${t}`);
      assert.equal(x.tokens, b[i].tokens, `tokens ${i} at ${t}`);
      for (const kind of KINDS) close(x.time_s[kind], b[i].time_s[kind], `${kind} ${i} at ${t}`);
    });
  }
  const end = frameAt(ix, w.to_ms);
  assert.ok(end.every((f) => f.state === -1), "after now no lane has a state");
});

test("jump moves between state changes, and stops at the ends", () => {
  const ix = buildIndex(fixture.window);
  const first = jump(ix, ix.from, 1);
  assert.ok(first !== null && first > ix.from);
  assert.equal(jump(ix, first, -1), null);
  let t = ix.from;
  let n = 0;
  for (let next = jump(ix, t, 1); next !== null; next = jump(ix, t, 1)) {
    assert.ok(next > t);
    t = next;
    n++;
  }
  assert.equal(n, ix.marks.length);
});

test("a drawing's columns depend on the width, not on how many events there are", () => {
  const w = synthetic(5, 3, 20000, 50000);
  const ix = buildIndex(w);
  const cols = columns(ix.lanes[0], w.from_ms, w.to_ms, 300, ix.known);
  assert.equal(cols.length, 300);
  const reqs = w.requests.lane.filter((l) => l === 0 && true).length;
  const counted = cols.reduce((s, c) => s + c.requests, 0);
  assert.ok(counted <= reqs && counted >= reqs - 5, `${counted} of ${reqs}`);
});

test("a frame of a week of a hundred thousand requests is fast", () => {
  const w = synthetic(3, 22, 100_000, 100_000);
  const ix = buildIndex(w);
  const t0 = performance.now();
  for (let k = 0; k < 1000; k++) frameAt(ix, w.from_ms + Math.floor(Math.random() * (w.to_ms - w.from_ms)));
  const per = (performance.now() - t0) / 1000;
  assert.ok(per < 1, `${per} ms per frame`);
});
