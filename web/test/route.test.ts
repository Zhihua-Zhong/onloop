import assert from "node:assert/strict";
import { test } from "node:test";
import { retiredTo } from "../src/lib/retired.ts";
import { ADDRESS, href, parse, VIEWS } from "../src/lib/address.ts";

test("an old link is rewritten to where its view lives in 0.7.0's shell, keeping its path and query", () => {
  // 0.6.0's Now and its task list are Decisions (plan, section 1)
  assert.equal(retiredTo("#/now"), "#/decisions");
  assert.equal(retiredTo("#/now?mode=you"), "#/decisions?mode=you");
  assert.equal(retiredTo("#/tasks"), "#/decisions");
  assert.equal(retiredTo("#/team"), "#/agents");
  assert.equal(retiredTo("#/replay?hours=24"), "#/timeline?hours=24");
  // a Replay link that names a moment keeps its playback and cursor: Details · Replay
  assert.equal(retiredTo("#/replay?hours=24&t=3"), "#/details/replay?hours=24&t=3");
  assert.equal(retiredTo("#/replay?to=9"), "#/details/replay?to=9");
  assert.equal(retiredTo("#/map/changes/opened?who=merge-check"), "#/details/map/changes/opened?who=merge-check");
  assert.equal(retiredTo("#/conversation?role=builder"), "#/details/conversation?role=builder");
  assert.equal(retiredTo("#/audit?kind=pr.merged"), "#/details/audit?kind=pr.merged");
  // 0.6.0's retired views go straight to their parts' new home
  assert.equal(retiredTo("#/flow?mode=you"), "#/details/map?mode=you");
  assert.equal(retiredTo("#/activity?hours=168"), "#/timeline?hours=168");
  assert.equal(retiredTo("#flow"), "#/details/map");
});

test("a current address is left alone", () => {
  for (const h of ["", "#/", "#/?x=1", "#/agents", "#/timeline?hours=24", "#/details/map/changes/opened?who=merge-check", "#/details/audit", "#/flows", "#/decisions", "#/decisions?x=1"])
    assert.equal(retiredTo(h), null, h);
});

test("every view's link opens that view, with its path and query", () => {
  for (const v of VIEWS) {
    const r = parse(href(v, { a: "1", b: undefined }));
    assert.equal(r.view, v, v);
    assert.equal(r.params.toString(), "a=1");
  }
  assert.deepEqual(parse(href("map", {}, ["changes", "opened"])).path, ["changes", "opened"]);
  assert.equal(href("map", {}, ["changes", "opened"]), "#/details/map/changes/opened");
  assert.equal(href("now"), "#/");
  assert.equal(href("now", { mode: "you" }), "#/?mode=you");
});

test("what is not an address opens the first screen, and an old link is the same view once rewritten", () => {
  for (const h of ["#/nowhere", "#/details", "#/details/nothing", "#/decision"]) assert.equal(parse(h).view, "now", h);
  assert.equal(parse("#/decisions").view, "decisions");
  // the eight views 0.6 had (Rule alerts is new in 0.7.0, so it never had an old address)
  for (const v of VIEWS.filter((x) => !["rules", "decisions", "now", "timeline", "replay"].includes(x))) assert.equal(parse(retiredTo(`#/${v}?k=1`) ?? "").view, v, v);
  assert.equal(parse(retiredTo("#/replay?k=1") ?? "").view, "timeline");
  assert.equal(parse(retiredTo("#/replay?t=1") ?? "").view, "replay");
  assert.equal(parse(retiredTo("#/now?k=1") ?? "").view, "decisions"); // Now is Decisions now
  // nothing in the shell is left at an old address
  for (const v of VIEWS.filter((x) => !["decisions", "rules", "timeline"].includes(x))) assert.notEqual(ADDRESS[v], v); // 0.6's views all moved
});
