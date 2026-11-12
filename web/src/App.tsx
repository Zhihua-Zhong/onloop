import { lazy, Suspense, useEffect, useState } from "react";
import { Eye, Moon, Sun } from "lucide-react";
import { useLive, type Live } from "@/lib/live";
import { href, useRoute, VIEW_LABEL, VIEWS, type View } from "@/lib/route";
import { cn } from "@/lib/utils";
import { Loading } from "@/components/kit";
import { NowView } from "@/views/Now";
import { TeamView } from "@/views/Team";
import { SchedulesView } from "@/views/Schedules";
import { AuditView } from "@/views/Audit";

// Flow carries the layout engine (elkjs, the largest dependency by far): it loads when Flow is first opened.
const FlowView = lazy(() => import("@/views/Flow"));

// The theme: a `?theme=` in the URL, else the person's own choice from the toggle (kept in this browser), else the
// system's `prefers-color-scheme`, followed live. Only the toggle stores a choice, so a first visit never pins one.
const DARK = "(prefers-color-scheme: dark)";

function chosenTheme(): "dark" | "light" | null {
  try {
    const url = new URLSearchParams(window.location.search).get("theme");
    if (url === "dark" || url === "light") return url;
    const saved = window.localStorage.getItem("onloop-theme");
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    /* storage can be unavailable; the theme is a convenience */
  }
  return null;
}

function useTheme(): [boolean, () => void] {
  const [choice, setChoice] = useState(chosenTheme);
  const [system, setSystem] = useState(() => window.matchMedia?.(DARK).matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.(DARK);
    if (!mq) return;
    const on = (e: MediaQueryListEvent) => setSystem(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const dark = choice ? choice === "dark" : system;
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);
  const toggle = () => {
    const next = dark ? "light" : "dark";
    setChoice(next);
    try {
      window.localStorage.setItem("onloop-theme", next);
    } catch {
      /* the theme is a convenience */
    }
  };
  return [dark, toggle];
}

function LiveBadge({ live }: { live: Live }) {
  if (live.state === "stale") return null; // the banner below says it in full
  return (
    <span data-testid="live" data-state={live.state} className="hidden items-center gap-1.5 text-xs text-muted md:inline-flex" title="Updates arrive from onloopd's event stream">
      <span className={cn("h-2 w-2 rounded-full", live.state === "live" ? "bg-state-done" : "bg-state-void")} aria-hidden />
      {live.state === "live" ? "Live" : "Connecting"}
    </span>
  );
}

function Nav({ view }: { view: View }) {
  return (
    <nav aria-label="Views" className="-mx-1 flex min-w-0 items-center gap-0.5 overflow-x-auto">
      {VIEWS.map((v) => (
        <a
          key={v}
          href={href(v)}
          data-testid={`nav-${v}`}
          aria-current={v === view ? "page" : undefined}
          className={cn(
            "shrink-0 rounded-md px-2.5 py-1.5 text-sm font-medium max-[420px]:px-1.5 max-[420px]:text-[13.5px]",
            v === view ? "bg-accent/10 font-semibold text-accent" : "text-muted hover:bg-raised hover:text-fg",
          )}
        >
          {VIEW_LABEL[v]}
        </a>
      ))}
    </nav>
  );
}

export default function App() {
  const route = useRoute();
  const live = useLive();
  const [dark, toggleTheme] = useTheme();
  useEffect(() => {
    document.title = `${VIEW_LABEL[route.view]} · onloop`;
  }, [route.view]);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2 max-[420px]:gap-2 max-[420px]:px-3">
        <a href={href("now")} className="flex shrink-0 items-center gap-2 font-semibold tracking-tight" aria-label="onloop, Now">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-fg text-[11px] font-bold text-bg" aria-hidden>ol</span>
          <span className="max-sm:hidden">onloop</span>
        </a>
        <Nav view={route.view} />
        <div className="ml-auto flex shrink-0 items-center gap-3 max-[420px]:gap-1.5 max-[420px]:pl-1">
          <LiveBadge live={live} />
          <span className="inline-flex items-center gap-1 text-xs text-muted" title="This page only reads. Where you can act, it shows the command to copy." data-testid="read-only">
            <Eye className="h-3.5 w-3.5" aria-hidden />
            <span className="max-lg:sr-only">Read-only</span>
          </span>
          <button
            type="button"
            data-testid="theme"
            onClick={toggleTheme}
            aria-label={dark ? "Light theme" : "Dark theme"}
            className="grid h-8 w-8 place-items-center rounded-md text-muted hover:bg-raised hover:text-fg"
          >
            {dark ? <Sun className="h-4 w-4" aria-hidden /> : <Moon className="h-4 w-4" aria-hidden />}
          </button>
        </div>
      </header>
      {live.state === "stale" && (
        <div role="status" data-testid="stale" className="border-b border-state-human/40 bg-state-human/10 px-4 py-1.5 text-[13px] text-fg">
          Live updates stopped at {new Date(live.since ?? Date.now()).toLocaleTimeString(undefined, { hour12: false })}. This page shows what was
          read then, and reconnects by itself.
        </div>
      )}
      <main className={cn("min-h-0 flex-1", route.view === "flow" ? "flex flex-col" : "overflow-y-auto")}>
        {route.view === "now" && <NowView />}
        {route.view === "flow" && (
          <Suspense fallback={<div className="p-4"><Loading what="the flow" /></div>}>
            <FlowView params={route.params} />
          </Suspense>
        )}
        {route.view === "team" && <TeamView />}
        {route.view === "schedules" && <SchedulesView params={route.params} />}
        {route.view === "audit" && <AuditView params={route.params} />}
      </main>
    </div>
  );
}
