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
    longest_without_a_move: {
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

// A block's tally in words, worst first, leaving out what is zero: "2 wait on you · 5 moving".
export function tallyText(b: MapBlock): string {
  const words: [Colour, string, string][] = [
    ["red", "jammed", "jammed"],
    ["amber", "waits on you", "wait on you"],
    ["moving", "moving", "moving"],
    ["grey", "not started", "not started"],
  ];
  const parts = words.filter(([c]) => b.tally[c]).map(([c, one, many]) => `${b.tally[c]} ${b.tally[c] === 1 ? one : many}`);
  return parts.join(" · ") || (b.leaves.length ? "all done" : "nothing in it yet");
}

// The legend's thresholds, in plain words: "given 2 d · declared 3 d".
export function thresholdsText(t: Record<string, number>, fmt: (s: number) => string): string {
  const names: Record<string, string> = { given: "given", declared: "a team's stage", checklist: "checklist item", pr: "change", wait: "wait" };
  return Object.entries(t)
    .map(([k, v]) => `${names[k] ?? k} ${fmt(v)}`)
    .join(" · ");
}

// The 1440 grid: three blocks a row, in the order the API gives (by origin), at fixed sizes.
export const BLOCK_W = 380;
export const BLOCK_H = 236;
export const GAP = 28;
export function gridPosition(i: number, perRow = 3): { x: number; y: number } {
  return { x: (i % perRow) * (BLOCK_W + GAP), y: Math.floor(i / perRow) * (BLOCK_H + GAP) };
}
