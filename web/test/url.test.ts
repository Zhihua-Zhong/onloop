import { test } from "node:test";
import assert from "node:assert/strict";
import { isUrl } from "../src/lib/url.ts";

test("only a web address is a link; a path or a code is not (it would open \"not found\")", () => {
  assert.equal(isUrl("https://github.com/o/r/pull/12"), true);
  assert.equal(isUrl("http://localhost:8790/"), true);
  for (const ref of ["state/design/builder_r5_final_report.md", "pr#45", "r5-planning", "", null, undefined]) {
    assert.equal(isUrl(ref), false, String(ref));
  }
});
