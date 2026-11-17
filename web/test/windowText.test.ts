import { test } from "node:test";
import assert from "node:assert/strict";
import { windowText } from "../src/lib/windowText.ts";

test("a window reads in days when it is whole days, else in hours", () => {
  assert.equal(windowText(168), "the last 7 days");
  assert.equal(windowText(48), "the last 2 days");
  assert.equal(windowText(24), "the last 24 hours");
  assert.equal(windowText(6), "the last 6 hours");
});
