import { memo, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Background, BackgroundVariant, ReactFlow, ReactFlowProvider, useStore, type Node, type NodeProps } from "@xyflow/react";
import { ChevronRight } from "lucide-react";
import { get, type DigestModel } from "@/lib/api";
import { clock, duration, plural } from "@/lib/format";
import { href } from "@/lib/route";
import {
  BLOCK_H,
  BLOCK_W,
  gridPosition,
  nextPath,
  phoneOrder,
  showsOpenHint,
  tallyText,
  thresholdsText,
  TONE_OF,
  type MapBlock,
  type MapModel,
  type MapWait,
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

export default function MapView({ params, path }: { params: URLSearchParams; path: string[] }) {
  const table = params.get("table") === "1"; // in the URL, so back, reload and a shared link keep it
  const q = useQuery({ queryKey: ["map"], queryFn: () => get<MapModel>("/api/map"), refetchInterval: 30_000 });
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const up = nextPath(path, { kind: "up" });
      if (up.length !== path.length) go(up, table);
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [path, table]);
  if (q.isPending) return <div className="p-4"><Loading what="the map" /></div>;
  if (q.isError) return <div className="p-4"><Problem error={q.error} /></div>;
  const m = q.data;
  if (path.length) return <NotYet m={m} path={path} table={table} />;
  return <Zoom0 m={m} table={table} />;
}

function Breadcrumb({ m, path, table }: { m: MapModel; path: string[]; table: boolean }) {
  const names = path.map((id) => m.blocks.find((b) => b.id === id)?.title ?? id);
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
          <span className="truncate font-semibold" aria-current={i === names.length - 1 ? "page" : undefined}>{n}</span>
        </span>
      ))}
    </nav>
  );
}

function NotYet({ m, path, table }: { m: MapModel; path: string[]; table: boolean }) {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4" data-testid="map-level" data-level={path.length}>
      <Breadcrumb m={m} path={path} table={table} />
      <Empty testid="zoom1-next">
        This goal's stages, one level in, come with the next part of the map. Press Escape, or use All goals above, to go
        back.
      </Empty>
    </div>
  );
}

