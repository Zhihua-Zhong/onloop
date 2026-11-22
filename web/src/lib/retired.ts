// The views decision 0019 retired, and where their parts moved. Imports nothing, so node's test runner reads it as is.
export const RETIRED: Record<string, "map" | "replay"> = { flow: "map", activity: "replay" };

// A retired view's link rewritten to the view its parts moved to (#/flow?x=1 -> #/map?x=1), so the address bar and a
// link copied from it name the view on screen; null when the hash names no retired view.
export function retiredTo(hash: string): string | null {
  const m = /^#\/?([^/?]*)(.*)$/.exec(hash);
  const to = m ? RETIRED[m[1]] : undefined;
  return to ? `#/${to}${m![2]}` : null;
}
