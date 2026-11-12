import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
  type NodeMouseHandler,
} from "@xyflow/react";
import { ExternalLink, Maximize2, X } from "lucide-react";
import { api, type FlowCard, type FlowModel } from "@/lib/api";
import { CARD_W, layout, lookOf, traced, type CardData, type ResData } from "@/lib/flow";
import { clock, duration, plural, reason } from "@/lib/format";
import { href, setParams } from "@/lib/route";
import { cn } from "@/lib/utils";
import { Command, Empty, Loading, Pill, Problem, tone } from "@/components/kit";
import { CardNode, ResourceNode, ZoneNode } from "@/components/FlowNodes";

const nodeTypes = { card: CardNode, zone: ZoneNode, resource: ResourceNode };
const PHONE = "(max-width: 767px)";
export const PHONE_ZOOM = 0.9; // a card is readable at this zoom on a 390 px screen; fit-everything is not
const WIDE_ZOOM = 0.9;

type Mode = "graph" | "you" | "roadmap" | "calendar";
const MODES: { id: Mode; label: string; short: string }[] = [
  { id: "graph", label: "Graph", short: "Graph" },
  { id: "you", label: "Waiting on you", short: "For you" },
  { id: "roadmap", label: "Roadmap", short: "Roadmap" },
  { id: "calendar", label: "Calendar", short: "Calendar" },
];

