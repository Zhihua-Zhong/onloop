import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { FlowCard, FlowModel, FlowResource, FlowWait, FlowZone } from "./api";
import type { Tone } from "@/components/kit";
import { reason } from "./format";

// The Flow canvas over the ledger (blueprint section 4.2): each zone is a direction, an owner role or "unassigned";
// each item is a card; the locks, queues and the human the cards wait on are nodes in their own zone.
//
// No line crosses a zone it does not connect. A card says what it waits on with a chip ("→ merge-check"); the lines
// from a resource to its cards, and a dependency between two zones, are "traces": drawn only while their resource or
// one of their cards is selected or hovered (`traced`). Lines inside a zone are laid out by ELK with the zone and stay
// in it. Routing every line around the zones was weighed and set aside: the waiting cards sit in many zones, so any
// route from the resource zone to them passes other zones, and routing around them would mean giving up the zone
// packing the canvas is built on.

export const CARD_W = 236;
export const CARD_H = 88;
export const CARD_H_DONE = 44;
export const RES_W = 220;
export const RES_H = 86;
export const RESOURCE_ZONE = "zone:resources";
export const zoneNodeId = (id: string) => `zone:${id}`;
export const resNodeId = (id: string) => `res:${id}`;

export type Look = { label: string; t: Tone };

export interface Chip { to: string; label: string; t: Tone }
export interface CardData extends Record<string, unknown> {
  card: FlowCard;
  look: Look;
  waits: FlowWait[];
  chips: Chip[];
  dim: boolean;
  hot: boolean;
}
// An edge drawn only while one of its ends (or the resource it belongs to) is in focus.
export interface TraceData extends Record<string, unknown> { trace: true; ends: string[] }
export interface ZoneData extends Record<string, unknown> {
  zone: FlowZone;
  title: string;
  waiting: number;
  blocked: number;
  open: number;
  total: number;
  resources: boolean;
  dim: boolean;
}
export interface ResData extends Record<string, unknown> {
  res: FlowResource;
  constraint: boolean;
  dim: boolean;
}

// How a card reads: waiting on you comes first, then blocked, then waiting on something else.
export function lookOf(card: FlowCard, waits: FlowWait[]): Look {
  if (card.state === "done") return { label: "Done", t: "done" };
  if (waits.some((w) => w.on_kind === "human")) return { label: "Waiting on you", t: "human" };
  if (card.state === "blocked") {
    const w = waits[0];
    return { label: w && w.reason !== "conflict" ? `Blocked: ${reason(w.reason)}` : "Blocked", t: "blocked" };
  }
  const w = waits[0];
  if (w) return { label: w.reason === "running" ? "Queued" : "Waiting", t: "external" };
  if (card.state === "in_review") return { label: "In review", t: "open" };
  if (card.state === "ready") return { label: "Ready", t: "open" };
  return { label: "Open", t: "open" };
}

// What a card waits on, as chips: the resource or "you" it would otherwise be linked to by a long line.
export function chipsOf(waits: FlowWait[], constraint: string | undefined): Chip[] {
  const out: Chip[] = [];
  for (const w of waits) {
    if (w.on_kind !== "resource" && w.on_kind !== "human") continue;
    const ref = w.on_kind === "human" ? "human" : w.on_ref;
    if (out.some((c) => c.to === resNodeId(ref))) continue;
    const t: Tone = w.on_kind === "human" ? "human" : ref === constraint ? "blocked" : w.reason === "conflict" ? "blocked" : "external";
    out.push({ to: resNodeId(ref), label: w.on_kind === "human" ? "you" : ref, t });
  }
  return out;
}

// The edges to draw now: every edge that is not a trace, and the traces with an end in focus.
export function traced(edges: Edge[], focus: string | null): Edge[] {
  return edges.map((e) => {
    const d = e.data as TraceData | undefined;
    if (!d?.trace) return e;
    const on = !!focus && d.ends.includes(focus);
    return e.hidden === !on ? e : { ...e, hidden: !on };
  });
}

