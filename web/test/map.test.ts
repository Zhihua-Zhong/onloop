import { test } from "node:test";
import assert from "node:assert/strict";
import { gridPosition, nextPath, phoneOrder, showsOpenHint, tallyText, type MapBlock } from "../src/lib/map.ts";

test("a geometric zoom never changes the level; only an explicit open or up does (Q23)", () => {
  for (const zoom of [0.2, 1, 1.6, 2, 8]) {
    assert.deepEqual(nextPath([], { kind: "zoom", zoom }), []);
    assert.deepEqual(nextPath(["goal:a"], { kind: "zoom", zoom }), ["goal:a"]);
  }
  assert.deepEqual(nextPath([], { kind: "open", block: "goal:a" }), ["goal:a"]);
  assert.deepEqual(nextPath(["goal:a"], { kind: "up" }), []);
});

test("Escape at zoom 0 goes nowhere", () => {
  assert.deepEqual(nextPath([], { kind: "up" }), []);
});

test("the Open hint shows from 1.6 times, not before", () => {
  assert.equal(showsOpenHint(1.59), false);
  assert.equal(showsOpenHint(1.6), true);
});

const block = (kind: MapBlock["kind"], tally: Partial<MapBlock["tally"]>, leaves = 1): MapBlock => ({
  id: kind,
  kind,
  title: kind,
  colour: "grey",
  shape: "□",
  label: "not started",
  bar: { done: 0, of: leaves, label: "" },
  tally: { red: 0, amber: 0, moving: 0, grey: 0, done: 0, ...tally },
  reason: null,
  leaves: Array.from({ length: leaves }, (_, i) => ({ id: String(i), title: "", colour: "grey", shape: "□" })),
});

test("a phone reads the batches, then the checklists, then the changes", () => {
  const order = phoneOrder([block("checklist", {}), block("changes", {}), block("feedback", {})]).map((b) => b.kind);
  assert.deepEqual(order, ["feedback", "checklist", "changes"]);
});

test("a tally in words, worst first, without zeros", () => {
  assert.equal(tallyText(block("feedback", { amber: 3, moving: 5 })), "3 wait on you · 5 moving");
  assert.equal(tallyText(block("feedback", { red: 1, grey: 2 })), "1 jammed · 2 not started");
  assert.equal(tallyText(block("checklist", { done: 4 })), "all done");
  assert.equal(tallyText(block("changes", {}, 0)), "nothing in it yet");
});

test("the grid places three blocks a row", () => {
  assert.deepEqual(gridPosition(0), { x: 0, y: 0 });
  assert.equal(gridPosition(3).x, 0);
  assert.ok(gridPosition(3).y > 0 && gridPosition(1).x > 0);
});
