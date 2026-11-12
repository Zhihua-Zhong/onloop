import { useEffect, useState } from "react";

// The views are hash routes (#/now, #/flow, #/audit?kind=pr.merged), so a reload or a shared link opens the same view
// and onloopd serves one index.html for all of them.
export const VIEWS = ["now", "flow", "team", "schedules", "audit"] as const;
export type View = (typeof VIEWS)[number];
export const VIEW_LABEL: Record<View, string> = { now: "Now", flow: "Flow", team: "Team", schedules: "Schedules", audit: "Audit" };

export interface Route { view: View; params: URLSearchParams }

function parse(hash: string): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [path, query = ""] = raw.split("?");
  const view = (VIEWS as readonly string[]).includes(path) ? (path as View) : "now";
  return { view, params: new URLSearchParams(query) };
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

export function href(view: View, params?: Record<string, string | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v) q.set(k, v);
  const s = q.toString();
  return `#/${view}${s ? `?${s}` : ""}`;
}

export function setParams(view: View, params: Record<string, string | undefined>): void {
  window.location.hash = href(view, params);
}
