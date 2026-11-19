import { test } from "node:test";
import assert from "node:assert/strict";
import { downMessage, refetchEvery, retrying, RETRY_WHILE_DOWN_MS, viewState } from "../src/lib/recovery.ts";

test("a query in error is tried again every few seconds, then back to its pace", () => {
  const every = refetchEvery(30_000);
  assert.equal(every({ state: { status: "error" } }), RETRY_WHILE_DOWN_MS);
  assert.ok(RETRY_WHILE_DOWN_MS <= 5_000);
  assert.equal(every({ state: { status: "success" } }), 30_000);
});

test("the last good data stays on screen while a refresh fails; an error alone says onloopd is down", () => {
  assert.equal(viewState("error", true), "data-stale");
  assert.equal(viewState("error", false), "down");
  assert.equal(viewState("success", true), "data");
  assert.equal(viewState("pending", false), "loading");
});

test("the browser's 'Failed to fetch' is said in plain words", () => {
  assert.match(downMessage(new TypeError("Failed to fetch")), /onloopd cannot be reached/);
  assert.equal(downMessage(new Error("/api/map could not be read: 500")), "/api/map could not be read: 500");
});

test("a view's code that failed to load is fetched again until it arrives", async () => {
  let tries = 0;
  const waits: number[] = [];
  const load = retrying(
    async () => {
      tries += 1;
      if (tries < 3) throw new TypeError("Failed to fetch dynamically imported module");
      return "the map";
    },
    3_000,
    async (ms) => {
      waits.push(ms);
    },
  );
  assert.equal(await load(), "the map");
  assert.equal(tries, 3);
  assert.deepEqual(waits, [3_000, 3_000]);
});