function Toolbar({ mode, overlay, hideDone, params, m }: { mode: Mode; overlay: boolean; hideDone: boolean; params: URLSearchParams; m: FlowModel }) {
  const set = (k: string, v: string | undefined) => setParams("flow", { ...Object.fromEntries(params), [k]: v });
  const humans = m.waits.filter((w) => w.on_kind === "human" && w.card).length;
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2 max-[420px]:px-3">
      <div role="tablist" aria-label="Flow view" className="flex min-w-0 gap-0.5 overflow-x-auto rounded-md border border-line bg-raised p-0.5">
        {MODES.map((x) => (
          <button
            key={x.id}
            role="tab"
            type="button"
            aria-selected={mode === x.id}
            data-testid={`mode-${x.id}`}
            onClick={() => set("mode", x.id === "graph" ? undefined : x.id)}
            className={cn("shrink-0 rounded px-2.5 py-1 text-[13px] font-medium max-sm:px-2", mode === x.id ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg")}
          >
            <span className="max-sm:hidden">{x.label}</span>
            <span className="sm:hidden">{x.short}</span>
            {x.id === "you" && humans > 0 && (
              <span className="ml-1 rounded-full bg-state-human/20 px-1.5 text-[11px] font-semibold text-state-human" title="Cards that wait on you. Now counts a paused role as one ask.">
                <span className="max-sm:hidden">{plural(humans, "card")}</span>
                <span className="sm:hidden">{humans}</span>
              </span>
            )}
          </button>
        ))}
      </div>
      {(mode === "graph" || mode === "you") && (
        <div className="flex items-center gap-3 text-[13px]">
          <label className="flex items-center gap-1.5" title="Highlight the biggest constraint and what it holds up; dim the rest">
            <input type="checkbox" data-testid="overlay" checked={overlay && mode === "graph"} disabled={!m.constraint || mode !== "graph"} onChange={() => set("overlay", overlay ? "0" : undefined)} />
            Constraint
          </label>
          <label className="flex items-center gap-1.5 text-muted">
            <input type="checkbox" data-testid="hide-done" checked={hideDone} onChange={() => set("done", hideDone ? undefined : "0")} />
            Hide done
          </label>
        </div>
      )}
      {m.constraint && (
        <p className="ml-auto text-[13px] text-muted max-lg:hidden" data-testid="flow-constraint">
          Constraint: <span className="font-mono font-medium text-state-blocked">{m.constraint.node === "human" ? "you" : m.constraint.node}</span>, holding{" "}
          {plural(m.constraint.cards.length, "card")} ({duration(m.constraint.downstream_wait_s)} of waiting)
        </p>
      )}
    </div>
  );
}

function SidePanel({ card, m, onClose }: { card: FlowCard; m: FlowModel; onClose: () => void }) {
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [onClose]);
  const waits = m.waits.filter((w) => w.card === card.id);
  const look = lookOf(card, waits);
  const zone = m.zones.find((z) => z.id === card.zone);
  const byId = new Map(m.cards.map((c) => [c.id, c] as const));
  const after = m.edges.filter((e) => e.kind === "depends" && e.target === card.id).map((e) => byId.get(e.source)).filter(Boolean) as FlowCard[];
  const before = m.edges.filter((e) => e.kind === "depends" && e.source === card.id).map((e) => byId.get(e.target)).filter(Boolean) as FlowCard[];
  const held = m.constraint?.cards.includes(card.id);
  const evidence = waits.find((w) => w.reason === "missing_evidence");
  const paused = waits.find((w) => w.reason === "paused_by_human");
  const Row = ({ k, children }: { k: string; children: React.ReactNode }) => (
    <div className="grid grid-cols-[6.5rem_1fr] gap-2 py-1.5 text-[13px]">
      <dt className="text-muted">{k}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
  return (
    <aside
      data-testid="side-panel"
      aria-label={`Details of ${card.title}`}
      className="absolute inset-x-0 bottom-0 z-20 max-h-[62%] overflow-y-auto rounded-t-zone border-t border-line bg-surface p-4 shadow-xl sm:inset-y-0 sm:left-auto sm:right-0 sm:max-h-none sm:w-[380px] sm:rounded-none sm:border-l sm:border-t-0"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-1.5 flex flex-wrap gap-1.5">
            <Pill t={look.t}>{look.label}</Pill>
            {held && <Pill t="blocked">held by the constraint</Pill>}
          </div>
          <h2 className="text-base font-semibold leading-snug">{card.title}</h2>
          <p className="mt-0.5 font-mono text-xs text-muted">{card.id}</p>
        </div>
        <button type="button" aria-label="Close" onClick={onClose} className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-muted hover:bg-raised">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
      {(card.default || card.expires) && (
        <div className="mt-3 rounded-ui border border-state-human/40 bg-state-human/10 p-3 text-[13px]">
          <p>{card.default ? <>If nobody answers: <b>{card.default}</b></> : "No default: nothing happens until someone answers."}</p>
          {card.expires && <p className="mt-1 text-muted">Expires {clock(card.expires)}</p>}
        </div>
      )}
      <dl className="mt-3 divide-y divide-line/60">
        <Row k="Zone">{zone ? `${zone.label} (${zone.source === "direction" ? "its direction" : zone.source === "owner" ? "its owner role" : "no direction or owner"})` : card.zone}</Row>
        <Row k="Owner">{card.owner_role ? <span className="font-mono">{card.owner_role}</span> : <span className="text-muted">nobody</span>}</Row>
        <Row k="Opened">{card.opened_at ? `${clock(card.opened_at)} (${duration(card.age_s)} ago)` : "unknown"}</Row>
        {waits.map((w) => (
          <Row key={w.id} k="Waits on">
            <span className="font-mono">{w.on_kind === "human" ? "you" : w.on_ref}</span>: {reason(w.reason)}, {duration(w.age_s)}
          </Row>
        ))}
        {after.length > 0 && <Row k="After">{after.map((c) => c.title).join(", ")}</Row>}
        {before.length > 0 && <Row k="Before">{before.map((c) => c.title).join(", ")}</Row>}
        {card.ref && (
          <Row k="Source">
            <a className="inline-flex items-center gap-1 text-accent hover:underline" href={card.ref} target="_blank" rel="noopener noreferrer">
              open <ExternalLink className="h-3 w-3" aria-hidden />
            </a>
          </Row>
        )}
      </dl>
      {evidence && <Command cmd={`onloop item evidence ${card.id} <ref>`} />}
      {paused && card.owner_role && <Command cmd={`onloop role resume ${card.owner_role}`} />}
      <p className="mt-3 text-[12px] text-muted">
        Read-only. <a className="text-accent hover:underline" href={href("audit", { subject: card.id })}>Its events in Audit</a>
      </p>
    </aside>
  );
}

function Canvas({ m, mode, overlay, hideDone }: { m: FlowModel; mode: "graph" | "you"; overlay: boolean; hideDone: boolean }) {
  const [laid, setLaid] = useState<{ nodes: Node[]; edges: Edge[]; zoneOrder: string[] } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [opening, setOpening] = useState<string>("");
  const [map, setMap] = useState(false);
  const { fitView, setViewport } = useReactFlow();
  // Nodes are held in React Flow's own state so it can record their measured size; the first view waits for it.
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges] = useEdgesState<Edge>([]);
  useEffect(() => {
    let live = true;
    layout(m, { overlay, onlyHuman: mode === "you", hideDone }).then((l) => {
      if (!live) return;
      setLaid(l);
      setNodes(l.nodes);
      setEdges(l.edges);
    });
    return () => {
      live = false;
    };
  }, [m, overlay, mode, hideDone, setNodes, setEdges]);

  // The first view is set once the nodes are measured. On a phone it opens on the first zone that holds something
  // waiting on the human, at a readable zoom, with "Show all" one tap away; elsewhere the whole graph fits. The view
  // moves only when the set of cards changes, never under a person who is panning.
  const measured = useNodesInitialized();
  const key = useRef("");
  useEffect(() => {
    if (!laid || !measured) return;
    const k = `${mode}|${laid.nodes.map((n) => n.id).join("|")}`;
    if (key.current === k) return;
    key.current = k;
    window.requestAnimationFrame(() => {
      const phone = window.matchMedia(PHONE).matches;
      const humanZone = phone
        ? laid.zoneOrder.find((z) => laid.nodes.some((n) => n.type === "card" && n.parentId === `zone:${z}` && (n.data as CardData).waits.some((w) => w.on_kind === "human")))
        : undefined;
      const zone = humanZone ? laid.nodes.find((n) => n.id === `zone:${humanZone}`) : undefined;
      const resZone = !phone && mode === "graph" && m.constraint ? laid.nodes.find((n) => n.id === "zone:resources") : undefined;
      if (zone) {
        // Below the button row, so "Show all" never sits on the zone's title.
        setViewport({ x: -zone.position.x * PHONE_ZOOM + 12, y: -zone.position.y * PHONE_ZOOM + 52, zoom: PHONE_ZOOM }, { duration: 0 });
        setOpening(humanZone!);
      } else if (resZone) {
        // On a wide screen it opens on the constraint: the locks, queues and you at the left, the cards they hold to
        // their right, at a zoom where titles read. "Show all" fits everything.
        setViewport({ x: -resZone.position.x * WIDE_ZOOM + 20, y: -resZone.position.y * WIDE_ZOOM + 56, zoom: WIDE_ZOOM }, { duration: 0 });
        setOpening("constraint");
      } else {
        fitView({ padding: 0.08, duration: 0 });
        setOpening("all");
      }
    });
  }, [laid, measured, mode, fitView, setViewport, m.constraint]);

  // What is in focus: the card or resource selected (a tap on a phone), else the one the pointer is over. Its traces,
  // the lines between it and what it waits on (or what waits on it), are drawn; every other trace stays hidden.
  const [picked, setPicked] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  useEffect(() => {
    setSelected(null);
    setPicked(null);
  }, [mode]); // a detail panel belongs to the view it was opened in
  const select: NodeMouseHandler = useCallback((_, n) => {
    if (n.type === "card") {
      setSelected(n.id);
      setPicked(n.id);
    } else if (n.type === "resource") {
      setSelected(null);
      setPicked((p) => (p === n.id ? null : n.id));
    }
  }, []);
  const focus = hover ?? picked;
  const shownEdges = useMemo(() => traced(edges, focus), [edges, focus]);
  const card = selected ? m.cards.find((c) => c.id === selected) ?? null : null;
  if (!laid) return <div className="p-4"><Loading what="the layout" /></div>;
  if (laid.nodes.length === 0) return <div className="p-4"><Empty>{mode === "you" ? "Nothing on the canvas waits on you." : "No items in the ledger yet."}</Empty></div>;
  return (
    <div className="relative min-h-0 flex-1" data-testid="canvas" data-opening={opening} data-focus={focus ?? ""}>
      <ReactFlow
        nodes={nodes}
        edges={shownEdges}
        onNodesChange={onNodesChange}
        onNodeMouseEnter={(_, n) => n.type !== "zone" && setHover(n.id)}
        onNodeMouseLeave={() => setHover(null)}
        nodeTypes={nodeTypes}
        onNodeClick={select}
        onPaneClick={() => {
          setSelected(null);
          setPicked(null);
        }}
        nodesDraggable={false}
        nodesConnectable={false}
        edgesFocusable={false}
        minZoom={0.1}
        maxZoom={1.8}
        panOnScroll
        zoomOnScroll={false}
        zoomActivationKeyCode={["Control", "Meta"]}
        proOptions={{ hideAttribution: true }}
        deleteKeyCode={null}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="hsl(var(--line))" />
        <Panel position="top-right" className={cn(card && "md:!right-[392px]")}>
          <button
            type="button"
            data-testid="show-all"
            onClick={() => {
              fitView({ padding: 0.08, duration: 250 });
              setOpening("all");
            }}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-[13px] font-medium shadow-sm hover:bg-raised"
          >
            <Maximize2 className="h-3.5 w-3.5" aria-hidden /> Show all
          </button>
          <button
            type="button"
            data-testid="minimap-toggle"
            aria-pressed={map}
            onClick={() => setMap((v) => !v)}
            className="ml-2 inline-flex h-8 items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 text-[13px] font-medium shadow-sm hover:bg-raised max-md:hidden"
          >
            Map
          </button>
        </Panel>
        <Controls showInteractive={false} position="bottom-right" className={cn("max-md:!hidden", card && "md:!right-[392px]")} />
        {map && <MiniMap
          pannable
          zoomable
          position="bottom-left"
          style={{ width: 150, height: 96 }}
          className="!rounded-lg !border !border-line !bg-surface/90"
          ariaLabel="Minimap"
          nodeColor={(n) => {
            if (n.type === "zone") return "transparent";
            const name = n.type === "resource" ? ((n.data as ResData).constraint ? "state-blocked" : "muted") : `state-${(n.data as CardData).look.t === "human" ? "human" : (n.data as CardData).look.t === "void" ? "void" : (n.data as CardData).look.t}`;
            const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
            return raw ? `hsl(${raw})` : "#888";
          }}
          nodeStrokeWidth={0}
        />}
      </ReactFlow>
      {card && (
        <SidePanel
          card={card}
          m={m}
          onClose={() => {
            setSelected(null);
            setPicked(null);
          }}
        />
      )}
    </div>
  );
}

