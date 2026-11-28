import assert from "node:assert/strict";
import { test } from "node:test";
import { colorFor, PALETTE } from "../src/lib/roleColor.ts";

test("each role of a team up to eight has its own colour, the same wherever it is drawn", () => {
  const team = ["data-eng", "designer", "builder", "lead", "reviewer", "operator"];
  const colours = team.map((r) => colorFor(team, r));
  assert.equal(new Set(colours).size, team.length); // data-eng and designer no longer share one purple
  assert.equal(colorFor([...team].reverse(), "designer"), colorFor(team, "designer")); // the order given does not matter
  assert.ok(colours.every((c) => PALETTE.includes(c)));
});

test("a role outside the team still gets a colour from the palette", () => {
  assert.ok(PALETTE.includes(colorFor(["builder"], "visitor")));
});
