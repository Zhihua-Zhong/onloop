import { useEffect, useState } from "react";
import { retiredTo } from "./retired";
import { href, parse, type View } from "./address";

// The views are hash routes, so a reload or a shared link opens the same view and onloopd serves one index.html for
// all of them. The addresses are in address.ts; an old link (0.6.0's `#/now`, `#/map`, `#/replay`) is rewritten to its
// new address in place (retired.ts).
export { ADDRESS, DETAILS, VIEWS, VIEW_LABEL, href, parse, type Route, type View } from "./address";

function canonical(): void {
  const to = retiredTo(window.location.hash);
  if (to) window.history.replaceState(window.history.state, "", to); // no new history entry: Back skips the old link
}

export function useRoute(): ReturnType<typeof parse> {
  const [route, setRoute] = useState(() => {
    canonical();
    return parse(window.location.hash);
  });
  useEffect(() => {
    const on = () => {
      canonical();
      setRoute(parse(window.location.hash));
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function setParams(view: View, params: Record<string, string | undefined>): void {
  window.location.hash = href(view, params);
}
