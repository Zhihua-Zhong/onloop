import { useEffect, useState } from "react";

// The views are hash routes (#/now, #/replay, #/audit?kind=pr.merged), so a reload or a shared link opens the same view
// and onloopd serves one index.html for all of them.
export const VIEWS = ["now", "replay", "map", "outcome", "team", "conversation", "schedules", "audit"] as const;
// The views decision 0019 retired: an old link opens where their parts moved, not Now
export const RETIRED: Record<string, View> = { flow: "map", activity: "replay" };
export type View = (typeof VIEWS)[number];
export const VIEW_LABEL: Record<View, string> = { now: "Now", replay: "Replay", map: "Map", outcome: "Outcome", team: "Team", conversation: "Conversation", schedules: "Schedules", audit: "Audit" };

// `path` is what follows the view: the map's levels, `#/map/<goal>/<stage>` (decision 0018).
export interface Route { view: View; params: URLSearchParams; path: string[] }

function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [where, query = ""] = raw.split("?");
  const [given, ...rest] = where.split("/");
  const first = RETIRED[given] ?? given;
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
