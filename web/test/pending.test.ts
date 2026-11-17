import { test } from "node:test";
import assert from "node:assert/strict";
import { pendingMarks, type Timers } from "../src/lib/pending.ts";

function fakeTimers() {
  let t = 0;
  const q: { at: number; fn: () => void; id: number }[] = [];
  let next = 1;
  const timers: Timers = {
    now: () => t,
    set: (fn, ms) => {
      const id = next++;
      q.push({ at: t + ms, fn, id });
      return id;
    },
    clear: (h) => {
      const i = q.findIndex((x) => x.id === h);
      if (i >= 0) q.splice(i, 1);
    },
  };
  const advance = (ms: number) => {
    t += ms;
    for (const x of q.filter((x) => x.at <= t).sort((a, b) => a.at - b.at)) {
      q.splice(q.indexOf(x), 1);
      x.fn();
    }
  };
  return { timers, advance };
}

test("a mark is sent only after the delay", () => {
  const { timers, advance } = fakeTimers();
  const sent: string[] = [];
  const p = pendingMarks((id, r) => sent.push(`${id}:${r}`), 8000, timers);
  p.mark("t-1", "done");
  advance(7999);
  assert.deepEqual(sent, []);
  assert.equal(p.get("t-1")?.resolution, "done");
  advance(1);
  assert.deepEqual(sent, ["t-1:done"]);
  assert.equal(p.get("t-1"), null);
});

test("undo before the delay sends nothing; after it, undo says it is too late", () => {
  const { timers, advance } = fakeTimers();
  const sent: string[] = [];
  const p = pendingMarks((id) => sent.push(id), 8000, timers);
  p.mark("t-1", "declined");
  advance(3000);
  assert.equal(p.undo("t-1"), true);
  advance(10000);
  assert.deepEqual(sent, []);
  p.mark("t-2", "done");
  advance(8000);
  assert.equal(p.undo("t-2"), false);
  assert.deepEqual(sent, ["t-2"]);
});

test("a double click is one mark, and a second mark replaces the first", () => {
  const { timers, advance } = fakeTimers();
  const sent: string[] = [];
  const p = pendingMarks((id, r) => sent.push(`${id}:${r}`), 8000, timers);
  p.mark("t-1", "done");
  p.mark("t-1", "done");
  advance(4000);
  p.mark("t-1", "declined");
  advance(7999);
  assert.deepEqual(sent, []);
  advance(1);
  assert.deepEqual(sent, ["t-1:declined"]);
});
