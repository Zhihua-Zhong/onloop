import { test } from "node:test";
import assert from "node:assert/strict";
import { windowSpanText, windowText } from "../src/lib/windowText.ts";

test("a window reads in days when it is whole days, else in hours", () => {
  assert.equal(windowText(168), "the last 7 days");
  assert.equal(windowText(48), "the last 2 days");
  assert.equal(windowText(24), "the last 24 hours");
  assert.equal(windowText(6), "the last 6 hours");
});

test("a window shortened to where the data begins says so (mock-up v2)", () => {
  assert.equal(windowSpanText(168, "2026-11-13T08:00:00.000Z", "2026-11-17T11:00:00.000Z"), "the 4 days since the data began");
  assert.equal(windowSpanText(48, "2026-11-16T05:00:00.000Z", "2026-11-17T02:00:00.000Z"), "the 21 hours since the data began");
  assert.equal(windowSpanText(48, "2026-11-15T02:00:00.000Z", "2026-11-17T02:00:00.000Z"), "the last 2 days");
});
