// The replay's index and frames (decision 0019, §3; spike S3). onloopd serves a window in columns (GET /api/replay);
// the page indexes it once, and then any frame is two binary searches per lane: no fold over the events up to the
// cursor. Nothing here needs data from before the window: `at_from` carries each lane's totals at `from`.

export const KINDS = ["working", "harness", "waiting", "queued"] as const;
export type Kind = (typeof KINDS)[number];

export interface LaneAt {
  state: number; // an index into kinds, -1 idle
  time_s: Record<Kind, number>;
  tokens: number;
  cache_read: number;
  requests: number;
}

export interface CoverageRun { run: string; lane: string; ended_ms: number; reported: number; received: number; share: number | null }
export interface Coverage { runs: number; reported: number; received: number; share: number | null; low: CoverageRun[]; high?: CoverageRun[]; low_below: number; high_above?: number }

export interface ReplayWindow {
  from: string;
  to: string;
  now: string;
  from_ms: number;
  to_ms: number;
  kinds: Kind[];
  lanes: string[];
  at_from: LaneAt[];
  at_to: LaneAt[];
  changes: { t: number[]; lane: number[]; state: number[] };
  requests: { t: number[]; lane: number[]; total: number[]; cache_read: number[]; step: number[] };
  steps: string[];
  step_by?: string[]; // per step: "recorded", or "rule" (rule E's clause 2: the next commit's Node: trailer)
  working_now?: WorkingNow[]; // the recorded steps open at the window's end (decision 0019, a6)
  // Activity's parts, moved to Replay (decision 0019, section 4)
  turns?: { t: number[]; lane: number[]; start: number[] }; // each turn's end, lane, and start (-1: not known)
  data_as_of?: string | null; // the export's time, when the ledger replays a team's daily snapshots
  first_ms?: number | null; // when the ledger's data begins
  // the cross-check: what the window's carried runs reported (the CLI's own count) against what their telemetry brought
  coverage?: Coverage;
  // per lane, when rule E left some of its requests between steps because several roles worked on its branch
  between_shared?: Record<string, number>;
  your_wait?: { count: number; oldest_s: number | null; share: number; waited_s: number; by_role: Record<string, number> }; // a7
}

export interface WorkingNow {
  lane: string;
  step: string;
  since_ms: number;
  tokens: number;
  cache_read: number;
  requests: number;
}

export interface LaneIndex {
  name: string;
  ct: Float64Array; // change times, the first one `from`
  cs: Int8Array; // the state from each change on
  cum: Float64Array[]; // per kind: seconds so far at each change time
  rt: Float64Array; // request times
  rsum: Float64Array; // tokens so far after each request (from at_from's)
  csum: Float64Array; // cache reads so far after each request
  rstep: Int32Array; // each request's step, -1 between steps
  base: LaneAt;
  tt: Float64Array; // ends of turns whose start is not known: drawn as marks, since no working time shows them
  turns: number; // turns that ended in the window
}

export interface ReplayIndex {
  from: number;
  to: number;
  known: number; // nothing is known after this: `now`, when the window runs past it
  lanes: LaneIndex[];
  steps: string[];
  marks: Float64Array; // every state change of any lane, sorted: what "jump to the next event" moves between
}

export interface LaneFrame {
  name: string;
  state: number;
  time_s: Record<Kind, number>;
  tokens: number;
  cache_read: number;
  requests: number;
  step: number; // the step of the lane's latest request, -1 none or between steps
}

const zero = (): Record<Kind, number> => ({ working: 0, harness: 0, waiting: 0, queued: 0 });

