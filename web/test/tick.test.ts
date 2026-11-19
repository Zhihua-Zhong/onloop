import { test } from "node:test";
import assert from "node:assert/strict";
import { beatAtMs, currentBeat, dataAgeS, DOWN_AFTER_MS, liveAge, liveNow, liveness, plainAge, QUIET_PERIODS, sinceFetch, tickModel } from "../src/lib/tick.ts";

const T0 = Date.parse("2026-11-19T23:09:28.000Z");

test("an age advances with the clock and no new data (the project lead's finding on 8796)", () => {
  // the heartbeat said "1 s ago" when the page read it; two minutes later, with nothing new read, it is 121 s
  assert.equal(liveAge(1, T0, T0), 1);
  assert.equal(liveAge(1, T0, T0 + 120_000), 121);
  assert.ok((liveAge(1, T0, T0 + 2_000) ?? 0) > (liveAge(1, T0, T0 + 1_000) ?? 0));
  assert.equal(liveAge(null, T0, T0 + 5_000), null); // unknown stays unknown
  assert.equal(sinceFetch(0, T0), 0); // nothing read yet: nothing to add
  assert.equal(liveNow("2026-11-19T23:09:28.000Z", T0, T0 + 90_000), "2026-11-19T23:10:58.000Z");
});

test("the map's ticking ages read as the server's do", () => {
  assert.equal(plainAge(30), "1 min");
  assert.equal(plainAge(40 * 60), "40 min");
  assert.equal(plainAge(98 * 60), "1 h 38 min");
  assert.equal(plainAge(14 * 3600 + 59), "14 h");
  assert.equal(plainAge(86400), "1 day");
  assert.equal(plainAge(3 * 86400 + 5, true), "≥ 3 days");
  assert.equal(plainAge(null), "an unknown time");
});

test("a quiet onloopd and a stopped one say so, past stated thresholds", () => {
  const period = 10;
  const at = (s: number) => T0 + s * 1000;
  assert.equal(liveness({ nowMs: at(0), newsAtMs: null, downSinceMs: null, periodS: period }).state, "connecting");
  assert.equal(liveness({ nowMs: at(QUIET_PERIODS * period), newsAtMs: at(0), downSinceMs: null, periodS: period }).state, "live");
  const quiet = liveness({ nowMs: at(QUIET_PERIODS * period + 1), newsAtMs: at(0), downSinceMs: null, periodS: period });
  assert.equal(quiet.state, "quiet");
  assert.equal(quiet.forS, QUIET_PERIODS * period + 1);
  // a failure shorter than the threshold is not yet "not answering"
  assert.equal(liveness({ nowMs: at(3), newsAtMs: at(0), downSinceMs: at(0), periodS: period }).state, "live");
  const down = liveness({ nowMs: T0 + DOWN_AFTER_MS, newsAtMs: at(0), downSinceMs: T0, periodS: period });
  assert.equal(down.state, "down");
  assert.equal(down.forS, DOWN_AFTER_MS / 1000);
  // the heartbeat's own age counts: a beat 30 s old when read is no news since 30 s before
  assert.equal(beatAtMs(30, at(100)), at(70));
  assert.equal(beatAtMs(null, at(100)), null);
});

test("a view's answer moves on as a whole: ages rise, time left falls, the map's words follow", () => {
  const m = {
    now: "2026-11-19T23:09:28.000Z",
    waits: [{ holder: "you", oldest_s: 3590, lower_bound: false, age_text: "59 min" }],
    holders: [{ items: [{ id: "a", age_s: 86390, lower_bound: true, age_text: "≥ 23 h" }] }],
    tasks: [{ age_s: 10, left_s: 100 }],
    lanes: [{ quiet_for_s: 5 }],
    count: 3,
  };
  const t = tickModel(m, 20);
  assert.equal(t.now, "2026-11-19T23:09:48.000Z");
  assert.equal(t.waits[0].oldest_s, 3610);
  assert.equal(t.waits[0].age_text, "1 h");
  assert.equal(t.holders[0].items[0].age_text, "≥ 1 day");
  assert.equal(t.tasks[0].age_s, 30);
  assert.equal(t.tasks[0].left_s, 80);
  assert.equal(t.lanes[0].quiet_for_s, 5); // a replay's quiet time is counted to the export's end
  assert.equal(tickModel(m, 20, { liveQuiet: true }).lanes[0].quiet_for_s, 25);
  assert.equal(t.count, 3);
  assert.equal(tickModel(m, 0), m); // nothing has passed: the same answer, so nothing re-renders
  assert.equal(m.waits[0].oldest_s, 3590); // the answer itself is never changed
});

test("when a newer heartbeat arrives, the card's age resets: every display shows the newest (finding on 2d51352)", () => {
  const load = T0; // the page read /api/now with a heartbeat 1 s old, written at 23:09:27
  const atLoad = "2026-11-19T23:09:27.000Z";
  const later = T0 + 21_000; // the header's /api/heartbeat read, 21 s later, found a beat 9.5 s old
  const newer = "2026-11-19T23:09:39.500Z";
  const nowMs = T0 + 22_000;
  const own = { at: atLoad, age_s: liveAge(1, load, nowMs), fetchedAtMs: nowMs }; // Now's own, ticked to now: 23 s
  assert.equal(own.age_s, 23);
  const b = currentBeat([own, { at: newer, age_s: 9.5, fetchedAtMs: later }], nowMs);
  assert.equal(b?.at, newer);
  assert.equal(b?.age_s, 10.5); // not 23 s and climbing
  assert.equal(b?.stale, false);
  // the order of the readings does not matter, and an old heartbeat alone is still aged honestly
  assert.equal(currentBeat([{ at: newer, age_s: 9.5, fetchedAtMs: later }, own], nowMs)?.at, newer);
  assert.equal(currentBeat([own], T0 + 70_000)?.stale, true);
  assert.equal(currentBeat([{ at: null, age_s: null, fetchedAtMs: later }], nowMs), null);
});

test("the data's age counts only answers that refresh: a digest read once never makes a healthy page look old", () => {
  const nowMs = T0 + 300_000;
  const answers = [
    { key: "now", updatedAtMs: T0 + 290_000, refreshes: true },
    { key: "digest", updatedAtMs: T0, refreshes: false },
    { key: "heartbeat", updatedAtMs: T0 + 299_000, refreshes: true },
  ];
  assert.equal(dataAgeS(answers, nowMs), 10);
  assert.equal(dataAgeS([answers[1]], nowMs), null);
});
