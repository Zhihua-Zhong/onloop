import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, SkipBack, SkipForward } from "lucide-react";
import { get } from "@/lib/api";
import { duration, plural } from "@/lib/format";
import { href } from "@/lib/route";
import { breakdown, buildIndex, columns, frameAt, inWindow, jump, KINDS, lastAtOrBefore as lastAtOrBeforeIdx, playFrom, playhead, previous, type Kind, type LaneBreakdown, type LaneFrame, type ReplayIndex, type ReplayWindow, type WorkingNow } from "@/lib/replay";
import { useNowMs } from "@/lib/clock";
import { parse as parseMd, type Block, type Inline } from "@/lib/markdown";
import { Loading, Problem } from "@/components/kit";
import { cn } from "@/lib/utils";

// Replay (decision 0019, a1): the team's work over time, as lanes in proportion to time. A cursor shows each lane's
// state, its four kinds of time and its tokens at that moment; it plays at 1x, 10x, 60x or 600x, or jumps from one
// state change to the next. Every number comes from onloopd's window (GET /api/replay) and the page's index of it.

const WINDOWS: [number, string][] = [
  [1, "1 h"],
  [6, "6 h"],
  [24, "24 h"],
  [168, "7 days"],
];
const SPEEDS = [1, 10, 60, 600] as const;
const WORKER_AT = 100_000; // events in a window past which the index is built off the page's thread

export const KIND_LABEL: Record<Kind, string> = { working: "Working", harness: "Harness", waiting: "Waiting on you", queued: "Queued" };
const KIND_HELP: Record<Kind, string> = {
  working: "a turn running, or a model request",
  harness: "onloop starting or finishing a carried turn, with no turn running yet",
  waiting: "a task the role asked you, still open",
  queued: "a message to the role, not yet delivered",
};
const KIND_FILL: Record<Kind, string> = {
  working: "fill-state-done",
  harness: "fill-state-external",
  waiting: "fill-state-human",
  queued: "fill-state-void",
};
const KIND_DOT: Record<Kind, string> = { working: "bg-state-done", harness: "bg-state-external", waiting: "bg-state-human", queued: "bg-state-void" };
// The work carries the weight; a wait is true but light (the lead's finding: days-long waits drowned the work)
const KIND_WEIGHT: Record<Kind, number> = { working: 0.95, harness: 0.8, waiting: 0.22, queued: 0.45 };
const KIND_SWATCH: Record<Kind, string> = { working: "opacity-95", harness: "opacity-80", waiting: "opacity-25", queued: "opacity-50" };

function stamp(ms: number, withDay: boolean, seconds = false): string {
  const d = new Date(ms);
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}), hour12: false });
  return withDay ? `${d.toLocaleDateString([], { weekday: "short" })} ${t}` : t;
}

function tokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    setW(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function useIndex(w: ReplayWindow | undefined): ReplayIndex | null {
  const [ix, setIx] = useState<ReplayIndex | null>(null);
  useEffect(() => {
    if (!w) return;
    const events = w.changes.t.length + w.requests.t.length;
    if (events <= WORKER_AT || typeof Worker === "undefined") {
      setIx(buildIndex(w));
      return;
    }
    const worker = new Worker(new URL("../lib/replayWorker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<ReplayIndex>) => setIx(e.data);
    worker.onerror = () => setIx(buildIndex(w)); // the page's own thread, slower, still right
    worker.postMessage(w);
    return () => worker.terminate();
  }, [w]);
  return ix;
}

const LIVE_EVERY_MS = 15_000;

export function ReplayView({ params }: { params: URLSearchParams }) {
  const hours = Number(params.get("hours")) || 24;
  // Live (a6): with no cursor in the URL the window ends now and moves with the clock, asked again every 15 s; the
  // cursor stays at now. Any move of the cursor leaves live; the Live button returns to it.
  const [live, setLive] = useState(() => !params.get("t"));
  const q = useQuery({
    queryKey: ["replay", hours],
    queryFn: () => {
      const end = Date.now();
      const from = new Date(end - hours * 3_600_000).toISOString();
      const to = new Date(end).toISOString();
      return get<ReplayWindow>(`/api/replay?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
    },
    staleTime: live ? 0 : Infinity,
    refetchInterval: live ? LIVE_EVERY_MS : false,
  });
  const ix = useIndex(q.data);
  const bd = useMemo(() => (q.data ? breakdown(q.data) : []), [q.data]);
  if (q.isPending || (q.data && !ix)) return <Loading what="the replay" />;
  if (q.isError && !q.data) return <Problem error={q.error} />;
  return (
    <Player
      ix={ix!}
      bd={bd}
      working={q.data?.working_now ?? []}
      wait={q.data?.your_wait ?? null}
      hours={hours}
      params={params}
      live={live}
      setLive={(on) => {
        setLive(on);
        if (on) void q.refetch();
      }}
    />
  );
}

function Player(p: {
  ix: ReplayIndex;
  bd: LaneBreakdown[];
  working: WorkingNow[];
  wait: ReplayWindow["your_wait"] | null;
  hours: number;
  params: URLSearchParams;
  live: boolean;
  setLive: (on: boolean) => void;
}) {
  const { ix, bd, hours, params, live, setLive } = p;
  const initial = Number(params.get("t"));
  const [t, setTRaw] = useState(() => (initial >= ix.from && initial <= ix.to ? initial : ix.known));
  // a cursor the person moves leaves live; live keeps the cursor at now as each new window arrives
  const setT: typeof setTRaw = (v) => {
    if (live) setLive(false);
    setTRaw(v);
  };
  useEffect(() => {
    if (live) setTRaw(ix.known);
  }, [live, ix]);
  const [speed, setSpeed] = useState<number>(() => {
    const s = Number(params.get("speed"));
    return (SPEEDS as readonly number[]).includes(s) ? s : 60;
  });
  const [playing, setPlaying] = useState(false);
  const table = params.get("table") === "1";
  const ledgerScope = params.get("scope") === "ledger"; // the window's figures by default; the ledger's on request
  const [picked, setPicked] = useState<string | null>(null); // a step whose products are shown (a4)
  const frameMs = useRef(0);
  const box = useRef<HTMLDivElement>(null);

  // the URL keeps the cursor and the speed, so a reload or a shared link opens the same moment; not on every frame
  const keep = useCallback(
    (at: number, sp = speed) => {
      window.history.replaceState(null, "", href("replay", { hours: String(hours), t: live ? undefined : String(Math.round(at)), speed: String(sp), table: table ? "1" : undefined }));
    },
    [hours, speed, table, live],
  );

  // playback by the wall clock: the cursor is where it was when play (or a new speed) began, plus the wall time since
  // then times the speed; a throttled tab skips frames but keeps time
  const tRef = useRef(t);
  tRef.current = t;
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let done = false;
    const anchorT = tRef.current;
    const anchorWall = performance.now();
    const tick = () => {
      if (done) return;
      const next = playhead(anchorT, anchorWall, performance.now(), speed, ix.known);
      setTRaw(next);
      if (next >= ix.known) {
        done = true;
        setPlaying(false);
      }
    };
    const step = () => {
      tick();
      if (!done) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    // a background or throttled tab gets few or no animation frames; a timer still fires, and the time comes from the
    // wall clock either way
    const timer = window.setInterval(tick, 250);
    return () => {
      done = true;
      cancelAnimationFrame(raf);
      window.clearInterval(timer);
    };
  }, [playing, speed, ix.known]);

  useEffect(() => {
    if (!playing) keep(t);
  }, [playing, t, keep]);

  const t0 = performance.now();
  const frame = useMemo(() => frameAt(ix, t), [ix, t]);
  useLayoutEffect(() => {
    frameMs.current = performance.now() - t0;
    box.current?.setAttribute("data-frame-ms", frameMs.current.toFixed(2));
  });

  const go = (dir: 1 | -1) => {
    const at = dir === 1 ? jump(ix, t, 1) : previous(ix, t);
    setPlaying(false);
    setT(at ?? (dir === 1 ? ix.known : ix.from));
  };
  const toggle = () => {
    if (live) setLive(false);
    if (!playing) setTRaw(playFrom(ix, t)); // at the end, play starts over (the video idiom)
    setPlaying((v) => !v);
  };
  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === "INPUT" && (e.target as HTMLInputElement).type !== "range") return;
    if (e.key === " ") {
      e.preventDefault();
      toggle();
    } else if (e.key === "ArrowRight" && e.shiftKey) {
      e.preventDefault();
      go(1);
    } else if (e.key === "ArrowLeft" && e.shiftKey) {
      e.preventDefault();
      go(-1);
    }
  };

  const active = frame.filter((_, i) => isActive(ix, i));
  const shown = useMemo(() => (ledgerScope ? frame : inWindow(ix, frame)), [ledgerScope, frame, ix]);
  const spanDays = (ix.to - ix.from) / 86_400_000 > 1;
  const span = hours >= 24 ? `${hours / 24} day${hours > 24 ? "s" : ""}` : `${hours} h`;
  const scope = ledgerScope ? `Since the ledger began, up to ${stamp(t, spanDays)}` : `In this ${span} window, up to ${stamp(t, spanDays)}`;
  const scopeHref = href("replay", { hours: String(hours), t: String(Math.round(t)), speed: String(speed), table: table ? "1" : undefined, scope: ledgerScope ? undefined : "ledger" });

  return (
    <div ref={box} className="mx-auto max-w-[1600px] px-6 py-5 max-sm:px-4" data-testid="replay" onKeyDown={onKey}>
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <h1 className="text-[20px] font-semibold">Replay</h1>
        <p className="text-[13px] text-muted">Who did what, how long it took, and what it cost, over time.</p>
        <div className="ml-auto flex items-center gap-1" role="group" aria-label="Time window">
          {WINDOWS.map(([h, label]) => (
            <a
              key={h}
              href={href("replay", { hours: String(h) })}
              aria-current={h === hours ? "true" : undefined}
              className={cn("rounded-md px-2.5 py-1 text-[13px]", h === hours ? "bg-raised font-medium text-fg" : "text-muted hover:bg-raised/60 hover:text-fg")}
            >
              {label}
            </a>
          ))}
          <a
            href={href("replay", { hours: String(hours), t: String(Math.round(t)), speed: String(speed), table: table ? undefined : "1" })}
            className="ml-2 rounded-md border border-line px-2.5 py-1 text-[13px] hover:bg-raised/60"
            data-testid="table-toggle"
          >
            {table ? "Show lanes" : "Show as a table"}
          </a>
        </div>
      </header>
      <Controls
        t={t}
        ix={ix}
        live={live}
        onLive={() => {
          setPlaying(false);
          setLive(true);
        }}
        speed={speed}
        playing={playing}
        spanDays={spanDays}
        onToggle={toggle}
        onSpeed={(s) => {
          setSpeed(s);
          keep(t, s);
        }}
        onJump={go}
        onScrub={(at) => {
          setPlaying(false);
          setT(at);
        }}
      />
      {live && <Working working={p.working} />}
      <Summary frame={frame} shown={shown} active={active.length} lanes={frame.length} scope={scope} wait={p.wait} span={span} />
      {picked && <Products step={picked} at={t} onClose={() => setPicked(null)} />}
      {table ? <AtCursor frame={shown} ix={ix} onPick={setPicked} scope={scope} scopeHref={scopeHref} ledgerScope={ledgerScope} /> : <Lanes ix={ix} frame={frame} t={t} spanDays={spanDays} onScrub={(at) => { setPlaying(false); setT(at); }} />}
      {!table && <AtCursor frame={shown} ix={ix} compact onPick={setPicked} scope={scope} scopeHref={scopeHref} ledgerScope={ledgerScope} />}
      <WhereItWent bd={bd} hours={hours} wait={p.wait} onPick={setPicked} />
    </div>
  );
}

function isActive(ix: ReplayIndex, i: number): boolean {
  const L = ix.lanes[i];
  return L.ct.length > 1 || L.cs[0] >= 0 || L.rt.length > 0;
}

function Controls(p: {
  t: number;
  ix: ReplayIndex;
  live: boolean;
  onLive: () => void;
  speed: number;
  playing: boolean;
  spanDays: boolean;
  onToggle: () => void;
  onSpeed: (s: number) => void;
  onJump: (d: 1 | -1) => void;
  onScrub: (t: number) => void;
}) {
  const after = p.t > p.ix.known;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-ui border border-line bg-surface px-3 py-2" data-testid="controls">
      <button type="button" onClick={() => p.onJump(-1)} className="grid h-8 w-8 place-items-center rounded-md hover:bg-raised" aria-label="Previous change" title="Previous change (Shift+Left)" data-testid="prev">
        <SkipBack className="h-4 w-4" aria-hidden />
      </button>
      <button
        type="button"
        onClick={p.onToggle}
        className="inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-3 text-[13px] font-medium text-bg hover:opacity-90"
        aria-label={p.playing ? "Pause" : "Play"}
        title="Play or pause (Space)"
        data-testid="play"
        data-playing={p.playing ? "1" : undefined}
      >
        {p.playing ? <Pause className="h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
        {p.playing ? "Pause" : "Play"}
      </button>
      <button type="button" onClick={() => p.onJump(1)} className="grid h-8 w-8 place-items-center rounded-md hover:bg-raised" aria-label="Next change" title="Next change (Shift+Right)" data-testid="next">
        <SkipForward className="h-4 w-4" aria-hidden />
      </button>
      <div className="flex items-center gap-0.5" role="group" aria-label="Speed">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => p.onSpeed(s)}
            aria-pressed={s === p.speed}
            data-testid={`speed-${s}`}
            className={cn("rounded-md px-2 py-1 text-[12.5px] tabular-nums", s === p.speed ? "bg-raised font-semibold text-fg" : "text-muted hover:bg-raised/60 hover:text-fg")}
          >
            {s}×
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={p.onLive}
        aria-pressed={p.live}
        data-testid="live-toggle"
        title={p.live ? "Following now: the window moves with the clock and is read again every 15 s" : "Back to now, and follow it"}
        className={cn("inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[12.5px]", p.live ? "bg-state-done/15 font-medium text-fg" : "text-muted hover:bg-raised/60 hover:text-fg")}
      >
        <span className={cn("h-2 w-2 rounded-full", p.live ? "bg-state-done" : "bg-state-void")} aria-hidden />
        {p.live ? "Live" : "Go live"}
      </button>
      <span className="font-mono text-[13px] tabular-nums" data-testid="cursor-time" title="The moment the lanes and the numbers show">
        {stamp(p.t, p.spanDays, true)}
      </span>
      {after && <span className="text-[12px] text-muted">after now: nothing is known yet</span>}
      <input
        type="range"
        min={p.ix.from}
        max={p.ix.to}
        step={1000}
        value={p.t}
        onChange={(e) => p.onScrub(Number(e.target.value))}
        aria-label="Moment in the window"
        aria-valuetext={stamp(p.t, true, true)}
        className="min-w-[160px] flex-1 accent-[hsl(var(--accent))]"
        data-testid="scrubber"
      />
    </div>
  );
}

function Summary(p: {
  frame: LaneFrame[];
  shown: LaneFrame[];
  active: number;
  lanes: number;
  scope: string;
  wait: ReplayWindow["your_wait"] | null;
  span: string;
}) {
  const { frame, shown } = p;
  // the glance trial's first question, answered without adding anything up: who did most, and what it cost
  const top = [...shown].sort((a, b) => b.time_s.working - a.time_s.working || b.tokens - a.tokens)[0];
  const tok = shown.reduce((s, f) => s + f.tokens, 0);
  const cache = shown.reduce((s, f) => s + f.cache_read, 0);
  const now = KINDS.map((k, ki) => [k, frame.filter((f) => f.state === ki).length] as const).filter(([, n]) => n);
  return (
    <div className="mt-3 space-y-1 text-[14px] leading-snug" data-testid="summary">
      <p className="text-[12.5px] font-medium text-muted" data-testid="scope">
        {p.scope}
      </p>
      <p data-testid="most-work">
        {top && top.time_s.working >= 1 ? (
          <>
            <span className="font-semibold">Most work: {top.name}</span>, {duration(top.time_s.working)} working
            {top.tokens ? <>, {tokens(top.tokens)} tokens</> : null}.
            <span className="text-muted"> The team: {tokens(tok)} tokens, {tokens(cache)} more read from the cache.</span>
          </>
        ) : (
          <span className="text-muted">No lane worked in this span. The team: {tokens(tok)} tokens.</span>
        )}
      </p>
      <p className="text-muted">
        At the cursor: <span className="text-fg">{now.length ? now.map(([k, n]) => `${plural(n, "lane")} ${KIND_LABEL[k].toLowerCase()}`).join(", ") : "no lane busy"}</span>, of{" "}
        {plural(p.active, "lane")} with activity ({p.lanes} in all). A lane is a role.
        {p.wait && (
          <>
            {" "}
            <span className="text-fg">{p.wait.count} waiting on you now</span>; something waited on you {Math.round(p.wait.share * 100)}% of the
            whole {p.span}.
          </>
        )}
      </p>
    </div>
  );
}

function Lanes({ ix, frame, t, spanDays, onScrub }: { ix: ReplayIndex; frame: LaneFrame[]; t: number; spanDays: boolean; onScrub: (t: number) => void }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const narrow = width > 0 && width < 640;
  const gutter = narrow ? 92 : 150;
  const right = narrow ? 8 : 16;
  const plot = Math.max(0, width - gutter - right);
  // who worked comes first: lanes in order of their working time in the window, the ones that only waited after
  const order = useMemo(() => {
    const worked = (i: number) => {
      const L = ix.lanes[i];
      const at = (t: number) => {
        const k = lastAtOrBeforeIdx(L.ct, t);
        return k < 0 ? 0 : L.cum[0][k] + (L.cs[k] === 0 ? (t - L.ct[k]) / 1000 : 0);
      };
      return at(ix.known) - at(ix.from);
    };
    return ix.lanes
      .map((_, i) => i)
      .filter((i) => isActive(ix, i))
      .sort((a, b) => worked(b) - worked(a) || ix.lanes[a].name.localeCompare(ix.lanes[b].name));
  }, [ix]);
  const idle = ix.lanes.filter((_, i) => !isActive(ix, i)).map((L) => L.name);
  const cols = useMemo(
    () => (plot > 0 ? order.map((i) => columns(ix.lanes[i], ix.from, ix.to, Math.floor(plot), ix.known)) : []),
    [ix, order, plot],
  );
  const x = (ms: number) => gutter + ((ms - ix.from) / (ix.to - ix.from)) * plot;
  const AXIS = 22;
  const ROW = narrow ? 34 : 30;
  const height = AXIS + order.length * ROW + (idle.length ? 44 : 0) + 8;
  const tickMs = (() => {
    const span = ix.to - ix.from;
    const want = narrow ? 4 : 8;
    const steps = [60_000, 300_000, 900_000, 1_800_000, 3_600_000, 3 * 3_600_000, 6 * 3_600_000, 12 * 3_600_000, 86_400_000];
    return steps.find((s) => span / s <= want) ?? 86_400_000;
  })();
  const ticks: number[] = [];
  for (let m = Math.ceil(ix.from / tickMs) * tickMs; m <= ix.to; m += tickMs) ticks.push(m);
  const drag = useRef(false);
  const scrubAt = (e: React.PointerEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - box.left;
    if (px < gutter) return;
    onScrub(Math.max(ix.from, Math.min(ix.to, ix.from + ((px - gutter) / plot) * (ix.to - ix.from))));
  };

  return (
    <div ref={ref} className="relative mt-4 select-none rounded-ui border border-line bg-surface" data-testid="lanes">
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={`Replay of ${plural(order.length, "lane")}; the table view lists the same numbers at the cursor`}
          onPointerDown={(e) => {
            drag.current = true;
            e.currentTarget.setPointerCapture(e.pointerId);
            scrubAt(e);
          }}
          onPointerMove={(e) => drag.current && scrubAt(e)}
          onPointerUp={() => (drag.current = false)}
          className="cursor-col-resize"
        >
          {ticks.map((m) => (
            <g key={m}>
              <line x1={x(m)} x2={x(m)} y1={AXIS - 4} y2={height - 6} className="stroke-line" strokeWidth={1} />
              <text x={x(m) > width - 48 ? x(m) - 3 : x(m) + 3} y={AXIS - 8} textAnchor={x(m) > width - 48 ? "end" : "start"} className="fill-muted text-[11px]">
                {stamp(m, spanDays && new Date(m).getHours() === 0)}
              </text>
            </g>
          ))}
          {ix.known < ix.to && (
            <rect x={x(ix.known)} y={AXIS - 4} width={x(ix.to) - x(ix.known)} height={height - AXIS} className="fill-raised" fillOpacity={0.6} data-testid="after-now" />
          )}
          {order.map((li, row) => {
            const L = ix.lanes[li];
            const f = frame[li];
            const y = AXIS + row * ROW;
            const mid = y + ROW / 2;
            const runs: { x0: number; x1: number; k: number }[] = [];
            cols[row]?.forEach((c, cx) => {
              if (c.kind < 0) return;
              const last = runs[runs.length - 1];
              if (last && last.k === c.kind && last.x1 === cx) last.x1 = cx + 1;
              else runs.push({ x0: cx, x1: cx + 1, k: c.kind });
            });
            const maxReq = Math.max(1, ...(cols[row] ?? []).map((c) => c.requests));
            return (
              <g key={L.name} data-testid="lane" data-lane={L.name} data-state={f.state >= 0 ? KINDS[f.state] : "idle"}>
                {row > 0 && <line x1={0} x2={width} y1={y} y2={y} className="stroke-line" strokeWidth={1} strokeOpacity={0.6} />}
                <text x={12} y={mid + 4} className="fill-fg text-[13px] font-medium">
                  {L.name}
                </text>
                <line x1={gutter} x2={gutter + plot} y1={mid} y2={mid} className="stroke-line" strokeWidth={1} />
                {runs.map((r, i) => (
                  <rect key={i} x={gutter + r.x0} y={mid - 6} width={r.x1 - r.x0} height={12} className={KIND_FILL[KINDS[r.k]]} fillOpacity={KIND_WEIGHT[KINDS[r.k]]} data-kind={KINDS[r.k]} />
                ))}
                {(cols[row] ?? []).map((c, cx) =>
                  c.requests ? (
                    <rect key={`q${cx}`} x={gutter + cx} y={mid + 7} width={1} height={Math.max(1.5, (c.requests / maxReq) * 5)} className="fill-fg" fillOpacity={0.55} />
                  ) : null,
                )}
              </g>
            );
          })}
          {idle.length > 0 && (
            <text x={12} y={AXIS + order.length * ROW + 18} className="fill-muted text-[12.5px]" data-testid="idle-lanes">
              <tspan className="fill-fg font-medium">{plural(idle.length, "lane")} with nothing in this window:</tspan>
              <tspan x={12} dy={17}>
                {idle.join(", ")}
              </tspan>
            </text>
          )}
          <line x1={x(t)} x2={x(t)} y1={AXIS - 6} y2={height - 4} className="stroke-accent" strokeWidth={2} data-testid="cursor" />
        </svg>
      )}
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-3 py-2 text-[12px] text-muted" data-testid="legend">
        {KINDS.map((k) => (
          <span key={k} className="inline-flex items-center gap-1.5" title={KIND_HELP[k]}>
            <span className={cn("inline-block h-2.5 w-4 shrink-0 rounded-sm", KIND_DOT[k], KIND_SWATCH[k])} aria-hidden />
            <span>
              <span className="text-fg">{KIND_LABEL[k]}:</span> {KIND_HELP[k]}
            </span>
          </span>
        ))}
        <span>Ticks under a lane are model requests. Drag on the lanes to move the cursor.</span>
      </p>
    </div>
  );
}

function AtCursor(p: {
  frame: LaneFrame[];
  ix: ReplayIndex;
  compact?: boolean;
  onPick: (s: string) => void;
  scope: string;
  scopeHref: string;
  ledgerScope: boolean;
}) {
  const { frame, ix, compact = false, onPick } = p;
  const rows = frame
    .map((f, i) => ({ f, i }))
    .filter(({ i }) => !compact || isActive(ix, i))
    .sort((a, b) => b.f.time_s.working - a.f.time_s.working || b.f.tokens - a.f.tokens || a.f.name.localeCompare(b.f.name));
  return (
    <div className="mt-4 overflow-x-auto rounded-ui border border-line bg-surface" data-testid={compact ? "at-cursor" : "replay-table"}>
      <table className="w-full text-left text-[13px]">
        <caption className="px-3 pt-2 text-left text-[12px] text-muted" data-testid="table-scope">
          {p.scope}: time in each state, and tokens. Most work first.{" "}
          <a href={p.scopeHref} className="text-accent hover:underline" data-testid="scope-toggle">
            {p.ledgerScope ? "Show this window only" : "Show the ledger's totals"}
          </a>
        </caption>
        <thead className="text-[12px] text-muted">
          <tr className="border-b border-line">
            <th className="px-3 py-2 font-medium">Lane</th>
            <th className="px-3 py-2 font-medium">At the cursor</th>
            {KINDS.map((k) => (
              <th key={k} className="px-3 py-2 text-right font-medium" title={KIND_HELP[k]}>
                {KIND_LABEL[k]}
              </th>
            ))}
            <th className="px-3 py-2 text-right font-medium" title="Input, output and cache creation; cache reads apart">
              Tokens
            </th>
            <th className="px-3 py-2 text-right font-medium">Cache reads</th>
            <th className="px-3 py-2 font-medium" title="The step recorded on the session of the lane's latest request">
              Step
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ f }) => (
            <tr key={f.name} className="border-b border-line last:border-0" data-testid="cursor-row" data-lane={f.name}>
              <td className="px-3 py-1.5 font-medium">{f.name}</td>
              <td className="px-3 py-1.5">
                {f.state >= 0 ? (
                  <span className="inline-flex items-center gap-1.5">
                    <span className={cn("h-2 w-2 shrink-0 rounded-full", KIND_DOT[KINDS[f.state]])} aria-hidden />
                    {KIND_LABEL[KINDS[f.state]]}
                  </span>
                ) : (
                  <span className="text-muted">idle</span>
                )}
              </td>
              {KINDS.map((k) => (
                <td key={k} className="px-3 py-1.5 text-right tabular-nums">
                  {f.time_s[k] >= 1 ? duration(f.time_s[k]) : <span className="text-muted">—</span>}
                </td>
              ))}
              <td className="px-3 py-1.5 text-right tabular-nums">{f.tokens ? tokens(f.tokens) : <span className="text-muted">—</span>}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-muted">{f.cache_read ? tokens(f.cache_read) : "—"}</td>
              <td className="px-3 py-1.5 font-mono text-[12px]">
                {f.step >= 0 ? (
                  <button type="button" className="text-accent hover:underline" onClick={() => onPick(ix.steps[f.step])} title="What this step produced, as of the cursor">
                    {ix.steps[f.step].replace(/^step:/, "")}
                  </button>
                ) : f.requests ? (
                  <span className="font-sans text-muted">between steps</span>
                ) : (
                  ""
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function WhereItWent({ bd, hours, wait, onPick }: { bd: LaneBreakdown[]; hours: number; wait: ReplayWindow["your_wait"] | null; onPick: (s: string) => void }) {
  const lanes = bd
    .filter((L) => L.tokens > 0 || KINDS.some((k) => L.time_s[k] >= 1))
    .sort((a, b) => b.time_s.working - a.time_s.working || b.tokens - a.tokens || a.name.localeCompare(b.name));
  const busiest = Math.max(1, ...lanes.map((L) => KINDS.reduce((s, k) => s + L.time_s[k], 0)));
  const span = hours >= 24 ? `${hours / 24} day${hours > 24 ? "s" : ""}` : `${hours} h`;
  return (
    <section className="mt-4 rounded-ui border border-line bg-surface" data-testid="breakdown" aria-label="Where the time went">
      <header className="flex flex-wrap items-baseline gap-x-3 border-b border-line px-3 py-2">
        <h2 className="text-[15px] font-semibold">Where the time went</h2>
        <p className="text-[12px] text-muted">
          In the whole window ({span}), not at the cursor. Tokens are input, output and cache creation; cache reads are apart. Click a step to see what it produced.
        </p>
      </header>
      {wait && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3 py-2.5" data-testid="your-wait-bar">
          <span className="w-28 shrink-0 text-[13px] font-medium">You</span>
          <span className="flex h-3 min-w-[120px] flex-1 overflow-hidden rounded-sm bg-raised" aria-hidden>
            <span className="h-full bg-state-human" style={{ width: `${Math.round(wait.share * 100)}%` }} />
          </span>
          <span className="text-[12.5px]">
            <span className="font-medium">something waited on you {Math.round(wait.share * 100)}% of the window</span>
            <span className="text-muted">
              {" "}
              ({duration(wait.waited_s)}) · {wait.count} waiting now{wait.oldest_s != null && wait.count > 0 ? `, the oldest for ${duration(wait.oldest_s)}` : ""}
            </span>
          </span>
        </div>
      )}
      {lanes.length === 0 ? (
        <p className="px-3 py-3 text-[13px] text-muted">Nothing in this window.</p>
      ) : (
        <ul className="divide-y divide-line">
          {lanes.map((L) => {
            const total = KINDS.reduce((s, k) => s + L.time_s[k], 0);
            const share = L.tokens ? Math.round((100 * L.between.tokens) / L.tokens) : 0;
            return (
              <li key={L.name} className="px-3 py-2.5" data-testid="breakdown-lane" data-lane={L.name}>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className="w-28 shrink-0 text-[13px] font-medium">{L.name}</span>
                  <span className="flex h-3 min-w-[120px] flex-1 overflow-hidden rounded-sm bg-raised" style={{ maxWidth: `${Math.max(4, (100 * total) / busiest)}%` }} aria-hidden>
                    {KINDS.map((k) =>
                      L.time_s[k] > 0 ? (
                        <span key={k} className={cn("h-full", KIND_DOT[k], KIND_SWATCH[k])} style={{ width: `${(100 * L.time_s[k]) / Math.max(total, 1e-9)}%` }} title={`${KIND_LABEL[k]}: ${duration(L.time_s[k])}`} data-kind={k} />
                      ) : null,
                    )}
                  </span>
                  <span className="text-[12.5px] text-muted">
                    {KINDS.filter((k) => L.time_s[k] >= 1)
                      .map((k) => `${KIND_LABEL[k].toLowerCase()} ${duration(L.time_s[k])}`)
                      .join(" · ") || "no time in a state"}
                  </span>
                  <span className="ml-auto text-[12.5px] tabular-nums">
                    {L.tokens ? (
                      <>
                        <span className="font-medium">{tokens(L.tokens)} tokens</span>
                        <span className="text-muted"> · {tokens(L.cache_read)} cache reads</span>
                      </>
                    ) : (
                      <span className="text-muted">no model requests</span>
                    )}
                  </span>
                </div>
                {L.tokens > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5 pl-[7.75rem] max-sm:pl-0">
                    {L.steps.map((c) => (
                      <span key={`${c.step}|${c.by}`} className="inline-flex items-center gap-1.5 rounded-md border border-line px-2 py-0.5 text-[12px]" data-testid="step-cost" data-by={c.by}>
                        <button type="button" className="font-mono text-accent hover:underline" onClick={() => onPick(c.step)} title="What this step produced, as of the cursor">
                          {c.step.replace(/^step:/, "")}
                        </button>
                        <span className="tabular-nums">{tokens(c.tokens)}</span>
                        <span className={cn("rounded px-1 text-[11px]", c.by === "rule" ? "bg-state-human/15 text-fg" : "bg-raised text-muted")} title={c.by === "rule" ? "No step was recorded on the session; the next commit on its branch carries this node" : "onloop recorded this step when it started the work"}>
                          {c.by === "rule" ? "attributed by rule" : "recorded"}
                        </span>
                      </span>
                    ))}
                    {L.between.requests > 0 && (
                      <span className="inline-flex items-center gap-1.5 rounded-md border border-dashed border-line px-2 py-0.5 text-[12px] text-muted" data-testid="between-steps" title="Requests no step was recorded for and no rule placed: never charged to the nearest step">
                        between steps <span className="tabular-nums text-fg">{tokens(L.between.tokens)}</span> ({share}% of the lane's tokens)
                      </span>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function Working({ working }: { working: WorkingNow[] }) {
  const nowMs = useNowMs();
  return (
    <section className="mt-3 rounded-ui border border-l-[3px] border-line border-l-state-done bg-surface px-3 py-2" data-testid="working-now" aria-label="Working now">
      <h2 className="text-[14px] font-semibold">Working now</h2>
      {working.length === 0 ? (
        <p className="mt-0.5 text-[13px] text-muted">No step onloop recorded is open now. Work without a recorded step shows in the lanes, not here.</p>
      ) : (
        <ul className="mt-1 space-y-0.5 text-[13px]">
          {working.map((w) => (
            <li key={`${w.lane}|${w.step}`} className="flex flex-wrap items-baseline gap-x-2" data-testid="working-row" data-lane={w.lane}>
              <span className="font-medium">{w.lane}</span>
              <span className="font-mono text-[12.5px]">{w.step.replace(/^step:/, "")}</span>
              <span className="text-muted">for {duration((nowMs - w.since_ms) / 1000)}</span>
              <span className="tabular-nums">
                {tokens(w.tokens)} tokens so far
                <span className="text-muted">
                  {" "}
                  · {tokens(w.cache_read)} cache reads · {plural(w.requests, "model request")}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

interface ProductFile {
  repo: number;
  path: string;
  sha: string;
  t_ms: number;
  type: string;
  deleted: boolean;
}
interface ProductsModel {
  step: string;
  at: string;
  repos: string[];
  commits: { sha: string; t_ms: number; repo: number }[];
  files: ProductFile[];
}

const fileUrl = (f: ProductFile) => `/api/products/file?repo=${f.repo}&sha=${f.sha}&path=${encodeURIComponent(f.path)}`;

// What a step produced, as of the cursor (decision 0019, a4): the files its commits changed, each at its latest commit
// then. HTML shows in a sandboxed frame (and onloopd serves it with its own sandbox), Markdown as text formatting
// only, images as images, anything else as text.
function Products({ step, at, onClose }: { step: string; at: number; onClose: () => void }) {
  const minute = Math.floor(at / 60_000) * 60_000;
  const kindOk = /^step:(node|branch):/.test(step);
  const q = useQuery({
    queryKey: ["products", step, minute],
    queryFn: () => get<ProductsModel>(`/api/products?step=${encodeURIComponent(step)}&at=${encodeURIComponent(new Date(minute + 59_999).toISOString())}`),
    enabled: kindOk,
  });
  const [open, setOpen] = useState<string | null>(null);
  const files = q.data?.files.filter((f) => !f.deleted) ?? [];
  const shown = files.find((f) => `${f.repo}|${f.path}` === open) ?? null;
  return (
    <section className="mt-4 rounded-ui border border-l-[3px] border-line border-l-accent bg-surface" data-testid="products" aria-label={`Products of ${step}`}>
      <header className="flex flex-wrap items-baseline gap-x-3 border-b border-line px-3 py-2">
        <h2 className="text-[15px] font-semibold">
          What <span className="font-mono text-[14px]">{step.replace(/^step:/, "")}</span> produced
        </h2>
        <p className="text-[12px] text-muted">as of the cursor ({stamp(at, true)}): each file at its latest commit then</p>
        <button type="button" onClick={onClose} className="ml-auto rounded-md px-2 py-0.5 text-[12.5px] text-muted hover:bg-raised hover:text-fg" data-testid="products-close">
          Close
        </button>
      </header>
      {!kindOk ? (
        <p className="px-3 py-3 text-[13px] text-muted">Products are read for a node or a branch step, from the commits that carry it. A task or a PR step has none here yet.</p>
      ) : q.isPending ? (
        <div className="px-3 py-3">
          <Loading what="the products" />
        </div>
      ) : q.isError ? (
        <div className="px-3 py-3">
          <Problem error={q.error} />
        </div>
      ) : files.length === 0 ? (
        <p className="px-3 py-3 text-[13px] text-muted" data-testid="products-none">
          No commit of this step at or before the cursor{q.data.commits.length ? "" : ", in the repositories onloop recorded"}.
        </p>
      ) : (
        <div className="grid gap-0 md:grid-cols-[minmax(220px,1fr)_3fr]">
          <ul className="border-b border-line py-1 md:border-b-0 md:border-r" data-testid="product-files">
            {files.map((f) => {
              const key = `${f.repo}|${f.path}`;
              return (
                <li key={key}>
                  <button
                    type="button"
                    onClick={() => setOpen(key)}
                    aria-pressed={open === key}
                    className={cn("block w-full truncate px-3 py-1 text-left font-mono text-[12.5px]", open === key ? "bg-accent/10 text-accent" : "hover:bg-raised")}
                    title={`${q.data.repos[f.repo]}: ${f.path} at ${f.sha.slice(0, 8)}, ${stamp(f.t_ms, true)}`}
                    data-testid="product-file"
                  >
                    {f.path}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="min-h-[160px] p-3" data-testid="product-view">
            {shown ? <Preview f={shown} /> : <p className="text-[13px] text-muted">Choose a file to see it as it was at the cursor.</p>}
          </div>
        </div>
      )}
    </section>
  );
}

function Preview({ f }: { f: ProductFile }) {
  const url = fileUrl(f);
  const textual = !f.type.startsWith("image/") && !f.type.startsWith("text/html");
  const q = useQuery({ queryKey: ["product-file", url], queryFn: () => fetch(url).then((r) => (r.ok ? r.text() : Promise.reject(new Error(`onloopd answered ${r.status}`)))), enabled: textual });
  if (f.type.startsWith("text/html")) {
    // an empty sandbox: no scripts, no forms, an opaque origin; onloopd's answer carries its own sandbox too
    return (
      <div className="space-y-1.5">
        <iframe sandbox="" src={url} title={f.path} className="h-[460px] w-full rounded border border-line bg-white" data-testid="product-html" />
        <p className="text-[12px] text-muted">
          Shown in a sandbox: no script runs, and it never has onloop's origin.{" "}
          <a href={url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline" data-testid="product-open">
            Open it on its own
          </a>{" "}
          (still sandboxed).
        </p>
      </div>
    );
  }
  if (f.type.startsWith("image/")) return <img src={url} alt={f.path} className="max-h-[460px] max-w-full" data-testid="product-image" />;
  if (q.isPending) return <Loading what={f.path} />;
  if (q.isError) return <Problem error={q.error} />;
  if (f.type.startsWith("text/markdown")) return <Md text={q.data} />;
  return (
    <pre className="max-h-[460px] overflow-auto whitespace-pre-wrap rounded bg-raised p-2 font-mono text-[12px]" data-testid="product-text">
      {q.data}
    </pre>
  );
}

function Md({ text }: { text: string }) {
  return (
    <div className="max-h-[460px] space-y-2 overflow-auto text-[13.5px] leading-relaxed" data-testid="product-markdown">
      {parseMd(text).map((b, i) => (
        <MdBlock key={i} b={b} />
      ))}
    </div>
  );
}

function MdBlock({ b }: { b: Block }) {
  if (b.t === "h") {
    const size = ["text-[18px]", "text-[16px]", "text-[15px]", "text-[14px]", "text-[13.5px]", "text-[13.5px]"][b.level - 1];
    return (
      <p role="heading" aria-level={b.level} className={cn("font-semibold", size)}>
        <MdInline c={b.c} />
      </p>
    );
  }
  if (b.t === "p") return <p><MdInline c={b.c} /></p>;
  if (b.t === "quote") return <blockquote className="border-l-2 border-line pl-3 text-muted"><MdInline c={b.c} /></blockquote>;
  if (b.t === "pre") return <pre className="overflow-auto rounded bg-raised p-2 font-mono text-[12px]">{b.v}</pre>;
  const L = b.t === "ul" ? "ul" : "ol";
  return (
    <L className={cn("pl-5", L === "ul" ? "list-disc" : "list-decimal")}>
      {b.items.map((it, i) => (
        <li key={i}>
          <MdInline c={it} />
        </li>
      ))}
    </L>
  );
}

function MdInline({ c }: { c: Inline[] }) {
  return (
    <>
      {c.map((n, i) =>
        n.t === "text" ? (
          <span key={i}>{n.v}</span>
        ) : n.t === "code" ? (
          <code key={i} className="rounded bg-raised px-1 font-mono text-[12px]">{n.v}</code>
        ) : n.t === "strong" ? (
          <strong key={i}><MdInline c={n.c} /></strong>
        ) : n.t === "em" ? (
          <em key={i}><MdInline c={n.c} /></em>
        ) : (
          <a key={i} href={n.href} target="_blank" rel="noreferrer noopener" className="text-accent hover:underline">
            <MdInline c={n.c} />
          </a>
        ),
      )}
    </>
  );
}
