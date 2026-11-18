import { test } from "node:test";
import assert from "node:assert/strict";
import { constraintSentence } from "../src/lib/constraint.ts";

test("one sentence for the constraint, the same on Now and on Flow", () => {
  assert.equal(constraintSentence({ on_kind: "role", on_ref: "lead", waiters: 1 }), "lead holds 1 change");
  assert.equal(constraintSentence({ on_kind: "human", on_ref: "human", waiters: 3 }), "you hold 3 items");
  assert.equal(
    constraintSentence({ on_kind: "resource", on_ref: "merge-check", waiters: 5 }),
    "merge-check holds 5 changes in its queue",
  );
});
