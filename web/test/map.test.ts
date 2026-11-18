import { test } from "node:test";
import assert from "node:assert/strict";
import {
  BLOCK_MIN_W,
  cardLayout,
  chainLinks,
  createOpener,
  gridLayout,
  isReserved,
  nextPath,
  openMoveMs,
  phoneOrder,
  showsOpenHint,
  type MapBlock,
} from "../src/lib/map.ts";

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

test("cards of different heights: each row as tall as its tallest, still at scale 1", () => {
  const g = cardLayout([200, 320, 180, 240], 1072);
  assert.equal(g.zoom, 1);
  assert.equal(g.perRow, 3);
  assert.equal(g.positions[3].y, g.positions[0].y + 320 + 24); // the second row starts under the tallest card
});

test("zoom 3 and 4 are reserved; 0 to 2 are built", () => {
  assert.equal(isReserved(["goal:a", "built"]), false);
  assert.equal(isReserved(["goal:a", "built", "builder"]), true);
});

test("opening moves the camera for about 250 ms, and not at all with reduced motion", () => {
  assert.equal(openMoveMs(false), 250);
  assert.equal(openMoveMs(true), 0);
});

test("a second open during the camera's move is ignored: one level, never two", async () => {
  let release: () => void = () => undefined;
  const opener = createOpener(() => new Promise<void>((r) => (release = r)));
  const opened: string[] = [];
  const first = opener(() => undefined, 250, () => opened.push("first"));
  const second = await opener(() => undefined, 250, () => opened.push("second"));
  release();
  assert.equal(await first, true);
  assert.equal(second, false);
  assert.deepEqual(opened, ["first"]);
  assert.equal(await opener(() => undefined, 0, () => opened.push("later")), true); // free again afterwards
});

test("the stages' chain never crosses the canvas: no edge where a row wraps", () => {
  const g = cardLayout([200, 200, 200, 200, 200], 1072); // three a row: stage 3 is the last of the first row
  const { edges, wraps } = chainLinks(g.positions);
  assert.deepEqual(edges, [[0, 1], [1, 2], [3, 4]]);
  assert.deepEqual(wraps, [2]);
});
