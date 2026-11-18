import { test } from "node:test";
import assert from "node:assert/strict";
import { BLOCK_MIN_W, gridLayout, nextPath, phoneOrder, showsOpenHint, type MapBlock } from "../src/lib/map.ts";

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
  tally_text: "",
  reason: null,
  leaves: Array.from({ length: leaves }, (_, i) => ({ id: String(i), title: "", colour: "grey", shape: "□" })),
});

test("a phone reads the batches, then the checklists, then the changes", () => {
  const order = phoneOrder([block("checklist", {}), block("changes", {}), block("feedback", {})]).map((b) => b.kind);
  assert.deepEqual(order, ["feedback", "checklist", "changes"]);
});

test("zoom 0 opens at scale 1, so its smallest text (12 px) renders at 12 px or more", () => {
  for (const width of [360, 800, 1072, 1600]) {
    const g = gridLayout(3, width);
    assert.ok(g.zoom >= 1, `opening zoom ${g.zoom} at ${width} px`);
  }
});

test("the blocks share the width: three a row at 1440's canvas, one on a narrow one", () => {
  const wide = gridLayout(3, 1072); // 1440 minus the page's padding and the waits column
  assert.equal(wide.perRow, 3);
  assert.ok(wide.blockW >= BLOCK_MIN_W);
  assert.ok(wide.positions[2].x + wide.blockW <= 1072);
  const narrow = gridLayout(3, 600);
  assert.equal(narrow.perRow, 1);
  assert.ok(narrow.positions[1].y > narrow.positions[0].y);
});
