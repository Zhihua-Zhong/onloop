// The map (decision 0018): its model as GET /api/map serves it, and the rules the page keeps. Pure, so node:test can
// hold them without a browser.

export type Colour = "red" | "amber" | "moving" | "grey" | "done";

export interface MapLeaf {
  id: string;
  title: string;
  colour: Colour;
  shape: string;
  stage?: string;
  who?: string;
  why?: string;
  age_s?: number | null;
  lower_bound?: boolean;
  reported_by_team?: boolean;
  task_backed?: boolean;
}

export interface MapStage { stage: string; reported: boolean; count: number | null; colour: Colour | null }

export interface MapBlock {
  id: string;
  kind: "checklist" | "feedback" | "changes";
  title: string;
  colour: Colour;
  shape: string;
  label: string;
  bar: { done: number | null; of: number; label: string; as_of?: string | null };
  tally: Record<Colour, number>;
  tally_text: string; // the server's words, so the agreement test holds the words as well as the numbers
  reason: { child: string; title: string; text: string; lower_bound: boolean } | null;
  leaves: MapLeaf[];
  reported_by_team?: boolean;
  stages?: MapStage[];
}

export interface MapWait {
  holder: string;
  kind: string;
  count: number;
  oldest_s: number;
  lower_bound: boolean;
  oldest_ref?: string | null;
  oldest_text?: string | null;
  age_text: string;
  note: string | null;
}

export interface MapModel {
  now: string;
  level: number;
  path: string[];
  data_as_of: string | null;
  signals: {
    waiting_on_you: number;
    stuck: number;
    team_quiet: {
      seconds: number;
      text: string;
      as_of: string;
      ongoing: boolean;
      team_activity: { turns: number; roles: number; hours: number };
    } | null;
  };
  blocks: MapBlock[];
  waits: MapWait[];
  thresholds_s: Record<string, number>;
  legend: { colour: Colour; shape: string; label: string }[];
}

// The kit's tones for the map's colours: §4a's validated palette. "moving" is §4a's blue (Q27).
export const TONE_OF: Record<Colour, "blocked" | "human" | "open" | "void" | "done"> = {
  red: "blocked",
  amber: "human",
  moving: "open",
  grey: "void",
  done: "done",
};

// Where the camera is, and what a person did. Only an explicit move changes the level: a geometric zoom never does
// (Q23), so zooming in over a block the person did not choose never drops them into it.
export type MapMove = { kind: "zoom"; zoom: number } | { kind: "open"; block: string } | { kind: "up" };

export const OPEN_HINT_ZOOM = 1.6;

export function nextPath(path: string[], move: MapMove): string[] {
  switch (move.kind) {
    case "zoom":
      return path;
    case "open":
      return [...path, move.block];
    case "up":
      return path.slice(0, -1); // at zoom 0 there is nowhere up to go: Escape does nothing
  }
}

export function showsOpenHint(zoom: number): boolean {
  return zoom >= OPEN_HINT_ZOOM;
}

// The phone's one column (the overview plan's §4): batches, then checklists, then the changes.
const PHONE_ORDER: Record<MapBlock["kind"], number> = { feedback: 0, checklist: 1, changes: 2 };
export function phoneOrder(blocks: MapBlock[]): MapBlock[] {
  return [...blocks].sort((a, b) => PHONE_ORDER[a.kind] - PHONE_ORDER[b.kind]);
}

// The legend's thresholds, in plain words: "given 2 d · declared 3 d".
export function thresholdsText(t: Record<string, number>, fmt: (s: number) => string): string {
  const names: Record<string, string> = { given: "given", declared: "a team's stage", checklist: "checklist item", pr: "change", wait: "wait" };
  return Object.entries(t)
    .map(([k, v]) => `${names[k] ?? k} ${fmt(v)}`)
    .join(" · ");
}

// Zoom 0 opens at scale 1, never fitted smaller: the blocks' text is set at 12 px and up, and a glance must read it
// (the lead's fix 1). The blocks share the canvas's width instead, as many a row as fit at their least width.
export const OPENING_ZOOM = 1;
export const BLOCK_MIN_W = 320;
// The most a block widens to: past 1920 three blocks still fill the row (2560's canvas gives each about 700 px).
export const BLOCK_MAX_W = 760;
export const BLOCK_H = 236;
export const GAP = 24;
export const PAD = 16;
// How often the canvas checks its own width, besides the ResizeObserver: a page that draws no frames (a background
// tab, a hidden pane) gets neither observer callbacks nor resize events, yet its timers still run.
export const WIDTH_CHECK_MS = 1000;

export interface GridLayout {
  zoom: number;
  perRow: number;
  blockW: number;
  positions: { x: number; y: number }[];
}

