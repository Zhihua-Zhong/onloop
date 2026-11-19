import { useEffect, useState } from "react";

// The views are hash routes (#/now, #/flow, #/audit?kind=pr.merged), so a reload or a shared link opens the same view
// and onloopd serves one index.html for all of them.
export const VIEWS = ["now", "map", "activity", "flow", "outcome", "team", "conversation", "schedules", "audit"] as const;
export type View = (typeof VIEWS)[number];
export const VIEW_LABEL: Record<View, string> = { now: "Now", map: "Map", activity: "Activity", flow: "Flow", outcome: "Outcome", team: "Team", conversation: "Conversation", schedules: "Schedules", audit: "Audit" };

// `path` is what follows the view: the map's levels, `#/map/<goal>/<stage>` (decision 0018).
export interface Route { view: View; params: URLSearchParams; path: string[] }

function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [where, query = ""] = raw.split("?");
  const [first, ...rest] = where.split("/");
  const known = (VIEWS as readonly string[]).includes(first);
  const view = known ? (first as View) : "now";
  const path = known ? rest.filter(Boolean).map((s) => decodeURIComponent(s)) : [];
  return { view, params: new URLSearchParams(query), path };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => setRoute(parse(window.location.hash));
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function href(view: View, params?: Record<string, string | undefined>, path: string[] = []): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v) q.set(k, v);
  const s = q.toString();
  const tail = path.map((p) => `/${encodeURIComponent(p)}`).join("");
  return `#/${view}${tail}${s ? `?${s}` : ""}`;
}

export function setParams(view: View, params: Record<string, string | undefined>): void {
  window.location.hash = href(view, params);
}
