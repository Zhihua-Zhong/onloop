import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useStore,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { get, type DigestModel } from "@/lib/api";
import { downMessage, refetchEvery, viewState } from "@/lib/recovery";
import { useTicking } from "@/lib/clock";
import { clock, duration, plural } from "@/lib/format";
import { href } from "@/lib/route";
import {
  BLOCK_H,
  cardLayout,
  chainLinks,
  isReserved,
  nextPath,
  opener,
  opensOn,
  phoneOrder,
  showsOpenHint,
  thresholdsText,
  TONE_OF,
  WIDTH_CHECK_MS,
  type MapBlock,
  type MapHolder,
  type MapLevelBase,
  type MapModel,
  type MapStageEntry,
  type MapWait,
  type Zoom1Model,
  type Zoom2Model,
  barShare,
  IDLE,
  stageOfColumn,
  type StageGrid,
} from "@/lib/map";
import { cn } from "@/lib/utils";
import { Empty, Loading, Problem, tone } from "@/components/kit";

// The map (decision 0018), zoom 0: where is it jammed, and is it me? One block per goal, grouped by origin, never by
// role; a block's colour is the worst of its children, with a shape, and a red or amber block says which child and
// why. Geometric zoom never changes the level: a person opens a block on purpose (double-click, Enter, its title).

const PHONE = "(max-width: 767px)";

function usePhone(): boolean {
  const [phone, setPhone] = useState(() => window.matchMedia?.(PHONE).matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.(PHONE);
    if (!mq) return;
    const on = (e: MediaQueryListEvent) => setPhone(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return phone;
}

function go(path: string[], table: boolean) {
  window.location.hash = href("map", { table: table ? "1" : undefined }, path);
}

// Going in: a click, a double-click, Enter, Space or Open, all through the one guarded opener.
function goIn(path: string[], table: boolean): () => void {
  return () => {
    opener(() => go(path, table));
  };
}

export default function MapView({ params, path }: { params: URLSearchParams; path: string[] }) {
  const table = params.get("table") === "1"; // in the URL, so back, reload and a shared link keep it
  const reserved = isReserved(path);
  const key = path.join("/");
  const q = useQuery({
    queryKey: ["map", key],
    queryFn: () => get<MapLevelBase>(`/api/map${key ? `?path=${encodeURIComponent(key)}` : ""}`),
    refetchInterval: refetchEvery(30_000),
    enabled: !reserved,
  });
  const ticked = useTicking(q.data, q.dataUpdatedAt); // its ages rise every second between answers (lib/tick.ts)
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const up = nextPath(path, { kind: "up" });
      if (up.length !== path.length) go(up, table);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [path, table]);
  if (reserved) return <Reserved path={path} table={table} />;
  const state = viewState(q.status, q.data !== undefined);
  if (state === "loading") return <div className="p-4"><Loading what="the map" /></div>;
  if (state === "down") return <div className="p-4"><Problem error={new Error(downMessage(q.error))} /></div>;
  const m = ticked as MapLevelBase;
  const stale = state === "data-stale" && (
    <p role="status" data-testid="map-stale" className="border-b border-state-human/40 bg-state-human/10 px-4 py-1.5 text-[13px]">
      {downMessage(q.error)} This is the map as last read.
    </p>
  );
  const level =
    m.level === 1 ? <Zoom1 m={m as Zoom1Model} table={table} /> : m.level === 2 ? <Zoom2 m={m as Zoom2Model} table={table} who={params.get("who")} /> : <Zoom0 m={m as MapModel} table={table} />;
  return (
    <>
      {stale}
      <div key={key} className="map-enter flex h-full min-h-0 flex-col" data-testid="map-enter">
        {level}
      </div>
    </>
  );
}

function Breadcrumb({ path, names, table }: { path: string[]; names: string[]; table: boolean }) {
  return (
    <nav aria-label="Where you are on the map" data-testid="breadcrumb" className="flex min-w-0 items-center gap-1 text-sm">
      {path.length ? (
        <a href={href("map", { table: table ? "1" : undefined })} className="font-medium text-accent hover:underline">All goals</a>
      ) : (
        <span className="font-semibold" aria-current="page">All goals</span>
      )}
      {names.map((n, i) => (
        <span key={i} className="flex min-w-0 items-center gap-1">
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
          {i < names.length - 1 ? (
            <a href={href("map", { table: table ? "1" : undefined }, path.slice(0, i + 1))} className="truncate font-medium text-accent hover:underline">
              {n}
            </a>
          ) : (
            <span className="inline-block truncate font-semibold first-letter:uppercase" aria-current="page">{n}</span>
          )}
        </span>
      ))}
    </nav>
  );
}

function Reserved({ path, table }: { path: string[]; table: boolean }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4" data-testid="map-level" data-level={path.length}>
      <Breadcrumb path={path} names={path} table={table} />
      <Empty testid="reserved">
        {path.length === 3 ? "A role's turns, one level in," : "A turn's tool calls"} come with the tool events. Press
        Escape, or use the links above, to go back.
      </Empty>
    </div>
  );
}