export function gridLayout(n: number, width: number): GridLayout {
  const room = Math.max(BLOCK_MIN_W, width - 2 * PAD);
  const fit = Math.floor((room + GAP) / (BLOCK_MIN_W + GAP));
  const perRow = Math.max(1, Math.min(n || 1, fit));
  const blockW = Math.min(BLOCK_MAX_W, Math.floor((room - (perRow - 1) * GAP) / perRow));
  const positions = Array.from({ length: n }, (_, i) => ({
    x: PAD + (i % perRow) * (blockW + GAP),
    y: PAD + Math.floor(i / perRow) * (BLOCK_H + GAP),
  }));
  return { zoom: OPENING_ZOOM, perRow, blockW, positions };
}

// Zoom 1: one goal's stages. Zoom 2: who holds what in one stage (GET /api/map?path=<goal>[/<stage>]).
export interface MapChecks { passed: number; skipped: number; failed: string[]; running: string[]; text: string }
export interface MapStageLeaf extends MapLeaf { checks?: MapChecks | null }
export interface MapStageEntry {
  stage: string;
  reported: boolean;
  count: number | null;
  colour: Colour | null;
  shape: string | null;
  label: string | null;
  tally_text: string;
  reason: MapBlock["reason"];
  oldest: { id: string; age_s: number | null; age_text: string; lower_bound: boolean } | null;
  leaves: MapStageLeaf[];
}
export interface MapLevelBase {
  now: string;
  level: number;
  path: string[];
  data_as_of: string | null;
  signals: MapModel["signals"];
  thresholds_s: Record<string, number>;
  legend: MapModel["legend"];
}
export interface Zoom1Model extends MapLevelBase {
  block: Omit<MapBlock, "leaves">;
  stages: MapStageEntry[];
}
export interface MapHolderItem extends MapStageLeaf {
  age_text: string | null;
  link: { kind: string; view: "now" | "audit"; ref: string };
}
export interface MapHolder {
  holder: string;
  kind: "you" | "marked" | "team" | "role";
  count: number;
  colour: Colour;
  shape: string;
  tally_text: string;
  oldest_s: number;
  items: MapHolderItem[];
}
export interface Zoom2Model extends MapLevelBase {
  block: Omit<MapBlock, "leaves">;
  stage: Omit<MapStageEntry, "leaves">;
  holders: MapHolder[];
}

// The levels that are built (decision 0018): 0, 1 and 2. Zoom 3 (a role's turns) and 4 (a turn's tool calls) are
// reserved in the URLs and come with the tool events.
export const BUILT_LEVELS = 2;
export function isReserved(path: string[]): boolean {
  return path.length > BUILT_LEVELS;
}

// The same grid for cards of different heights (zoom 1's stages, zoom 2's holders): rows as many as fit, each row as
// tall as its tallest card, at scale 1.
export function cardLayout(heights: number[], width: number): GridLayout & { heights: number[] } {
  const g = gridLayout(heights.length, width);
  const positions: { x: number; y: number }[] = [];
  let y = PAD;
  for (let r = 0; r * g.perRow < heights.length; r++) {
    const row = heights.slice(r * g.perRow, (r + 1) * g.perRow);
    row.forEach((_, c) => positions.push({ x: PAD + c * (g.blockW + GAP), y }));
    y += Math.max(...row) + GAP;
  }
  return { ...g, positions, heights };
}

// Opening a block (the overview plan's §12.5). The level opens at once, on the first click, never behind a timer: a
// page in a background tab throttles timers, and a double-click's own events must not undo it. A double-click is
// click (detail 1), click (detail 2), dblclick: the first opens, and the rest of the gesture, within OPEN_GUARD_MS,
// is ignored, even when it lands on the new level's cards. One opener serves the whole page, across levels. The
// "camera into the block" is the new level's entry: about 250 ms, and none with prefers-reduced-motion (index.css).
export const OPEN_MOVE_MS = 250;
export const OPEN_GUARD_MS = 500;
export function openMoveMs(reducedMotion: boolean): number {
  return reducedMotion ? 0 : OPEN_MOVE_MS;
}
export function createOpener(now: () => number = () => Date.now()): (open: () => void) => boolean {
  let last = -Infinity;
  return (open) => {
    const t = now();
    if (t - last < OPEN_GUARD_MS) return false;
    last = t;
    open();
    return true;
  };
}
export const opener = createOpener();

// Enter and Space open a focused block, as a button's keys do.
export function opensOn(key: string): boolean {
  return key === "Enter" || key === " ";
}

// The chain between a goal's stages: an edge joins two stages only when they sit in the same row. Where a row wraps,
// the last stage says what comes next instead of an edge crossing the canvas.
export function chainLinks(positions: { x: number; y: number }[]): { edges: [number, number][]; wraps: number[] } {
  const edges: [number, number][] = [];
  const wraps: number[] = [];
  for (let i = 0; i + 1 < positions.length; i++) {
    if (positions[i].y === positions[i + 1].y) edges.push([i, i + 1]);
    else wraps.push(i);
  }
  return { edges, wraps };
}
