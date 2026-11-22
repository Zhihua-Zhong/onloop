import assert from "node:assert/strict";
import { test } from "node:test";
import { retiredTo } from "../src/lib/retired.ts";

test("a retired view's link is rewritten to where its parts moved, keeping its path and query", () => {
  assert.equal(retiredTo("#/flow"), "#/map");
  assert.equal(retiredTo("#/flow?mode=you"), "#/map?mode=you");
  assert.equal(retiredTo("#/activity?hours=168"), "#/replay?hours=168");
  assert.equal(retiredTo("#flow"), "#/map");
});

test("a live view's link is left alone", () => {
  for (const h of ["", "#/", "#/now", "#/map/changes/opened?who=merge-check", "#/replay?hours=24", "#/flows", "#/audit"])
    assert.equal(retiredTo(h), null, h);
});
