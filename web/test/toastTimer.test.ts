import assert from "node:assert/strict";
import { test } from "node:test";
import { gone, hold, release, shown } from "../src/lib/toastTimer.ts";

test("a toast with an undo stays 9 s, one without 4.5 s", () => {
  const u = shown(0, true);
  assert.equal(gone(u, 8999), false);
  assert.equal(gone(u, 9000), true);
  const p = shown(0, false);
  assert.equal(gone(p, 4499), false);
  assert.equal(gone(p, 4500), true);
});

test("held by the pointer or focus, it never goes; released, it stays at least 5 s more", () => {
  let c = shown(0, true);
  c = hold(c); // the pointer enters at 2 s
  assert.equal(gone(c, 60_000), false);
  c = release(c, 10_000, 9000); // leaves at 10 s, after the planned end
  assert.equal(gone(c, 14_999), false);
  assert.equal(gone(c, 15_000), true);
});

test("released early, it keeps its planned end when that is later", () => {
  let c = hold(shown(0, true));
  c = release(c, 1000, 9000);
  assert.equal(gone(c, 8999), false);
  assert.equal(gone(c, 9000), true);
});

test("pointer and focus both hold it: it waits for both to let go", () => {
  let c = hold(hold(shown(0, true)));
  c = release(c, 3000, 9000);
  assert.equal(c.endsAt, null);
  c = release(c, 20_000, 9000);
  assert.equal(c.endsAt, 25_000);
});