function Signals({ m }: { m: MapLevelBase }) {
  const s = m.signals;
  const d = useQuery({ queryKey: ["digest"], queryFn: () => get<DigestModel>("/api/digest"), refetchInterval: 60_000 });
  const lw = s.team_quiet;
  return (
    <section aria-label="Signals" data-testid="signals" className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
        <span data-testid="sig-you" className={cn("text-base font-semibold", s.waiting_on_you ? tone("human").text : "text-muted")}>
          <span aria-hidden>◆ </span>
          {s.waiting_on_you} {s.waiting_on_you === 1 ? "waits" : "wait"} on you
        </span>
        <span data-testid="sig-stuck" className={cn("text-base font-semibold", s.stuck ? tone("blocked").text : "text-muted")}>
          <span aria-hidden>! </span>
          {s.stuck} jammed
        </span>
        {(s.idle ?? 0) > 0 && (
          <span data-testid="sig-idle" className="text-sm text-muted" title="Open changes with no move for a week or more: parked or abandoned, so never counted as jammed">
            {s.idle} idle
          </span>
        )}
        <span data-testid="sig-quiet" className="text-sm text-fg" title="The longest stretch in the last day when no role had a turn running or ending">
          Team quiet at most: <span className="font-semibold">{lw ? `${lw.text} in the last ${lw.team_activity.hours} h` : "no turns in the last day"}</span>
          {lw?.ongoing && <span className="text-muted"> (still quiet)</span>}
        </span>
      </div>
      <p className="text-[13px] text-muted" data-testid="since">
        {d.data ? sinceSentence(d.data) : " "}
        {lw && (
          <>
            {" "}
            Team activity, last {lw.team_activity.hours} h: {plural(lw.team_activity.turns, "turn")} by {plural(lw.team_activity.roles, "role")}.
          </>
        )}
        {m.data_as_of && <> Data as of {clock(m.data_as_of, m.now)}.</>}
      </p>
    </section>
  );
}

// The since-you-looked sentence, in a fixed order of importance (O-S4): tasks for you, items done, what closed
// without you, and the times onloop was not running.
function sinceSentence(d: DigestModel): string {
  const parts: string[] = [];
  if (d.added.length) parts.push(`${plural(d.added.length, "new task")} for you`);
  const done = d.ticks.filter((t) => t.kind === "tick").length;
  if (done) parts.push(`${plural(done, "item")} done`);
  if (d.resolved_without_you.length) parts.push(`${d.resolved_without_you.length} closed without you`);
  if (d.gaps.length) parts.push(`onloop was not running for ${duration(d.gaps.reduce((a, g) => a + g.seconds, 0))}`);
  const since = d.since ? `Since you looked, ${clock(d.since, d.now)}: ` : "Since onloop started: ";
  return since + (parts.length ? parts.join(" · ") : "nothing new") + ".";
}

function Bar({ b }: { b: MapBlock }) {
  if (b.bar.done == null) return null;
  const pct = b.bar.of ? (100 * b.bar.done) / b.bar.of : 0;
  return (
    <div className="mt-2" data-testid="bar">
      <div className="h-2 overflow-hidden rounded-full bg-raised ring-1 ring-inset ring-line" aria-hidden>
        <div className="h-full rounded-full bg-state-done" style={{ width: `${pct}%` }} />
      </div>
      <p className="mt-1 text-[13px] font-medium tabular-nums">{b.bar.label}</p>
    </div>
  );
}

// A tally line ("1 jammed · 1 waits on you": the generic ledger's Changes in flight), with its jammed part in the
// jam's red and set apart, so "most stuck" never reads as the amber that waits on you beside it (run 1's one
// difficulty).
function Tally({ text, testid = "tally" }: { text: string; testid?: string }) {
  const parts = text.split(" · ");
  const jam = /^\d+ jammed$/.test(parts[0] ?? "") ? parts.shift() : null;
  return (
    <p className="mt-1 text-[13px] text-muted" data-testid={testid}>
      {jam && <span className={cn("font-semibold", tone("blocked").text)} data-testid="tally-jam">{jam}</span>}
      {jam && parts.length > 0 && <span className="mx-1.5 text-line" aria-hidden>|</span>}
      {parts.join(" · ")}
    </p>
  );
}

function Cells({ b }: { b: MapBlock }) {
  if (!b.leaves.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-[3px]" aria-hidden data-testid="cells">
      {b.leaves.slice(0, 60).map((lf) => (
        <span
          key={lf.id}
          title={`${lf.title}: ${lf.colour === "done" ? "done" : lf.idle ? `idle, ${lf.why ?? ""}` : lf.why ?? b.label}`}
          className={cn(
            "grid h-4 w-4 place-items-center rounded-[3px] text-[9px] font-bold leading-none",
            lf.idle
              ? "idle-hatch"
              : lf.colour === "grey"
                ? "border border-state-void/70 text-state-void"
                : cn(tone(TONE_OF[lf.colour]).dot, "text-white"),
          )}
        >
          {lf.colour === "done" || lf.colour === "grey" || lf.idle ? "" : lf.shape}
        </span>
      ))}
      {b.leaves.length > 60 && <span className="text-xs text-muted">+{b.leaves.length - 60}</span>}
    </div>
  );
}

