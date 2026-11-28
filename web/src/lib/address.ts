// Where each view lives in 0.7.0's shell (plan, section 1): the main pages at the top (`#/`, `#/agents`, `#/timeline`),
// history and operations one click down under Details (`#/details/map`, `#/details/audit?kind=pr.merged`). A view is
// still named by its 0.6.0 key inside the code, so the views link to each other with `href(view)` as before. Imports
// nothing, so node's test runner reads it as is.
export const VIEWS = ["now", "replay", "map", "outcome", "team", "conversation", "schedules", "audit", "rules"] as const;
export type View = (typeof VIEWS)[number];
export const VIEW_LABEL: Record<View, string> = { now: "Now", replay: "Replay", map: "Map", outcome: "Outcome", team: "Team", conversation: "Conversation", schedules: "Schedules", audit: "Audit", rules: "Rule alerts" };

/** Each view's address, after `#/`. */
export const ADDRESS: Record<View, string> = {
  now: "",
  team: "agents",
  replay: "timeline",
  map: "details/map",
  outcome: "details/outcome",
  conversation: "details/conversation",
  schedules: "details/schedules",
  audit: "details/audit",
  rules: "details/rules",
};
export const DETAILS: readonly View[] = ["map", "outcome", "conversation", "schedules", "audit", "rules"];

// `path` is what follows the view's address: the map's levels, `#/details/map/<goal>/<stage>` (decision 0018).
export interface Route { view: View; params: URLSearchParams; path: string[] }

export function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [where, query = ""] = raw.split("?");
  const parts = where.split("/").filter(Boolean).map((s) => decodeURIComponent(s));
  const params = new URLSearchParams(query);
  if (parts[0] === "details" && parts[1]) {
    const view = VIEWS.find((v) => ADDRESS[v] === `details/${parts[1]}`);
    if (view) return { view, params, path: parts.slice(2) };
  }
  const top = VIEWS.find((v) => ADDRESS[v] !== "" && !ADDRESS[v].startsWith("details/") && ADDRESS[v] === parts[0]);
  if (top) return { view: top, params, path: parts.slice(1) };
  return { view: "now", params, path: [] };
}

export function href(view: View, params?: Record<string, string | undefined>, path: string[] = []): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v) q.set(k, v);
  const s = q.toString();
  const where = [ADDRESS[view], ...path.map((p) => encodeURIComponent(p))].filter(Boolean).join("/");
  return `#/${where}${s ? `?${s}` : ""}`;
}