// Roadmap: time runs left to right; each zone is a lane and each item a bar from when it opened to when it finished
// (or now).
function Roadmap({ m }: { m: FlowModel }) {
  const now = Date.parse(m.now);
  const hour = 3600_000;
  // A phone gets a window that fits its width (the last day and a half to the next twelve hours), with each title on
  // its own line under its bar; a wide screen gets the whole history with titles beside the bars. Nothing scrolls
  // sideways, so no title is ever hidden under the lane names.
  const narrow = typeof window !== "undefined" && window.matchMedia(PHONE).matches;
  const dated = m.cards.filter((c) => c.opened_at);
  const expiries = m.cards.filter((c) => c.expires && c.state !== "done").map((c) => Date.parse(c.expires!));
  const start = narrow ? now - 36 * hour : Math.min(now - 24 * hour, ...dated.map((c) => Date.parse(c.opened_at!)));
  const end = narrow ? now + 12 * hour : Math.max(now + 12 * hour, ...expiries.map((t) => t + 6 * hour));
  const frac = (t: number) => Math.min(1, Math.max(0, (t - start) / (end - start)));
  const pos = (t: number) => `${frac(t) * 100}%`;
  const ticks: number[] = [];
  if (narrow) {
    for (let t = Math.ceil(start / (12 * hour)) * 12 * hour; t <= end; t += 12 * hour) ticks.push(t);
  } else {
    for (let d = new Date(start); d.getTime() <= end; d.setHours(24, 0, 0, 0)) ticks.push(new Date(d).setHours(0, 0, 0, 0));
  }
  const tickLabel = (t: number) =>
    narrow
      ? `${new Date(t).toLocaleDateString(undefined, { weekday: "short" })} ${String(new Date(t).getHours()).padStart(2, "0")}:00`
      : new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const lanes = m.zones
    .map((z) => ({ z, cards: dated.filter((c) => c.zone === z.id).sort((x, y) => (x.opened_at ?? "").localeCompare(y.opened_at ?? "")) }))
    .filter((l) => l.cards.length);
  const undated = m.cards.filter((c) => !c.opened_at);
  const laneW = narrow ? "w-24" : "w-44";
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="roadmap" data-window={narrow ? "36h" : "all"}>
      <p className="px-4 pt-3 text-[13px] text-muted">
        Each item runs from when it opened to when it finished, or to <span className="font-medium text-state-blocked">now</span>. An{" "}
        <span className="inline-block h-3 w-0.5 translate-y-0.5 bg-state-human" aria-hidden /> amber tick is when a decision expires.
        {narrow && " The last day and a half; an arrow marks an item that started earlier."}
      </p>
      <div className="min-h-0 flex-1 overflow-y-auto p-4 pt-2">
        <div className="rounded-zone border border-line bg-surface">
          <div className={cn("relative h-8 border-b border-line text-[11px] text-muted", narrow ? "ml-24" : "ml-44")} data-testid="roadmap-axis">
            {ticks.filter((t) => t >= start && t <= end).map((t) => (
              <span key={t} className="absolute top-2 -translate-x-1/2 whitespace-nowrap" style={{ left: pos(t) }}>
                {tickLabel(t)}
              </span>
            ))}
            <span className="absolute inset-y-0 w-px bg-state-blocked" style={{ left: pos(now) }} aria-hidden />
            <span className="absolute -top-0.5 -translate-x-1/2 rounded bg-state-blocked px-1 text-[10px] font-semibold text-surface" style={{ left: pos(now) }}>now</span>
          </div>
          {lanes.map(({ z, cards }) => (
            <div key={z.id} className="flex border-b border-line last:border-b-0" data-testid={`lane-${z.id}`}>
              <div className={cn("shrink-0 border-r border-line px-3 py-2 text-[13px] font-semibold", laneW, narrow && "px-2 text-[12px]")}>
                <span className={cn("break-words", z.source === "owner" && "font-mono")}>{z.label}</span>
                <span className="block text-[11px] font-normal text-muted">{z.source === "direction" ? "direction" : z.source === "owner" ? "owner role" : "no direction or owner"}</span>
              </div>
              <div className="relative min-w-0 flex-1 overflow-hidden py-1.5">
                <span className="absolute inset-y-0 w-px bg-state-blocked/50" style={{ left: pos(now) }} aria-hidden />
                {cards.map((c) => {
                  const look = lookOf(c, m.waits.filter((w) => w.card === c.id));
                  const s0 = Date.parse(c.opened_at!);
                  const e0 = c.state === "done" && c.updated_at ? Date.parse(c.updated_at) : now;
                  const earlier = s0 < start;
                  const left = frac(s0);
                  const late = !narrow && left > 0.55; // on a wide screen a bar that starts late is labelled on its left
                  return (
                    <div key={c.id} className={cn("relative", narrow ? "h-10" : "h-7")} title={`${c.title}: ${look.label}`} data-testid="roadmap-item">
                      <div
                        className={cn("absolute top-2.5 h-2 rounded-full", tone(look.t).dot, c.state === "done" && "opacity-60", earlier && "rounded-l-none")}
                        style={{ left: pos(s0), width: `max(0.5rem, calc(${pos(e0)} - ${pos(s0)}))` }}
                      />
                      {earlier && <span className={cn("absolute left-0 top-1 text-[11px] leading-none", tone(look.t).text)} aria-label="started earlier">◂</span>}
                      {c.expires && c.state !== "done" && Date.parse(c.expires) <= end && (
                        <span className="absolute top-1.5 h-4 w-0.5 bg-state-human" style={{ left: pos(Date.parse(c.expires)) }} title={`expires ${clock(c.expires, m.now)}`} aria-hidden />
                      )}
                      <span
                        data-testid="roadmap-title"
                        className={cn(
                          "absolute truncate whitespace-nowrap text-[12.5px] leading-5",
                          narrow ? "top-[18px] max-w-full" : "top-1 max-w-[46%]",
                          look.t === "human" && "font-semibold",
                          c.state === "done" && "text-muted",
                        )}
                        style={
                          narrow
                            ? { left: `min(${pos(s0)}, 40%)`, right: 0 }
                            : late
                              ? { right: `calc(100% - ${pos(s0)} + 8px)` }
                              : { left: `calc(${pos(e0)} + 8px)` }
                        }
                      >
                        {c.title}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
        {undated.length > 0 && <p className="mt-2 text-[13px] text-muted">{plural(undated.length, "item")} with no date are not drawn.</p>}
      </div>
    </div>
  );
}

// Calendar window: what just finished, what is happening now, and what comes next (the next day).
function Calendar({ m }: { m: FlowModel }) {
  const now = Date.parse(m.now);
  const day = 86400_000;
  const waitsOf = (id: string) => m.waits.filter((w) => w.card === id);
  const done = m.cards.filter((c) => c.state === "done" && c.updated_at && now - Date.parse(c.updated_at) <= day);
  const current = m.cards.filter((c) => c.state !== "done" && (c.state === "in_review" || waitsOf(c.id).length > 0));
  const nextCards = m.cards.filter((c) => c.state !== "done" && !current.includes(c) && (c.state === "ready" || (c.expires && Date.parse(c.expires) - now <= day)));
  const nextJobs = m.schedules.filter((s) => s.next_due && Date.parse(s.next_due) - now <= day && Date.parse(s.next_due) >= now - day);
  const Col = ({ title, children, count, testid }: { title: string; children: React.ReactNode; count: number; testid: string }) => (
    <section className="flex min-w-0 flex-col gap-2" data-testid={testid}>
      <h2 className="text-[15px] font-semibold">{title} <span className="text-muted">{count}</span></h2>
      {children}
    </section>
  );
  const Item = ({ c }: { c: FlowCard }) => {
    const look = lookOf(c, waitsOf(c.id));
    return (
      <div className={cn("rounded-ui border border-l-[3px] border-line bg-surface px-3 py-2", tone(look.t).edge)}>
        <p className="text-[14px] font-medium leading-snug">{c.title}</p>
        <p className="mt-0.5 text-[12px] text-muted">{look.label}{c.owner_role ? ` · ${c.owner_role}` : ""}{c.expires ? ` · expires ${clock(c.expires, m.now)}` : ""}</p>
      </div>
    );
  };
  return (
    <div className="min-h-0 flex-1 overflow-auto p-4" data-testid="calendar">
      <div className="mx-auto grid max-w-[1400px] gap-5 md:grid-cols-3">
        <Col title="Just done (24 h)" count={done.length} testid="cal-done">
          {done.length ? done.map((c) => <Item key={c.id} c={c} />) : <Empty>Nothing finished in the last day.</Empty>}
        </Col>
        <Col title="Now" count={current.length} testid="cal-now">
          {current.map((c) => <Item key={c.id} c={c} />)}
        </Col>
        <Col title="Next (24 h)" count={nextCards.length + nextJobs.length} testid="cal-next">
          {[...nextJobs].sort((a, b) => (a.next_due ?? "").localeCompare(b.next_due ?? "")).map((s) => (
            <div key={s.id} className="rounded-ui border border-l-[3px] border-line border-l-line bg-surface px-3 py-2">
              <p className="font-mono text-[14px] font-medium">{s.name}</p>
              <p className="mt-0.5 text-[12px] text-muted">scheduled job · due {clock(s.next_due, m.now)}</p>
            </div>
          ))}
          {[...nextCards].sort((a, b) => (a.expires ?? "9").localeCompare(b.expires ?? "9")).map((c) => <Item key={c.id} c={c} />)}
        </Col>
      </div>
    </div>
  );
}

export default function FlowView({ params }: { params: URLSearchParams }) {
  const q = useQuery({ queryKey: ["flow"], queryFn: api.flow, refetchInterval: 30_000 });
  const raw = params.get("mode");
  const mode: Mode = raw === "you" || raw === "roadmap" || raw === "calendar" ? raw : "graph";
  const overlay = params.get("overlay") !== "0";
  const hideDone = params.get("done") === "0";
  const m = q.data;
  const body = useMemo(() => {
    if (!m) return null;
    if (mode === "roadmap") return <Roadmap m={m} />;
    if (mode === "calendar") return <Calendar m={m} />;
    return (
      <ReactFlowProvider>
        <Canvas m={m} mode={mode} overlay={overlay && mode === "graph"} hideDone={hideDone} />
      </ReactFlowProvider>
    );
  }, [m, mode, overlay, hideDone]);
  if (q.isPending) return <div className="p-4"><Loading what="the flow" /></div>;
  if (q.isError || !m) return <div className="p-4"><Problem error={q.error} /></div>;
  return (
    <div className="flex min-h-0 flex-1 flex-col" data-testid="view-flow" data-mode={mode}>
      <Toolbar mode={mode} overlay={overlay} hideDone={hideDone} params={params} m={m} />
      {body}
    </div>
  );
}

export { CARD_W };
