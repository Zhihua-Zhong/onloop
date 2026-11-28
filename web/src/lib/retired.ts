// Old links, and where what they showed lives now. Imports nothing, so node's test runner reads it as is.
//
// 0.6.0 retired Flow and Activity (decision 0019). 0.7.0's shell (plan, section 1) gives every view a new address: the
// first screen is Home (`#/`), 0.6.0's Now and its task list are Decisions, Team is Agents, Replay is the Timeline, and
// the rest live under Details. Until 0.7.0's Agents and Timeline land, those show 0.6.0's Team and Replay.

/** The old first segment of a link, and the address that shows it now (before its path and query). */
export const RETIRED: Record<string, string> = {
  now: "decisions",
  tasks: "decisions",
  team: "agents",
  replay: "timeline",
  activity: "timeline",
  flow: "details/map",
  map: "details/map",
  outcome: "details/outcome",
  conversation: "details/conversation",
  schedules: "details/schedules",
  audit: "details/audit",
};

// An old link rewritten to its new address, keeping what follows it (#/flow?x=1 -> #/details/map?x=1,
// #/map/changes/opened -> #/details/map/changes/opened, #/now -> #/), so the address bar and a link copied from it
// name the view on screen; null when the hash is already a current address.
export function retiredTo(hash: string): string | null {
  const m = /^#\/?([^/?]*)(.*)$/.exec(hash);
  if (!m || !(m[1] in RETIRED)) return null;
  const to = RETIRED[m[1]];
  const rest = m[2];
  if (to === "") return `#/${rest.replace(/^\//, "")}`;
  return `#/${to}${rest}`;
}
