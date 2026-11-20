// A step's Markdown products are read as a tree, never as HTML (decision 0019, a4).
import { test } from "node:test";
import assert from "node:assert/strict";
import { inline, parse } from "../src/lib/markdown.ts";

test("blocks: headings, paragraphs, lists, quotes and fenced code", () => {
  const b = parse("# Title\n\nOne line\nand more.\n\n- a\n- b\n\n1. first\n2. second\n\n> said\n\n```\nx < y\n```\n");
  assert.deepEqual(
    b.map((x) => x.t),
    ["h", "p", "ul", "ol", "quote", "pre"],
  );
  assert.deepEqual(b[1], { t: "p", c: [{ t: "text", v: "One line and more." }] });
  assert.equal((b[5] as { v: string }).v, "x < y");
});

test("a document's raw HTML stays text, and only http links are links", () => {
  assert.deepEqual(inline("<script>alert(1)</script>"), [{ t: "text", v: "<script>alert(1)</script>" }]);
  const js = inline("[x](javascript:alert(1))");
  assert.ok(js.every((n) => n.t === "text") && js.map((n) => (n as { v: string }).v).join("").startsWith("x"), JSON.stringify(js));
  assert.deepEqual(inline("[site](https://example.com/a)"), [{ t: "link", href: "https://example.com/a", c: [{ t: "text", v: "site" }] }]);
  assert.deepEqual(inline("a **b** *c* `d`"), [
    { t: "text", v: "a " },
    { t: "strong", c: [{ t: "text", v: "b" }] },
    { t: "text", v: " " },
    { t: "em", c: [{ t: "text", v: "c" }] },
    { t: "text", v: " " },
    { t: "code", v: "d" },
  ]);
});
