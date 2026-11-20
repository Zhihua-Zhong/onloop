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
    return { name, ct, cs, cum, rt, rsum, csum, rstep, base };
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
