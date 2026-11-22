import { lazy, Suspense, useEffect, useState } from "react";
import { Eye, Moon, Sun } from "lucide-react";
import { streamLost, streamNews, useLive, type Live } from "@/lib/live";
import { useQueryClient } from "@tanstack/react-query";
import { useHeartbeat } from "@/lib/heartbeat";
import { useNowMs } from "@/lib/clock";
import { beatAtMs, dataAgeS, liveness, type LiveState } from "@/lib/tick";
import { duration } from "@/lib/format";
import { retrying } from "@/lib/recovery";
import { href, useRoute, VIEW_LABEL, VIEWS, type View } from "@/lib/route";
import { cn } from "@/lib/utils";
import { Loading } from "@/components/kit";
import { NowView } from "@/views/Now";
import { OutcomeView } from "@/views/Outcome";
import { TeamView } from "@/views/Team";
import { SchedulesView } from "@/views/Schedules";
import { AuditView } from "@/views/Audit";
import { ConversationView } from "@/views/Conversation";
import { ReplayView } from "@/views/Replay";

// The map carries React Flow, so it loads when the map is first opened.
const MapView = lazy(retrying(() => import("@/views/Map")));

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

// The header's clock and liveness (the project lead's finding on 8796): a clock that ticks every second, so a page
// that has stopped is seen to have stopped; what onloopd last said, from its stream and its heartbeat; and how old
// the data on this page is. The rules and thresholds are in lib/tick.ts.
function LiveBadge({ live }: { live: Live }) {
  const nowMs = useNowMs();
  const client = useQueryClient();
  const hb = useHeartbeat(); // the same reading Now's Health card shows
  const beatNews = hb.data ? beatAtMs(hb.data.age_s, hb.dataUpdatedAt) : null;
  const news = Math.max(streamNews() ?? 0, beatNews ?? 0) || null;
  const lost = streamLost();
  // down: neither the stream nor the heartbeat answers; counted from the last time either did
  const downSince = lost !== null && hb.isError ? Math.max(lost, hb.dataUpdatedAt || 0, streamNews() ?? 0) : null;
  const { state, forS } = liveness({ nowMs, newsAtMs: news, downSinceMs: downSince, periodS: hb.data?.period_s ?? 10 });
  const answers = client
    .getQueryCache()
    .findAll({ type: "active" })
    .map((q) => ({
      key: String(q.queryKey[0]),
      updatedAtMs: q.state.dataUpdatedAt,
      refreshes: q.observers.some((o) => Boolean(o.options.refetchInterval)),
    }));
  const dataAge = dataAgeS(answers, nowMs);
  if (hb.data?.read_only && hb.data.frozen !== false) {
    // a copy served read-only (no writer): nothing new can arrive, so it is never "Live" or "No news"
    const upTo = hb.data.data_up_to ? new Date(hb.data.data_up_to).toLocaleString(undefined, { hour12: false, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : null;
    return (
      <span className="inline-flex items-center gap-3 text-xs text-muted">
        <span data-testid="clock" className="font-mono tabular-nums text-fg" title="This page's clock: it ticks every second">
          {new Date(nowMs).toLocaleTimeString(undefined, { hour12: false })}
        </span>
        <span data-testid="live" data-state="read-only" role="status" className="inline-flex items-center gap-1.5" title="This server has no writer: it serves a copy of a ledger, and nothing new arrives">
          <span className="h-2 w-2 shrink-0 rounded-full bg-state-void" aria-hidden />
          <span className="max-md:sr-only">Read-only copy{upTo ? ` · data up to ${upTo}` : ""}</span>
          <span className="md:hidden" aria-hidden>read-only</span>
        </span>
      </span>
    );
  }
  const words: Record<LiveState, string> = {
    connecting: "Connecting",
    live: "Live",
    quiet: `No news for ${duration(forS)}`,
    down: `onloopd not answering for ${duration(forS)}`,
  };
  // a read-only view of a ledger something else still writes: live, and says it takes no writes
  if (hb.data?.read_only) for (const k of Object.keys(words) as LiveState[]) words[k] = `Read-only view · ${words[k]}`;
  // a phone has room for a few words only; the duration must never be the part cut off
  const short: Record<LiveState, string> = { connecting: "", live: "", quiet: `quiet ${duration(forS)}`, down: `offline ${duration(forS)}` };
  const dot: Record<LiveState, string> = { connecting: "bg-state-void", live: "bg-state-done", quiet: "bg-state-human", down: "bg-state-blocked" };
  const title =
    state === "quiet"
      ? "onloopd answers, but neither its event stream nor its heartbeat has brought anything new for over two of its ticks: it may have stalled"
      : state === "down"
        ? "Neither onloopd's event stream nor its heartbeat answers. This page shows what it last read, and keeps trying."
        : "Updates arrive from onloopd's event stream; its heartbeat is checked every 10 s";
  return (
    <span className="inline-flex items-center gap-3 text-xs text-muted">
      <span data-testid="clock" className="font-mono tabular-nums text-fg" title="This page's clock: it ticks every second">
        {new Date(nowMs).toLocaleTimeString(undefined, { hour12: false })}
      </span>
      <span data-testid="live" data-state={state} role="status" className={cn("inline-flex items-center gap-1.5", state === "down" ? "font-medium text-state-blocked" : state === "quiet" ? "font-medium text-state-human" : "")} title={title}>
        <span className={cn("h-2 w-2 shrink-0 rounded-full", dot[state])} aria-hidden />
        <span className="max-md:sr-only">{words[state]}</span>
        {short[state] && <span className="md:hidden" aria-hidden>{short[state]}</span>}
      </span>
      {dataAge != null && (
        <span data-testid="data-age" className="tabular-nums max-lg:sr-only" title="When this page last read its data from onloopd">
          data as of {duration(dataAge)} ago
        </span>
      )}
      {live.state === "stale" && <span className="sr-only">Live updates stopped.</span>}
    </span>
  );
}

function Nav({ view }: { view: View }) {
  return (
    <nav
      aria-label="Views"
      className="-mx-1 flex min-w-0 items-center gap-0.5 overflow-x-auto max-[480px]:order-last max-[480px]:-mb-1 max-[480px]:w-full max-[480px]:justify-between"
    >
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
      <header className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2 max-[480px]:flex-wrap max-[480px]:gap-y-1 max-[420px]:gap-x-2 max-[420px]:px-3">
        <a href={href("now")} className="flex shrink-0 items-center gap-2 font-semibold tracking-tight" aria-label="onloop, Now">
          <span className="grid h-6 w-6 place-items-center rounded-md bg-fg text-[11px] font-bold text-bg" aria-hidden>ol</span>
          <span className="min-[481px]:max-sm:hidden">onloop</span>
        </a>
        <Nav view={route.view} />
        <div className="ml-auto flex shrink-0 items-center gap-3 max-[420px]:gap-1.5 max-[420px]:pl-1">
          <LiveBadge live={live} />
          <span
            className="inline-flex items-center gap-1 text-xs text-muted"
            title="This page only reads, except the message box on Conversation and a task's Done or Decline. Each is a request from a local user, not verified, never an approval. Where you can act otherwise, it shows the command to copy."
            data-testid="read-only"
          >
            <Eye className="h-3.5 w-3.5" aria-hidden />
            <span className="max-lg:sr-only">
              {route.view === "conversation" ? "Read-only except messages" : route.view === "now" ? "Read-only except Done and Decline" : "Read-only"}
            </span>
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
      <main className={cn("min-h-0 flex-1", route.view === "conversation" || route.view === "map" ? "flex flex-col" : "overflow-y-auto")}>
        {route.view === "now" && <NowView />}
        {route.view === "map" && (
          <Suspense fallback={<div className="p-4"><Loading what="the map" /></div>}>
            <MapView params={route.params} path={route.path} />
          </Suspense>
        )}
        {route.view === "replay" && <ReplayView params={route.params} />}
        {route.view === "outcome" && <OutcomeView params={route.params} />}
        {route.view === "team" && <TeamView />}
        {route.view === "conversation" && <ConversationView params={route.params} />}
        {route.view === "schedules" && <SchedulesView params={route.params} />}
        {route.view === "audit" && <AuditView params={route.params} />}
      </main>
    </div>
  );
}