export function wantsHuman(card: FlowCard, waits: FlowWait[]): boolean {
  return waits.some((w) => w.card === card.id && w.on_kind === "human");
}

let elkPromise: Promise<import("elkjs/lib/elk-api").ELK> | null = null;
const getElk = () => (elkPromise ??= import("elkjs/lib/elk.bundled.js").then((m) => new m.default()));

export interface LayoutOpts {
  overlay: boolean;
  onlyHuman: boolean;
  hideDone: boolean;
}

export async function layout(m: FlowModel, opts: LayoutOpts): Promise<{ nodes: Node[]; edges: Edge[]; zoneOrder: string[] }> {
  const waitsOf = new Map<string, FlowWait[]>();
  for (const w of m.waits) if (w.card) waitsOf.set(w.card, [...(waitsOf.get(w.card) ?? []), w]);
  const hot = new Set<string>(m.constraint ? [...m.constraint.cards, resNodeId(m.constraint.node)] : []);
  const dimOn = opts.overlay && !!m.constraint;
  let cards = m.cards;
  if (opts.onlyHuman) cards = cards.filter((c) => (waitsOf.get(c.id) ?? []).some((w) => w.on_kind === "human"));
  if (opts.hideDone) cards = cards.filter((c) => c.state !== "done");
  const shown = new Set(cards.map((c) => c.id));
  const resources = m.resources.filter((r) => !opts.onlyHuman || r.kind === "human");
  const zones = m.zones.filter((z) => cards.some((c) => c.zone === z.id));

  const graph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "rectpacking",
      "elk.spacing.nodeNode": "36",
      "elk.padding": "[top=24,left=24,bottom=24,right=24]",
      "elk.rectpacking.widthApproximation.targetWidth": "1.7",
      "elk.contentAlignment": "V_TOP H_LEFT",
    },
    children: [
      ...(resources.length
        ? [
            {
              id: RESOURCE_ZONE,
              layoutOptions: { "elk.algorithm": "box", "elk.padding": "[top=62,left=20,bottom=20,right=20]", "elk.spacing.nodeNode": "16", "elk.box.packingMode": "GROUP_DEC" },
              children: resources.map((r) => ({ id: resNodeId(r.id), width: RES_W, height: RES_H })),
            },
          ]
        : []),
      ...zones.map((z) => {
        const inside = cards.filter((c) => c.zone === z.id);
        const ids = new Set(inside.map((c) => c.id));
        return {
          id: zoneNodeId(z.id),
          layoutOptions: {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.padding": "[top=62,left=20,bottom=20,right=20]",
            "elk.spacing.nodeNode": "18",
            "elk.layered.spacing.nodeNodeBetweenLayers": "48",
          },
          children: inside.map((c) => ({ id: c.id, width: CARD_W, height: c.state === "done" ? CARD_H_DONE : CARD_H })),
          edges: m.edges.filter((e) => ids.has(e.source) && ids.has(e.target)).map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
        };
      }),
    ],
  };
  type Out = { children?: Array<{ id: string; x: number; y: number; width: number; height: number; children?: Array<{ id: string; x: number; y: number }> }> };
  const out = (await (await getElk()).layout(graph)) as Out;

  const byCard = new Map(m.cards.map((c) => [c.id, c] as const));
  const byRes = new Map(m.resources.map((r) => [r.id, r] as const));
  const byZone = new Map(m.zones.map((z) => [z.id, z] as const));
  const nodes: Node[] = [];
  const placed = [...(out.children ?? [])].sort((a, b) => a.y - b.y || a.x - b.x);
  const zoneOrder: string[] = [];
  for (const z of placed) {
    const isRes = z.id === RESOURCE_ZONE;
    const zone: FlowZone = isRes ? { id: "resources", label: "Locks, queues and you", source: "none", cards: resources.length } : byZone.get(z.id.slice(5))!;
    const members = isRes ? [] : cards.filter((c) => c.zone === zone.id);
    const looks = members.map((c) => lookOf(c, waitsOf.get(c.id) ?? []));
    if (!isRes) zoneOrder.push(zone.id);
    const data: ZoneData = {
      zone,
      title: zone.label,
      resources: isRes,
      total: members.length,
      waiting: looks.filter((l) => l.t === "human").length,
      blocked: looks.filter((l) => l.t === "blocked").length,
      open: members.filter((c) => c.state !== "done").length,
      // What waits on the human is never dimmed: the overlay shows the constraint, it does not hide your own queue.
      dim: dimOn && !isRes && !members.some((c) => hot.has(c.id) || (waitsOf.get(c.id) ?? []).some((w) => w.on_kind === "human")),
    };
    nodes.push({ id: z.id, type: "zone", position: { x: z.x, y: z.y }, data, style: { width: z.width, height: z.height }, draggable: false, selectable: false, zIndex: 0 });
    for (const c of z.children ?? []) {
      if (isRes) {
        const res = byRes.get(c.id.slice(4))!;
        const isC = m.constraint?.node === res.id;
        const dim = dimOn && !isC && res.kind !== "human";
        nodes.push({ id: c.id, type: "resource", parentId: z.id, position: { x: c.x, y: c.y }, data: { res, constraint: isC, dim } satisfies ResData, zIndex: 10, draggable: false });
      } else {
        const card = byCard.get(c.id)!;
        const ws = waitsOf.get(card.id) ?? [];
        nodes.push({
          id: c.id,
          type: "card",
          parentId: z.id,
          position: { x: c.x, y: c.y },
          data: {
            card,
            look: lookOf(card, ws),
            waits: ws,
            chips: chipsOf(ws, m.constraint?.node),
            dim: dimOn && !hot.has(card.id) && !ws.some((w) => w.on_kind === "human"),
            hot: dimOn && hot.has(card.id),
          } satisfies CardData,
          zIndex: 10,
          draggable: false,
        });
      }
    }
  }

  const zoneOfCard = new Map(cards.map((c) => [c.id, c.zone] as const));
  const edges: Edge[] = [];
  for (const e of m.edges) {
    if (!shown.has(e.source) || !shown.has(e.target)) continue;
    const crossing = zoneOfCard.get(e.source) !== zoneOfCard.get(e.target);
    const lit = dimOn && hot.has(e.source) && hot.has(e.target);
    edges.push({
      id: e.id,
      source: e.source,
      target: e.target,
      hidden: crossing, // a dependency between two zones is a trace
      ...(crossing ? { data: { trace: true, ends: [e.source, e.target] } satisfies TraceData } : {}),
      type: crossing ? "default" : "smoothstep",
      zIndex: 1,
      className: e.kind === "depends" ? "edge-depends" : "edge-parent",
      style: { strokeDasharray: e.kind === "depends" ? "6 4" : undefined, strokeWidth: crossing ? 1.2 : 1.5, opacity: dimOn && !lit ? 0.18 : crossing ? 0.55 : 1 },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    });
  }
  // A card that waits on a lock, a queue or the human is linked to it: the line ends at the thing it waits on.
  for (const w of m.waits) {
    if (!w.card || !shown.has(w.card)) continue;
    if (w.on_kind !== "resource" && w.on_kind !== "human") continue;
    const target = resNodeId(w.on_kind === "human" ? "human" : w.on_ref);
    if (!resources.some((r) => resNodeId(r.id) === target)) continue;
    const isC = m.constraint?.node === (w.on_kind === "human" ? "human" : w.on_ref);
    // Drawn from the resource's right side to the card's left side: the resources sit at the left of the canvas.
    edges.push({
      id: `wait:${w.id}`,
      source: target,
      target: w.card,
      hidden: true, // a trace: the card's chip names the resource; the line shows while either is in focus
      data: { trace: true, ends: [target, w.card] } satisfies TraceData,
      type: "default",
      zIndex: isC ? 3 : 2,
      className: w.on_kind === "human" ? "edge-wait-human" : isC ? "edge-wait-constraint" : "edge-wait",
      // The lines to "You" are many and long; on the full graph they are quiet, and full strength in "Waiting on you".
      style: { strokeWidth: isC ? 2 : 1.4, opacity: 0.85 },
    });
  }
  return { nodes, edges, zoneOrder };
}