function Signals({ m }: { m: MapModel }) {
  const s = m.signals;
  const d = useQuery({ queryKey: ["digest"], queryFn: () => get<DigestModel>("/api/digest"), refetchInterval: 60_000 });
  const lw = s.longest_without_a_move;
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
        <span data-testid="sig-longest" className="text-sm text-fg">
          Longest without a move: <span className="font-semibold">{lw ? lw.text : "no turns in the last day"}</span>
          {lw?.ongoing && <span className="text-muted"> (still going)</span>}
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

function Cells({ b }: { b: MapBlock }) {
  if (!b.leaves.length) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-[3px]" aria-hidden data-testid="cells">
      {b.leaves.slice(0, 60).map((lf) => (
        <span
          key={lf.id}
          title={`${lf.title}: ${lf.colour === "done" ? "done" : lf.why ?? b.label}`}
          className={cn(
            "grid h-4 w-4 place-items-center rounded-[3px] text-[9px] font-bold leading-none",
            lf.colour === "grey" ? "border border-state-void/70 text-state-void" : cn(tone(TONE_OF[lf.colour]).dot, "text-white"),
          )}
        >
          {lf.colour === "done" || lf.colour === "grey" ? "" : lf.shape}
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

function BlockCard({ b, onOpen, hint = false, inCanvas = false, className }: { b: MapBlock; onOpen: () => void; hint?: boolean; inCanvas?: boolean; className?: string }) {
  const t = TONE_OF[b.colour];
  const notReported = b.stages?.filter((s) => !s.reported).map((s) => s.stage) ?? [];
  return (
    <article
      data-testid="block"
      data-block={b.id}
      data-colour={b.colour}
      tabIndex={0}
      aria-label={`${b.title}: ${b.label}. ${b.bar.label}. ${tallyText(b)}.${b.reason ? ` ${b.reason.text}.` : ""}`}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onOpen();
        }
      }}
      onDoubleClick={inCanvas ? undefined : onOpen}
      className={cn(
        "relative flex h-full min-w-0 flex-col rounded-ui border border-l-[4px] border-line bg-surface px-3.5 py-3 shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-accent",
        tone(t).edge,
        className,
      )}
    >
      <header className="flex min-w-0 items-start gap-2">
        <button type="button" tabIndex={-1} onClick={onOpen} className="min-w-0 flex-1 text-left text-[15px] font-semibold leading-snug hover:underline" data-testid="block-title">
          <span className="line-clamp-2">{b.title}</span>
        </button>
        <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold", tone(t).soft, tone(t).text)} data-testid="block-state">
          <span aria-hidden>{b.shape}</span>
          {b.label}
        </span>
      </header>
      {b.reported_by_team && <p className="mt-0.5 text-xs text-muted" data-testid="reported">Stages reported by the team</p>}
      <Bar b={b} />
      {b.kind === "changes" && <p className="mt-1 text-[13px] font-medium tabular-nums">{b.bar.label}</p>}
      <p className="mt-1 text-[13px] text-muted" data-testid="tally">{tallyText(b)}</p>
      {b.reason && (
        <p className={cn("mt-1.5 text-[13px] font-medium leading-snug", tone(t).text)} data-testid="reason">
          <span className="line-clamp-2">{b.reason.text}</span>
        </p>
      )}
      <Stages b={b} />
      {notReported.length > 0 && <p className="mt-1 text-[11.5px] text-muted">Not reported by this team's export: {notReported.join(", ")}.</p>}
      <Cells b={b} />
      {hint && (
        <button type="button" tabIndex={-1} onClick={onOpen} data-testid="open-hint" className="absolute bottom-2 right-2 inline-flex items-center gap-0.5 rounded-md bg-accent px-2 py-1 text-xs font-semibold text-white shadow">
          Open <ChevronRight className="h-3 w-3" aria-hidden />
        </button>
      )}
    </article>
  );
}

type BlockData = { b: MapBlock; onOpen: () => void };

const BlockNode = memo(function BlockNode({ data }: NodeProps<Node<BlockData>>) {
  const zoom = useStore((s) => s.transform[2]);
  return <BlockCard b={data.b} onOpen={data.onOpen} hint={showsOpenHint(zoom)} inCanvas />;
});
const nodeTypes = { block: BlockNode };

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

function Legend({ m }: { m: MapModel }) {
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
      </ul>
      <p className="text-muted">A block takes its worst child's colour. Jammed after: {thresholdsText(m.thresholds_s, duration)}.</p>
      <p className="text-muted">"≥" means the start is older than anything onloop has seen.</p>
    </section>
  );
}

function Canvas({ m, table }: { m: MapModel; table: boolean }) {
  const nodes = useMemo<Node<BlockData>[]>(
    () =>
      m.blocks.map((b, i) => ({
        id: b.id,
        type: "block",
        position: gridPosition(i),
        data: { b, onOpen: () => go(nextPath([], { kind: "open", block: b.id }), table) },
        draggable: false,
        selectable: false,
        focusable: false, // the block itself is the one Tab stop, in reading order
        // React Flow turns pointer events off on a node that is neither draggable, selectable nor clickable; a block's
        // title, Open hint and double-click need them, and a node's own style wins
        style: { width: BLOCK_W, height: BLOCK_H, pointerEvents: "all" },
      })),
    [m.blocks, table],
  );
  return (
    <div className="relative min-h-[420px] min-w-0 flex-1 overflow-hidden rounded-ui border border-line bg-bg" data-testid="map-canvas">
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={[]}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.06, maxZoom: 1 }}
          minZoom={0.3}
          maxZoom={2.5}
          zoomOnScroll
          zoomOnPinch
          panOnDrag
          zoomOnDoubleClick={false}
          onNodeDoubleClick={(_, n) => (n.data as BlockData).onOpen()}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          proOptions={{ hideAttribution: true }}
          deleteKeyCode={null}
        >
          <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="hsl(var(--line))" />
        </ReactFlow>
      </ReactFlowProvider>
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
                <td className="px-3 py-2">{tallyText(b)}</td>
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
  const open = (b: MapBlock) => () => go(nextPath([], { kind: "open", block: b.id }), table);
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4 max-[420px]:p-3" data-testid="map-level" data-level={0}>
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
        <Breadcrumb m={m} path={[]} table={table} />
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
          <Canvas m={m} table={table} />
          <aside className="flex w-80 shrink-0 flex-col gap-5 overflow-y-auto" aria-label="Waits and legend">
            <Waits waits={m.waits} />
            <Legend m={m} />
          </aside>
        </div>
      )}
    </div>
  );
}