function Stages({ b }: { b: MapBlock }) {
  if (!b.stages) return null;
  return (
    <ul className="mt-2 grid grid-cols-3 gap-1.5" data-testid="stages">
      {b.stages.map((s) => (
        <li
          key={s.stage}
          data-reported={s.reported}
          className={cn(
            "rounded-md border px-2 py-1 text-[12.5px] leading-tight",
            s.reported ? "border-line bg-raised" : "map-hatch border-dashed border-state-void/60 text-muted",
          )}
        >
          <span className="block font-medium capitalize">{s.stage}</span>
          {s.reported ? (
            <span className={cn("tabular-nums", s.colour && s.colour !== "grey" ? tone(TONE_OF[s.colour]).text : "text-muted")}>
              {s.count}
              {s.colour && s.colour !== "grey" && s.count ? <span aria-hidden> {s.colour === "red" ? "!" : s.colour === "amber" ? "◆" : "▶"}</span> : null}
            </span>
          ) : (
            <span>not reported</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function BlockCard({ b, onOpen, hint = false, className }: { b: MapBlock; onOpen: () => void; hint?: boolean; className?: string }) {
  const t = TONE_OF[b.colour];
  const notReported = b.stages?.filter((s) => !s.reported).map((s) => s.stage) ?? [];
  return (
    <article
      data-testid="block"
      data-block={b.id}
      data-colour={b.colour}
      tabIndex={0}
      role="button"
      aria-label={`Open ${b.title}: ${b.label}. ${b.bar.label}. ${b.tally_text}.${b.reason ? ` ${b.reason.text}.` : ""}`}
      onKeyDown={(e) => {
        if (opensOn(e.key)) {
          e.preventDefault();
          onOpen();
        }
      }}
      onDoubleClick={onOpen}
      className={cn(
        "relative flex h-full min-w-0 cursor-pointer flex-col rounded-ui border border-l-[4px] border-line bg-surface px-3.5 py-3 shadow-sm outline-none focus-visible:ring-[3px] focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        b.label === "idle" ? "border-l-state-idle" : tone(t).edge,
        className,
      )}
    >
      <header className="flex min-w-0 items-start gap-2">
        <button type="button" tabIndex={-1} onClick={onOpen} className="min-w-0 flex-1 text-left text-[15px] font-semibold leading-snug hover:underline" data-testid="block-title">
          <span className="line-clamp-2">{b.title}</span>
        </button>
        <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold", b.label === "idle" ? "idle-hatch text-fg" : cn(tone(t).soft, tone(t).text))} data-testid="block-state">
          {b.label !== "idle" && <span aria-hidden>{b.shape}</span>}
          {b.label}
        </span>
      </header>
      {b.reported_by_team && <p className="mt-0.5 text-xs text-muted" data-testid="reported">Stages reported by the team</p>}
      <Bar b={b} />
      {b.kind === "changes" && <p className="mt-1 text-[13px] font-medium tabular-nums">{b.bar.label}</p>}
      <Tally text={b.tally_text} />
      {b.reason && (
        <p className={cn("mt-1.5 text-[13px] font-medium leading-snug", tone(t).text)} data-testid="reason">
          <span className="line-clamp-2">{b.reason.text}</span>
        </p>
      )}
      <Stages b={b} />
      {notReported.length > 0 && <p className="mt-1 text-xs text-muted">Not reported by this team's export: {notReported.join(", ")}.</p>}
      <Cells b={b} />
      {hint && (
        <button type="button" tabIndex={-1} onClick={onOpen} data-testid="open-hint" className="absolute bottom-2 right-2 inline-flex items-center gap-0.5 rounded-md bg-accent px-2 py-1 text-xs font-semibold text-white shadow">
          Open <ChevronRight className="h-3 w-3" aria-hidden />
        </button>
      )}
    </article>
  );
}

type CardData = {
  render: (hint: boolean, open?: () => void) => ReactNode;
  onOpen?: () => void;
  chain: boolean;
  next?: string; // where a row wraps: what comes next, instead of an edge across the canvas
};

const CardNode = memo(function CardNode({ data }: NodeProps<Node<CardData>>) {
  const zoom = useStore((s) => s.transform[2]);
  const open = data.onOpen;
  return (
    <>
      {data.chain && <Handle type="target" position={Position.Left} isConnectable={false} className="!opacity-0" />}
      {data.render(showsOpenHint(zoom) && !!open, open)}
      {data.next && (
        <span className="absolute -bottom-5 right-2 text-xs text-muted" data-testid="chain-next">
          then: {data.next} ↓
        </span>
      )}
      {data.chain && <Handle type="source" position={Position.Right} isConnectable={false} className="!opacity-0" />}
    </>
  );
});
const nodeTypes = { card: CardNode };

interface CanvasCard {
  id: string;
  height: number;
  label?: string;
  render: (hint: boolean, open?: () => void) => ReactNode;
  onOpen?: () => void;
}

function Waits({ waits }: { waits: MapWait[] }) {
  const max = Math.max(1, ...waits.map((w) => w.oldest_s));
  return (
    <section aria-label="Waits, by who holds them" data-testid="waits" className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">Waits, by who holds them</h2>
      {waits.length === 0 && <p className="text-[13px] text-muted">Nothing waits.</p>}
      <ol className="flex flex-col gap-2">
        {waits.map((w) => {
          const you = w.holder === "you";
          return (
            <li key={w.holder} data-testid="wait-row" data-holder={w.holder} className="min-w-0">
              <div className="flex items-baseline justify-between gap-2 text-[13px]">
                <span className={cn("truncate font-semibold", you && tone("human").text)}>{w.holder}</span>
                <span className="shrink-0 tabular-nums text-muted">
                  {plural(w.count, you ? "task" : "wait")}, oldest <span className="font-semibold text-fg">{w.age_text}</span>
                </span>
              </div>
              <div className="mt-0.5 h-2 overflow-hidden rounded-full bg-raised" aria-hidden>
                <div className={cn("h-full rounded-full", you ? "bg-state-human" : "bg-state-external")} style={{ width: `${Math.max(4, (100 * w.oldest_s) / max)}%` }} />
              </div>
              {w.oldest_text && (
                <p className="mt-0.5 truncate text-xs text-muted" title={w.oldest_text}>
                  {w.oldest_text}
                </p>
              )}
              {w.note && (
                <p className="text-xs font-medium text-fg" data-testid="lower-bound-note">
                  {w.note}
                </p>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Legend({ m }: { m: MapLevelBase }) {
  return (
    <section aria-label="Legend" data-testid="legend" className="flex flex-col gap-1.5 text-[12.5px]">
      <h2 className="text-sm font-semibold">Legend</h2>
      <ul className="flex flex-wrap gap-x-3 gap-y-1">
        {m.legend.map((l) => (
          <li key={l.colour} className="inline-flex items-center gap-1.5">
            <span className={cn("grid h-4 w-4 place-items-center rounded-[3px] text-[9px] font-bold text-white", l.colour === "grey" ? "border border-state-void/70" : tone(TONE_OF[l.colour]).dot)} aria-hidden>
              {l.colour === "grey" || l.colour === "done" ? "" : l.shape}
            </span>
            {l.label}
          </li>
        ))}
        <li className="inline-flex items-center gap-1.5" data-testid="legend-idle">
          <span className="idle-hatch h-4 w-4 rounded-[3px]" aria-hidden />
          idle: open, no move for a week
        </li>
      </ul>
      <p className="text-muted">A block takes its worst live child's colour; idle work is never counted as jammed. Jammed after: {thresholdsText(m.thresholds_s, duration)}.</p>
      <p className="text-muted">"≥" means the start is older than anything onloop has seen.</p>
    </section>
  );
}

function useWidthOf<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  // measured at once, then on every resize: a ResizeObserver reports only on a rendering step, and a page in a
  // background tab may not render one for a while (Activity's lanes do the same)
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setW(el.clientWidth); // the same width twice is no re-render
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    measure();
    // a widened window must re-lay the blocks even when no frame is drawn: then only a timer sees the new width
    const timer = window.setInterval(measure, WIDTH_CHECK_MS);
    return () => {
      ro.disconnect();
      window.clearInterval(timer);
    };
  }, []);
  return [ref, w];
}

// One canvas for every level: a fixed grid at scale 1 (never fitted smaller), wheel and pinch zoom, drag pan. With
// `chain`, each card leads to the next, as a goal's stages do.
function Canvas({ cards, chain = false }: { cards: CanvasCard[]; chain?: boolean }) {
  const [box, width] = useWidthOf<HTMLDivElement>();
  const heights = cards.map((c) => c.height);
  const grid = useMemo(() => cardLayout(heights, width), [heights.join(","), width]); // eslint-disable-line react-hooks/exhaustive-deps
  const links = useMemo(() => (chain ? chainLinks(grid.positions) : { edges: [], wraps: [] }), [chain, grid]);
  const edges = useMemo<Edge[]>(
    () => links.edges.map(([a, b]) => ({ id: `chain:${a}`, source: cards[a].id, target: cards[b].id, type: "smoothstep", focusable: false })),
    [cards, links],
  );
  const nodes = useMemo<Node<CardData>[]>(
    () =>
      cards.map((c, i) => ({
        id: c.id,
        type: "card",
        position: grid.positions[i],
        data: {
          render: c.render,
          onOpen: c.onOpen,
          chain,
          next: links.wraps.includes(i) ? cards[i + 1]?.label : undefined,
        },
        draggable: false,
        selectable: false,
        focusable: false, // the block itself is the one Tab stop, in reading order
        // React Flow turns pointer events off on a node that is neither draggable, selectable nor clickable; a block's
        // title, Open hint and double-click need them, and a node's own style wins
        // the node's own size: React Flow keeps a node it has not measured hidden, and a page that is not drawn (a
        // background tab) never measures, so its blocks could be neither clicked nor focused
        width: grid.blockW,
        height: c.height,
        style: { width: grid.blockW, height: c.height, pointerEvents: "all" },
      })),
    [cards, grid, chain, links],
  );
  return (
    <div
      ref={box}
      className="relative min-h-[420px] min-w-0 flex-1 overflow-hidden rounded-ui border border-line bg-bg"
      data-testid="map-canvas"
      data-zoom={grid.zoom}
      data-per-row={grid.perRow}
    >
      {width > 0 && (
      <ReactFlowProvider key={grid.perRow}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          defaultViewport={{ x: 0, y: 0, zoom: grid.zoom }}
          minZoom={0.3}
          maxZoom={2.5}
          zoomOnScroll
          zoomOnPinch
          panOnDrag
          zoomOnDoubleClick={false}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
          deleteKeyCode={null}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="hsl(var(--line))" />
        </ReactFlow>
      </ReactFlowProvider>
      )}
    </div>
  );
}

function MapTable({ m }: { m: MapModel }) {
  return (
    <div className="flex flex-col gap-4" data-testid="map-table">
      <div className="overflow-x-auto rounded-ui border border-line bg-surface">
        <table className="w-full text-left text-[13px]">
          <caption className="sr-only">The goals on the map, one row each</caption>
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Goal</th>
              <th scope="col" className="px-3 py-2 font-medium">State</th>
              <th scope="col" className="px-3 py-2 font-medium">Progress</th>
              <th scope="col" className="px-3 py-2 font-medium">Items</th>
              <th scope="col" className="px-3 py-2 font-medium">Why</th>
            </tr>
          </thead>
          <tbody>
            {m.blocks.map((b) => (
              <tr key={b.id} className="border-b border-line last:border-0 align-top">
                <th scope="row" className="px-3 py-2 font-medium">
                  <a href={href("map", { table: "1" }, [b.id])} className="hover:underline">{b.title}</a>
                  {b.reported_by_team && <span className="block text-xs font-normal text-muted">stages reported by the team</span>}
                </th>
                <td className={cn("whitespace-nowrap px-3 py-2 font-medium", tone(TONE_OF[b.colour]).text)}>
                  <span aria-hidden>{b.shape} </span>
                  {b.label}
                </td>
                <td className="px-3 py-2 tabular-nums">
                  {b.bar.label}
                  {b.stages && (
                    <span className="block text-xs text-muted">
                      {b.stages.map((s) => `${s.stage}: ${s.reported ? s.count : "not reported"}`).join(" · ")}
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">{b.tally_text}</td>
                <td className="px-3 py-2">{b.reason?.text ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="overflow-x-auto rounded-ui border border-line bg-surface">
        <table className="w-full text-left text-[13px]">
          <caption className="sr-only">Who holds the waits, oldest first</caption>
          <thead className="border-b border-line text-xs text-muted">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Held by</th>
              <th scope="col" className="px-3 py-2 font-medium">How many</th>
              <th scope="col" className="px-3 py-2 font-medium">Oldest</th>
              <th scope="col" className="px-3 py-2 font-medium">What</th>
            </tr>
          </thead>
          <tbody>
            {m.waits.map((w) => (
              <tr key={w.holder} className="border-b border-line last:border-0 align-top" data-testid="wait-table-row">
                <th scope="row" className="px-3 py-2 font-medium">{w.holder}</th>
                <td className="px-3 py-2 tabular-nums">{w.count}</td>
                <td className="px-3 py-2 tabular-nums">
                  {w.age_text}
                  {w.note && <span className="block text-xs text-muted">{w.note}</span>}
                </td>
                <td className="px-3 py-2">{w.oldest_text ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Zoom0({ m, table }: { m: MapModel; table: boolean }) {
  const phone = usePhone();
  const you = m.waits.find((w) => w.holder === "you");
  const open = (b: MapBlock) => goIn(nextPath([], { kind: "open", block: b.id }), table);
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4 max-[420px]:p-3" data-testid="map-level" data-level={0}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
        <Breadcrumb path={[]} names={[]} table={table} />
        <a
          href={href("map", { table: table ? undefined : "1" })}
          data-testid="table-toggle"
          className="ml-auto rounded-md border border-line px-2.5 py-1 text-[13px] font-medium text-muted hover:bg-raised hover:text-fg"
        >
          {table ? "Show the map" : "Show as a list"}
        </a>
      </div>
      <Signals m={m} />
      {table ? (
        <MapTable m={m} />
      ) : phone ? (
        <div className="flex flex-col gap-3" data-testid="map-column">
          {you && (
            <p className="rounded-ui border border-state-human/40 bg-state-human/10 px-3 py-2 text-[13.5px] font-medium" data-testid="most-waiting">
              Most waiting work: you, {plural(you.count, "task")}, oldest {you.age_text}
            </p>
          )}
          {phoneOrder(m.blocks).map((b) => (
            <BlockCard key={b.id} b={b} onOpen={open(b)} />
          ))}
          <Waits waits={m.waits} />
          <Legend m={m} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 gap-4">
          <Canvas
            cards={m.blocks.map((b) => ({
              id: b.id,
              height: BLOCK_H,
              onOpen: open(b),
              render: (hint: boolean, through?: () => void) => <BlockCard b={b} onOpen={through ?? open(b)} hint={hint} />,
            }))}
          />
          <aside className="flex w-80 shrink-0 flex-col gap-5 overflow-y-auto" aria-label="Waits and legend">
            <Waits waits={m.waits} />
            <Legend m={m} />
          </aside>
        </div>
      )}
    </div>
  );
}

function TableToggle({ path, table }: { path: string[]; table: boolean }) {
  return (
    <a
      href={href("map", { table: table ? undefined : "1" }, path)}
      data-testid="table-toggle"
      className="ml-auto rounded-md border border-line px-2.5 py-1 text-[13px] font-medium text-muted hover:bg-raised hover:text-fg"
    >
      {table ? "Show the map" : "Show as a list"}
    </a>
  );
}

// An item's mark in a list: its shape in its colour, or the idle hatch for work parked for a week, never a red "!".
function ItemMark({ colour, shape, idle }: { colour: string; shape: string; idle?: boolean }) {
  if (idle) return <span aria-hidden className="idle-hatch inline-block h-2.5 w-2.5 shrink-0 translate-y-[1px] rounded-[2px]" title="idle" />;
  return (
    <span aria-hidden className={cn("shrink-0 text-xs", colour === "done" || colour === "grey" ? "text-muted" : tone(TONE_OF[colour as keyof typeof TONE_OF]).text)}>
      {shape}
    </span>
  );
}

function StatePill({ colour, shape, label }: { colour: MapBlock["colour"] | null; shape: string | null; label: string | null }) {
  if (!colour) return null;
  const t = TONE_OF[colour];
  if (label === "idle") {
    return (
      <span className="idle-hatch inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-xs font-semibold text-fg" data-testid="state">
        idle
      </span>
    );
  }
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold", tone(t).soft, tone(t).text)} data-testid="state">
      <span aria-hidden>{shape}</span>
      {label}
    </span>
  );
}

const STAGE_ROWS = 4;
const stageHeight = (s: MapStageEntry) => 150 + Math.min(STAGE_ROWS, s.leaves.length) * 22 + (s.reason ? 34 : 0);

function StageCard({ s, onOpen, hint = false }: { s: MapStageEntry; onOpen?: () => void; hint?: boolean }) {
  const t = s.colour ? TONE_OF[s.colour] : "void";
  return (
    <article
      data-testid="stage"
      data-stage={s.stage}
      data-colour={s.colour ?? ""}
      data-reported={s.reported}
      tabIndex={0}
      role={onOpen ? "button" : "group"}
      aria-label={`${onOpen ? "Open " : ""}${s.stage}: ${s.reported ? `${s.count}, ${s.tally_text}` : "not reported"}.${s.reason ? ` ${s.reason.text}.` : ""}`}
      onKeyDown={(e) => {
        if (opensOn(e.key) && onOpen) {
          e.preventDefault();
          onOpen();
        }
      }}
      onDoubleClick={onOpen}
      className={cn(
        "relative flex h-full min-w-0 flex-col rounded-ui border border-l-[4px] border-line px-3.5 py-3 shadow-sm outline-none focus-visible:ring-[3px] focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
        onOpen && "cursor-pointer",
        s.reported ? "bg-surface" : "map-hatch border-dashed",
        s.label === "idle" ? "border-l-state-idle" : tone(t).edge,
      )}
    >
      <header className="flex min-w-0 items-start gap-2">
        {onOpen ? (
          <button type="button" tabIndex={-1} onClick={onOpen} className="min-w-0 flex-1 text-left text-[15px] font-semibold capitalize leading-snug hover:underline" data-testid="stage-title">
            {s.stage}
          </button>
        ) : (
          <span className="min-w-0 flex-1 text-[15px] font-semibold capitalize leading-snug" data-testid="stage-title">{s.stage}</span>
        )}
        <StatePill colour={s.colour} shape={s.shape} label={s.label} />
      </header>
      <p className="mt-1 text-[22px] font-semibold tabular-nums leading-none" data-testid="stage-count">{s.reported ? s.count : "—"}</p>
      <Tally text={s.tally_text} />
      {s.oldest && s.count && s.colour !== "done" ? <p className="text-[13px] text-muted">Oldest: <span className="font-medium text-fg">{s.oldest.age_text}</span></p> : null}
      {s.reason && (
        <p className={cn("mt-1.5 text-[13px] font-medium leading-snug", tone(t).text)} data-testid="reason">
          <span className="line-clamp-2">{s.reason.text}</span>
        </p>
      )}
      {s.leaves.length > 0 && (
        <ul className="mt-2 flex flex-col gap-0.5 text-[13px]">
          {s.leaves.slice(0, STAGE_ROWS).map((lf) => (
            <li key={lf.id} className="flex min-w-0 items-baseline gap-1.5">
              <ItemMark colour={lf.colour} shape={lf.shape} idle={lf.idle} />
              <span className="truncate">{lf.title}</span>
              {lf.checks && <span className="ml-auto shrink-0 text-xs text-muted" data-testid="checks">{lf.checks.text.replace(/^checks: /, "")}</span>}
            </li>
          ))}
          {s.leaves.length > STAGE_ROWS && <li className="text-xs text-muted">and {s.leaves.length - STAGE_ROWS} more</li>}
        </ul>
      )}
      {hint && onOpen && (
        <button type="button" tabIndex={-1} onClick={onOpen} data-testid="open-hint" className="absolute bottom-2 right-2 inline-flex items-center gap-0.5 rounded-md bg-accent px-2 py-1 text-xs font-semibold text-white shadow">
          Open <ChevronRight className="h-3 w-3" aria-hidden />
        </button>
      )}
    </article>
  );
}

function Zoom1({ m, table }: { m: Zoom1Model; table: boolean }) {
  const phone = usePhone();
  const path = m.path;
  const open = (s: MapStageEntry) => (s.reported && s.count ? goIn(nextPath(path, { kind: "open", block: s.stage }), table) : undefined);
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4 max-[420px]:p-3" data-testid="map-level" data-level={1}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
        <Breadcrumb path={path} names={[m.block.title]} table={table} />
        <TableToggle path={path} table={table} />
      </div>
      <Signals m={m} />
      <p className="text-[13px]" data-testid="block-line">
        <span className="font-semibold">{m.block.title}</span>: {m.block.bar.label} · {m.block.tally_text}
        {m.block.reported_by_team && <span className="text-muted"> · stages reported by the team</span>}
      </p>
      {table ? (
        <div className="overflow-x-auto rounded-ui border border-line bg-surface" data-testid="map-table">
          <table className="w-full text-left text-[13px]">
            <caption className="sr-only">The stages of {m.block.title}, in order</caption>
            <thead className="border-b border-line text-xs text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Stage</th>
                <th scope="col" className="px-3 py-2 font-medium">How many</th>
                <th scope="col" className="px-3 py-2 font-medium">State</th>
                <th scope="col" className="px-3 py-2 font-medium">Oldest</th>
                <th scope="col" className="px-3 py-2 font-medium">Why</th>
              </tr>
            </thead>
            <tbody>
              {m.stages.map((s) => (
                <tr key={s.stage} className="border-b border-line align-top last:border-0">
                  <th scope="row" className="px-3 py-2 font-medium capitalize">
                    {s.reported && s.count ? <a href={href("map", { table: "1" }, [...path, s.stage])} className="hover:underline">{s.stage}</a> : s.stage}
                  </th>
                  <td className="px-3 py-2 tabular-nums">{s.reported ? s.count : "not reported"}</td>
                  <td className="px-3 py-2">{s.reported ? s.tally_text : ""}</td>
                  <td className="px-3 py-2">{s.colour !== "done" ? (s.oldest?.age_text ?? "") : ""}</td>
                  <td className="px-3 py-2">{s.reason?.text ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : phone ? (
        <div className="flex flex-col gap-3" data-testid="map-column">
          {m.stages.map((s) => (
            <StageCard key={s.stage} s={s} onOpen={open(s)} />
          ))}
          <Legend m={m} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 gap-4">
          <Canvas
            chain
            cards={m.stages.map((s) => ({
              id: `stage:${s.stage}`,
              label: s.stage,
              height: stageHeight(s),
              onOpen: open(s),
              render: (hint: boolean, through?: () => void) => <StageCard s={s} onOpen={through ?? open(s)} hint={hint} />,
            }))}
          />
          <aside className="flex w-80 shrink-0 flex-col gap-5 overflow-y-auto" aria-label="Legend">
            <Legend m={m} />
          </aside>
        </div>
      )}
    </div>
  );
}

function linkHref(link: { view: "now" | "audit"; ref: string }): string {
  return link.view === "now" ? href("now") : href("audit", { subject: link.ref });
}


function HolderCard({ h }: { h: MapHolder }) {
  const t = TONE_OF[h.colour];
  return (
    <article
      data-testid="holder"
      data-holder={h.holder}
      data-colour={h.colour}
      tabIndex={0}
      aria-label={`${h.holder}: ${h.count}, ${h.tally_text}`}
      role="group"
      className={cn("flex h-full min-w-0 flex-col rounded-ui border border-l-[4px] border-line bg-surface px-3.5 py-3 shadow-sm outline-none focus-visible:ring-[3px] focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg", h.items.length && h.items.every((i) => i.idle) ? "border-l-state-idle" : tone(t).edge)}
    >
      <header className="flex min-w-0 items-start gap-2">
        <h2 className={cn("min-w-0 flex-1 text-[15px] font-semibold leading-snug", h.kind === "you" && tone("human").text)}>{h.holder}</h2>
        <StatePill colour={h.colour} shape={h.shape} label={h.colour === "done" ? "done" : h.items.length && h.items.every((i) => i.idle) ? "idle" : null} />
      </header>
      <p className="mt-0.5 text-[13px] text-muted" data-testid="tally">{plural(h.count, "item")} · {h.tally_text}</p>
      <ul className="mt-2 flex flex-col gap-2">
        {h.items.slice(0, 8).map((i) => (
          <li key={i.id} className="min-w-0 text-[13px]" data-testid="holder-item">
            <div className="flex min-w-0 items-baseline gap-1.5">
              <ItemMark colour={i.colour} shape={i.shape} idle={i.idle} />
              <span className="truncate font-medium">{i.title}</span>
              {i.age_text && <span className="ml-auto shrink-0 tabular-nums text-muted">{i.age_text}</span>}
            </div>
            <p className="pl-4 text-xs text-muted [overflow-wrap:anywhere]">
              {i.why ?? ""}
              {i.checks && <span data-testid="checks">{i.why ? " · " : ""}{i.checks.text}</span>}
              {" · "}
              <a href={linkHref(i.link)} className="font-medium text-accent hover:underline" data-testid="item-link">{i.link.kind}</a>
            </p>
          </li>
        ))}
        {h.items.length > 8 && <li className="text-xs text-muted">and {h.items.length - 8} more, in the list form</li>}
      </ul>
    </article>
  );
}

function columnHead(c: StageGrid["columns"][number], g: StageGrid): [string, string, string] {
  const days = g.idle_after_s ? Math.round(g.idle_after_s / 86400) : 7;
  const name = c.stage === IDLE ? `Idle ≥ ${days} days` : c.stage;
  if (!c.reported) return [name, "not reported", "by this team's export"];
  const n = c.count ?? 0;
  if (c.stage === IDLE) return [name, `${n} open, no move`, "parked or abandoned"];
  if (c.stage === "merged") return [name, `${c.flow ?? n} in the week`, "by the role that opened each"];
  if (c.flow == null) return [name, `${n}`, ""];
  if (c.stage === "opened") return [name, `${n} waiting < ${days} d`, `${c.flow} opened in the week`];
  return [name, `${n} open now`, `${c.flow} reached it in the week`];
}

// Who holds what across all of a goal's stages (mock-up v2). A cell opens its stage at that holder; the stage this
// level opened is outlined, and the live stage with the most jammed work is marked as the bottleneck.
function Grid({ m, table, who }: { m: Zoom2Model; table: boolean; who: string | null }) {
  const g = m.grid;
  const here = m.stage.stage;
  const phone = usePhone();
  // a phone has room for a few columns: the ones with something in them, and the stage this level opened
  const cols = phone ? g.columns.filter((c) => (c.count ?? 0) > 0 || stageOfColumn(c.stage) === here) : g.columns;
  const left = g.columns.filter((c) => !cols.includes(c)).map((c) => columnHead(c, g)[0]);
  const open = (col: string, holder: string) => () =>
    opener(() => {
      window.location.hash = href("map", { table: table ? "1" : undefined, who: holder }, [m.path[0], stageOfColumn(col)]);
    });
  return (
    <div className="overflow-x-auto rounded-ui border border-line bg-surface" data-testid="grid">
      <table className={cn("w-full table-fixed border-collapse text-[13px]", !phone && "min-w-[720px]")}>
        <caption className="sr-only">Who holds the open work in {m.block.title}, by stage</caption>
        <colgroup>
          <col className={phone ? "w-24" : "w-44"} />
          {cols.map((c) => <col key={c.stage} />)}
        </colgroup>
        <thead>
          <tr>
            <th scope="col" className="px-3 py-2 text-left align-bottom text-xs font-medium text-muted">Held by</th>
            {cols.map((c) => {
              const [t1, t2, t3] = columnHead(c, g);
              const jam = g.bottleneck === c.stage;
              const mine = stageOfColumn(c.stage) === here;
              return (
                <th
                  key={c.stage}
                  scope="col"
                  data-testid="grid-head"
                  data-stage={c.stage}
                  data-bottleneck={jam ? "1" : undefined}
                  aria-current={mine ? "true" : undefined}
                  className="p-1.5 align-top font-normal"
                >
                  <div className={cn("rounded-ui border px-2.5 py-2 text-left", jam ? "border-2 border-state-blocked" : "border-line", mine ? "bg-accent/10" : c.stage === IDLE ? "bg-raised" : "bg-bg")}>
                    <div className="truncate font-semibold text-fg">{t1.charAt(0).toUpperCase() + t1.slice(1)}</div>
                    <div className="truncate font-medium tabular-nums text-fg">{t2}</div>
                    {t3 && <div className="truncate text-xs text-muted">{t3}</div>}
                    {jam && <div className={cn("mt-0.5 text-xs font-semibold", tone("blocked").text)}>▲ the bottleneck</div>}
                  </div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {g.rows.map((r) => (
            <tr key={r.holder} className="border-t border-line" data-testid="grid-row" data-holder={r.holder}>
              <th scope="row" title={r.holder} className={cn("truncate px-3 py-2 text-left font-semibold max-md:px-2", r.kind === "you" && tone("human").text, who === r.holder && "underline")}>
                {r.holder}
              </th>
              {cols.map((c) => {
                const cell = r.cells[c.stage];
                if (!cell) return <td key={c.stage} className={cn("px-2 py-2", c.stage === IDLE && "bg-raised/60")} />;
                const share = barShare(g, c.stage, cell.count);
                const fill = c.stage === IDLE ? "idle-hatch" : cell.colour === "done" ? "bg-state-done" : tone(TONE_OF[cell.colour]).dot;
                const detail = cell.items.slice(0, 6).map((i) => `${i.id} ${i.age_text ?? ""}`.trim()).join("; ") + (cell.items.length > 6 ? " …" : "");
                return (
                  <td key={c.stage} className={cn("px-2 py-1.5", c.stage === IDLE && "bg-raised/60")}>
                    <button
                      type="button"
                      onClick={open(c.stage, r.holder)}
                      data-testid="grid-cell"
                      data-stage={c.stage}
                      data-colour={cell.colour}
                      title={`${r.holder}, ${c.stage}: ${cell.count}. ${detail}`}
                      aria-label={`${r.holder}, ${c.stage}: ${cell.count}${cell.colour === "red" ? ", jammed" : ""}. Open`}
                      className="flex w-full items-center gap-1.5 rounded-md px-1 py-1 text-left outline-none hover:bg-raised focus-visible:ring-[3px] focus-visible:ring-accent"
                    >
                      <span className="h-[18px] min-w-0 flex-1">
                        <span className={cn("block h-full rounded", fill)} style={{ width: `${share * 100}%` }} aria-hidden />
                      </span>
                      <span className="w-7 shrink-0 text-right font-semibold tabular-nums">{cell.count}</span>
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
          {g.rows.length === 0 && (
            <tr><td colSpan={cols.length + 1} className="px-3 py-4 text-muted">Nothing is open in {m.block.title}.</td></tr>
          )}
        </tbody>
      </table>
      {left.length > 0 && (
        <p className="border-t border-line px-3 py-2 text-[12.5px] text-muted" data-testid="grid-left-out">
          Empty here, so left out on a small screen: {left.join(", ")}
        </p>
      )}
      {g.roles_with_nothing.length > 0 && (
        <p className="border-t border-line px-3 py-2 text-[12.5px] text-muted" data-testid="grid-nothing">
          <span className="font-medium text-fg">{plural(g.roles_with_nothing.length, "role")} with nothing open:</span> {g.roles_with_nothing.join(", ")}
        </p>
      )}
    </div>
  );
}

function Zoom2({ m, table, who }: { m: Zoom2Model; table: boolean; who: string | null }) {
  const path = m.path;
  const picked = m.holders.find((h) => h.holder === who) ?? m.holders[0];
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4 max-[420px]:p-3" data-testid="map-level" data-level={2}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
        <Breadcrumb path={path} names={[m.block.title, m.stage.stage]} table={table} />
        <TableToggle path={path} table={table} />
      </div>
      <Signals m={m} />
      <p className="text-[13px]" data-testid="block-line">
        <span className="font-semibold capitalize">{m.stage.stage}</span> in {m.block.title}: {plural(m.stage.count ?? 0, "item")} · {m.stage.tally_text}
        {m.stage.reason && <span className={cn("font-medium", m.stage.colour ? tone(TONE_OF[m.stage.colour]).text : "")}> · {m.stage.reason.text}</span>}
      </p>
      {table ? (
        <div className="overflow-x-auto rounded-ui border border-line bg-surface" data-testid="map-table">
          <table className="w-full text-left text-[13px]">
            <caption className="sr-only">Who holds each item in {m.stage.stage}</caption>
            <thead className="border-b border-line text-xs text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Held by</th>
                <th scope="col" className="px-3 py-2 font-medium">Item</th>
                <th scope="col" className="px-3 py-2 font-medium">Waits on, since</th>
                <th scope="col" className="px-3 py-2 font-medium">Where to act</th>
              </tr>
            </thead>
            <tbody>
              {m.holders.flatMap((h) =>
                h.items.map((i) => (
                  <tr key={`${h.holder}:${i.id}`} className="border-b border-line align-top last:border-0">
                    <th scope="row" className="px-3 py-2 font-medium">{h.holder}</th>
                    <td className="px-3 py-2">
                      <span aria-hidden>{i.idle ? "idle · " : `${i.shape} `}</span>
                      {i.title}
                      {i.checks && <span className="block text-xs text-muted">{i.checks.text}</span>}
                    </td>
                    <td className="px-3 py-2">{[i.why, i.age_text].filter(Boolean).join(" · ")}</td>
                    <td className="px-3 py-2"><a href={linkHref(i.link)} className="text-accent hover:underline">{i.link.kind}</a></td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 gap-4 overflow-y-auto max-lg:flex-col" data-testid="map-grid-level">
          <div className="flex min-w-0 flex-1 flex-col gap-3">
            <Grid m={m} table={table} who={picked?.holder ?? null} />
            {picked ? (
              <section aria-label={`${picked.holder} in ${m.stage.stage}`} className="flex flex-col gap-1.5">
                <h2 className="text-[13px] text-muted">
                  In <span className="font-medium capitalize text-fg">{m.stage.stage}</span>, held by <span className="font-medium text-fg">{picked.holder}</span>; open another cell for its items
                </h2>
                <div className="max-w-[760px]"><HolderCard h={picked} /></div>
              </section>
            ) : (
              <p className="text-[13px] text-muted">Nothing is held in {m.stage.stage}; open another cell for its items.</p>
            )}
          </div>
          <aside className="flex w-80 shrink-0 flex-col gap-5 max-lg:w-full" aria-label="Legend">
            <Legend m={m} />
          </aside>
        </div>
      )}
    </div>
  );
}
