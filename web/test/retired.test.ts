import { test } from "node:test";
import assert from "node:assert/strict";
import { RETIRED, retiredTo } from "../src/lib/retired.ts";
import { parse, VIEWS } from "../src/lib/address.ts";

// The retired-link table (0.7.0 plan, section 1), complete: every view 0.6.1 had, and the two it had retired already,
// lands on a current page, with what followed it kept.
const OLD_0_6_1 = ["now", "replay", "map", "outcome", "team", "conversation", "schedules", "audit"];
const RETIRED_BEFORE = ["flow", "activity", "tasks"];

test("every 0.6.1 address has a row, and every row lands on a current view", () => {
  for (const v of [...OLD_0_6_1, ...RETIRED_BEFORE]) {
    assert.ok(v in RETIRED, v);
    const to = retiredTo(`#/${v}`);
    assert.ok(to, v);
    assert.ok((VIEWS as readonly string[]).includes(parse(to).view), `${v} -> ${to}`);
  }
});

test("the plan's table, row by row", () => {
  const want: Record<string, string> = {
    "#/now": "#/decisions",
    "#/tasks": "#/decisions",
    "#/team": "#/agents",
    "#/team/builder": "#/agents/builder",
    "#/replay": "#/timeline",
    "#/replay?t=5": "#/details/replay?t=5",
    "#/replay?hours=24&to=9": "#/details/replay?hours=24&to=9",
    "#/map": "#/details/map",
    "#/map/demo/changes": "#/details/map/demo/changes",
    "#/outcome": "#/details/outcome",
    "#/conversation?role=builder": "#/details/conversation?role=builder",
    "#/schedules": "#/details/schedules",
    "#/audit?kind=pr.merged": "#/details/audit?kind=pr.merged",
    "#/tasks?id=t-1": "#/decisions?id=t-1",
  };
  for (const [from, to] of Object.entries(want)) assert.equal(retiredTo(from), to, from);
});

test("a current address is never rewritten", () => {
  for (const h of ["#/", "#/decisions", "#/agents/builder", "#/timeline?days=7", "#/digest", "#/details/map", "#/details/rules"]) assert.equal(retiredTo(h), null, h);
});