/** The largest i with a[i] <= t, or -1. */
export function lastAtOrBefore(a: Float64Array, t: number, n = a.length): number {
  let lo = 0;
  let hi = n - 1;
  let out = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (a[mid] <= t) {
      out = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return out;
}

export function buildIndex(w: ReplayWindow): ReplayIndex {
  const nl = w.lanes.length;
  const changes: { t: number; s: number }[][] = Array.from({ length: nl }, () => []);
  for (let i = 0; i < w.changes.t.length; i++) changes[w.changes.lane[i]].push({ t: w.changes.t[i], s: w.changes.state[i] });
  const tEnds: number[][] = Array.from({ length: nl }, () => []);
  const tCount = new Array(nl).fill(0);
  const T = w.turns ?? { t: [], lane: [], start: [] };
  for (let i = 0; i < T.t.length; i++) {
    tCount[T.lane[i]] += 1;
    if (T.start[i] < 0) tEnds[T.lane[i]].push(T.t[i]);
  }
  const reqs: number[][] = Array.from({ length: nl }, () => []);
  for (let i = 0; i < w.requests.t.length; i++) reqs[w.requests.lane[i]].push(i);
  const all: number[] = [];
  const lanes: LaneIndex[] = w.lanes.map((name, li) => {
    const cl = changes[li].sort((a, b) => a.t - b.t);
    const n = cl.length + 1;
    const ct = new Float64Array(n);
    const cs = new Int8Array(n);
    const cum = KINDS.map(() => new Float64Array(n));
    const base = w.at_from[li];
    ct[0] = w.from_ms;
    cs[0] = base.state;
    KINDS.forEach((k, ki) => (cum[ki][0] = base.time_s[k] ?? 0));
    for (let i = 1; i < n; i++) {
      ct[i] = cl[i - 1].t;
      cs[i] = cl[i - 1].s;
      for (let ki = 0; ki < KINDS.length; ki++) cum[ki][i] = cum[ki][i - 1] + (cs[i - 1] === ki ? (ct[i] - ct[i - 1]) / 1000 : 0);
      all.push(ct[i]);
    }
    const ri = reqs[li];
    const rt = new Float64Array(ri.length);
    const rsum = new Float64Array(ri.length);
    const csum = new Float64Array(ri.length);
    const rstep = new Int32Array(ri.length);
    let tok = base.tokens;
    let cache = base.cache_read;
    ri.forEach((r, j) => {
      rt[j] = w.requests.t[r];
      tok += w.requests.total[r];
      cache += w.requests.cache_read[r];
      rsum[j] = tok;
      csum[j] = cache;
      rstep[j] = w.requests.step[r];
    });
    return { name, ct, cs, cum, rt, rsum, csum, rstep, base, tt: Float64Array.from(tEnds[li].sort((a, b) => a - b)), turns: tCount[li] };
  });
  all.sort((a, b) => a - b);
  return { from: w.from_ms, to: w.to_ms, known: Math.min(Date.parse(w.now), w.to_ms), lanes, steps: w.steps, marks: Float64Array.from(new Set(all)) };
}

/** One lane at time t: two binary searches. Time stops at `known`: the window's part after now is empty. */
export function laneAt(L: LaneIndex, t: number, known = Infinity): LaneFrame {
  const at = Math.min(t, known);
  const i = lastAtOrBefore(L.ct, at);
  const time = zero();
  let state = -1;
  if (i >= 0) {
    state = L.cs[i];
    KINDS.forEach((k, ki) => (time[k] = L.cum[ki][i] + (state === ki ? (at - L.ct[i]) / 1000 : 0)));
  } else KINDS.forEach((k) => (time[k] = L.base.time_s[k] ?? 0));
  const j = lastAtOrBefore(L.rt, at);
  return {
    name: L.name,
    state: t > known ? -1 : state,
    time_s: time,
    tokens: j >= 0 ? L.rsum[j] : L.base.tokens,
    cache_read: j >= 0 ? L.csum[j] : L.base.cache_read,
    requests: L.base.requests + j + 1,
    step: j >= 0 ? L.rstep[j] : -1,
  };
}

export function frameAt(ix: ReplayIndex, t: number): LaneFrame[] {
  return ix.lanes.map((L) => laneAt(L, t, ix.known));
}

/** The next (dir 1) or previous (dir -1) state change of any lane, strictly after or before t; null at the end. */
export function jump(ix: ReplayIndex, t: number, dir: 1 | -1): number | null {
  if (dir === 1) {
    const i = lastAtOrBefore(ix.marks, t) + 1;
    return i < ix.marks.length ? ix.marks[i] : null;
  }
  const i = lastAtOrBefore(ix.marks, t - 1);
  return i >= 0 ? ix.marks[i] : null;
}

export interface Column {
  kind: number; // the kind with the most time in this column, -1 idle
  requests: number;
}

/** What to draw for a lane at a given width: per pixel column, the kind it spent the most time in, and its requests.
 * The drawing never depends on the number of events, only on the width. */
export function columns(L: LaneIndex, from: number, to: number, width: number, known = Infinity): Column[] {
  const out: Column[] = [];
  const span = (to - from) / width;
  let prev = laneAt(L, from, known);
  let prevJ = lastAtOrBefore(L.rt, from);
  for (let x = 0; x < width; x++) {
    const t1 = from + span * (x + 1);
    const cur = laneAt(L, t1, known);
    let best = -1;
    let most = 0;
    KINDS.forEach((k, ki) => {
      const d = cur.time_s[k] - prev.time_s[k];
      if (d > most + 1e-9) {
        most = d;
        best = ki;
      }
    });
    const j = lastAtOrBefore(L.rt, t1);
    out.push({ kind: best, requests: j - prevJ });
    prev = cur;
    prevJ = j;
  }
  return out;
}

/** The naive frame (live.html's way, rejected for the product): fold every change up to t. Kept for the tests. */
export function naiveFrame(w: ReplayWindow, t: number): { state: number; time_s: Record<Kind, number>; tokens: number }[] {
  const known = Math.min(Date.parse(w.now), w.to_ms);
  const at = Math.min(t, known);
  return w.lanes.map((_, li) => {
    const time = { ...zero(), ...w.at_from[li].time_s };
    let state = w.at_from[li].state;
    let since = w.from_ms;
    for (let i = 0; i < w.changes.t.length; i++) {
      if (w.changes.lane[i] !== li || w.changes.t[i] > at) continue;
      if (state >= 0) time[KINDS[state]] += (w.changes.t[i] - since) / 1000;
      state = w.changes.state[i];
      since = w.changes.t[i];
    }
    if (state >= 0) time[KINDS[state]] += (at - since) / 1000;
    let tokens = w.at_from[li].tokens;
    for (let i = 0; i < w.requests.t.length; i++) if (w.requests.lane[i] === li && w.requests.t[i] <= at) tokens += w.requests.total[i];
    return { state: t > known ? -1 : state, time_s: time, tokens };
  });
}

export interface StepTokens {
  step: string;
  by: "recorded" | "rule";
  tokens: number;
  cache_read: number;
  requests: number;
}

export interface LaneBreakdown {
  name: string;
  time_s: Record<Kind, number>; // in the window: at_to less at_from
  tokens: number;
  cache_read: number;
  requests: number;
  steps: StepTokens[]; // most tokens first
  between: { tokens: number; cache_read: number; requests: number }; // no step recorded and none by rule
}

/** Where each lane's time and tokens went in the window (decision 0019, a2 and a3): the four kinds of time, and the
 * tokens by step, recorded or attributed by rule, with what is left between steps kept apart and visible. */
export function breakdown(w: ReplayWindow): LaneBreakdown[] {
  const out: LaneBreakdown[] = w.lanes.map((name, i) => {
    const time = zero();
    for (const k of KINDS) time[k] = Math.max(0, (w.at_to[i]?.time_s[k] ?? 0) - (w.at_from[i]?.time_s[k] ?? 0));
    return { name, time_s: time, tokens: 0, cache_read: 0, requests: 0, steps: [], between: { tokens: 0, cache_read: 0, requests: 0 } };
  });
  const per: Map<number, StepTokens>[] = w.lanes.map(() => new Map());
  for (let r = 0; r < w.requests.t.length; r++) {
    const L = out[w.requests.lane[r]];
    const tok = w.requests.total[r];
    const cache = w.requests.cache_read[r];
    L.tokens += tok;
    L.cache_read += cache;
    L.requests += 1;
    const si = w.requests.step[r];
    if (si < 0) {
      L.between.tokens += tok;
      L.between.cache_read += cache;
      L.between.requests += 1;
      continue;
    }
    const m = per[w.requests.lane[r]];
    const c = m.get(si) ?? { step: w.steps[si], by: w.step_by?.[si] === "rule" ? "rule" : "recorded", tokens: 0, cache_read: 0, requests: 0 };
    c.tokens += tok;
    c.cache_read += cache;
    c.requests += 1;
    m.set(si, c);
  }
  out.forEach((L, i) => (L.steps = [...per[i].values()].sort((a, b) => b.tokens - a.tokens || a.step.localeCompare(b.step))));
  return out;
}

/** Playback by the wall clock (the lead's finding: 600x ran at about 290x in a throttled tab). The cursor is the
 * anchor plus the elapsed wall time times the speed, clamped only at the end: a tab that gets two frames a second
 * skips frames but keeps time. */
/** The summary names both leaders when the second worked within 10% of the first: the glance trial's run 2 had them
 * 34 s apart (710 s and 676 s), and "most work" naming only one read as a claim the page could not back. */
export function mostWorkClose(first: number, second: number): boolean {
  return first > 0 && second > 0 && second >= 0.9 * first;
}

/** Where a link's window ends: at its moment `t` (epoch ms) when it names one in the past, so a shared link opens the
 * same window whenever it is opened; null otherwise (the window ends now, and moves with the clock when live). */
export function windowEnd(t: string | null, nowMs: number): number | null {
  if (!t) return null;
  const v = Number(t);
  return Number.isFinite(v) && v > 0 && v < nowMs ? v : null;
}

export function playhead(anchorT: number, anchorWall: number, nowWall: number, speed: number, end: number): number {
  return Math.min(end, anchorT + Math.max(0, nowWall - anchorWall) * speed);
}

/** How close to the end counts as "at the end": a second, or a thousandth of the window when that is more. A cursor
 * kept in a link from a pause can sit a frame short of the end. */
export function endSlack(ix: ReplayIndex): number {
  return Math.max(1000, (ix.to - ix.from) / 1000);
}

/** Where Play starts (the video idiom): from the window's start when the cursor is at the end, else where it is. */
export function playFrom(ix: ReplayIndex, t: number): number {
  return t >= ix.known - endSlack(ix) ? ix.from : t;
}

/** The previous change, at least a second before the cursor, so that from the end it is the last change before it
 * and never one a few milliseconds back. */
export function previous(ix: ReplayIndex, t: number): number | null {
  return jump(ix, t - 1000 + 1, -1);
}

/** A frame in the window's terms: what happened between the window's start and the cursor (the frame less each
 * lane's totals at `from`). Each lane's time is then at most the time elapsed since the window's start. */
export function inWindow(ix: ReplayIndex, frame: LaneFrame[]): LaneFrame[] {
  return frame.map((f, i) => {
    const b = ix.lanes[i].base;
    const time = { ...f.time_s };
    for (const k of KINDS) time[k] = Math.max(0, f.time_s[k] - (b.time_s[k] ?? 0));
    return { ...f, time_s: time, tokens: f.tokens - b.tokens, cache_read: f.cache_read - b.cache_read, requests: f.requests - b.requests };
  });
}

// -- Activity's parts, moved to Replay (decision 0019, section 4) ----------------------------------------------------

/** Per lane, every moment it showed a sign of life in the window, sorted: a turn's start and end, a model request, a
 * move into working. What "quiet" is measured between. */
export function activityTimes(w: ReplayWindow): Float64Array[] {
  const out: number[][] = w.lanes.map(() => []);
  const T = w.turns ?? { t: [], lane: [], start: [] };
  for (let i = 0; i < T.t.length; i++) {
    out[T.lane[i]].push(T.t[i]);
    if (T.start[i] >= 0) out[T.lane[i]].push(Math.max(T.start[i], w.from_ms));
  }
  for (let i = 0; i < w.requests.t.length; i++) out[w.requests.lane[i]].push(w.requests.t[i]);
  for (let i = 0; i < w.changes.t.length; i++) if (w.changes.state[i] === 0) out[w.changes.lane[i]].push(w.changes.t[i]);
  w.at_from.forEach((a, i) => {
    if (a.state === 0) out[i].push(w.from_ms); // working when the window opens
  });
  return out.map((v) => Float64Array.from(v.sort((a, b) => a - b)));
}

/** How long a lane has been quiet at t: since its last sign of life at or before t; null when none in the window. */
export function quietAt(times: Float64Array, t: number): number | null {
  const i = lastAtOrBefore(times, t);
  return i < 0 ? null : (t - times[i]) / 1000;
}

/** A lane's longest quiet stretch in [from, to]: between two signs of life, or from the last one to `to` (ongoing).
 * Before its first sign of life in the window nothing is known of it, so that stretch is not counted. */
export function longestQuiet(times: Float64Array, from: number, to: number): { from: number; to: number; seconds: number; ongoing: boolean } | null {
  let best: { from: number; to: number; seconds: number; ongoing: boolean } | null = null;
  let prev: number | null = null;
  for (const t of times) {
    if (t < from || t > to) continue;
    if (prev !== null && t - prev > (best ? best.seconds * 1000 : 0)) best = { from: prev, to: t, seconds: (t - prev) / 1000, ongoing: false };
    prev = t;
  }
  if (prev !== null && to - prev > (best ? best.seconds * 1000 : 0)) best = { from: prev, to, seconds: (to - prev) / 1000, ongoing: true };
  return best;
}

/** Per clock hour of the window: how many turns ended, and how many lanes showed a sign of life (roles at once). */
export function perHour(w: ReplayWindow, times: Float64Array[]): { at: number; turns: number; roles: number }[] {
  const H = 3_600_000;
  const out: { at: number; turns: number; roles: number }[] = [];
  for (let at = Math.floor(w.from_ms / H) * H; at < w.to_ms; at += H) out.push({ at, turns: 0, roles: 0 });
  const T = w.turns ?? { t: [], lane: [], start: [] };
  const first = out.length ? out[0].at : 0;
  for (const t of T.t) {
    const k = Math.floor((t - first) / H);
    if (k >= 0 && k < out.length) out[k].turns += 1;
  }
  for (const lane of times) {
    const seen = new Set<number>();
    for (const t of lane) {
      const k = Math.floor((t - first) / H);
      if (k >= 0 && k < out.length) seen.add(k);
    }
    for (const k of seen) out[k].roles += 1;
  }
  return out;
}
