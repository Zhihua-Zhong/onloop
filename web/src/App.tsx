import { lazy, Suspense, useEffect, useState, type ElementType, type ReactNode } from "react";
import { Activity, Bot, CalendarClock, ChevronDown, ChevronRight, Eye, Home as HomeIcon, Inbox, Map as MapIcon, Menu as MenuIcon, MessagesSquare, Monitor, Moon, ScrollText, Sun, Target, TriangleAlert } from "lucide-react";
import { streamLost, streamNews, useLive, type Live } from "@/lib/live";
import { homeApi } from "@/lib/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useHeartbeat } from "@/lib/heartbeat";
import { useNowMs } from "@/lib/clock";
import { beatAtMs, dataAgeS, liveness, type LiveState } from "@/lib/tick";
import { duration } from "@/lib/format";
import { retrying } from "@/lib/recovery";
import { DETAILS, href, useRoute, type View } from "@/lib/route";
import { cn } from "@/lib/utils";
import { Loading } from "@/components/kit";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/ui/menu";
import { Toaster } from "@/components/ui/toast";
import { Tooltip, TooltipProvider } from "@/components/ui/tooltip";
import { HomeView } from "@/views/Home";
import { DecisionsView } from "@/views/Decisions";
import { RuleAlertsView } from "@/views/details/RuleAlerts";
import { TeamView } from "@/views/Team";
import { ReplayView } from "@/views/Replay";
import { OutcomeView } from "@/views/details/Outcome";
import { SchedulesView } from "@/views/details/Schedules";
import { AuditView } from "@/views/details/Audit";
import { ConversationView } from "@/views/details/Conversation";

// The map carries React Flow, so it loads when the map is first opened.
const MapView = lazy(retrying(() => import("@/views/details/Map")));

// 0.7.0's shell (plan, section 1, step 1): prototype F's sidebar and header around the views. The main pages are at the
// top; history and operations are one click down, under Details. Until 0.7.0's own Home, Decisions, Agents and Timeline
// land (steps 2 to 4), Home, Agents and Timeline show 0.6.0's Now, Team and Replay, and say so; Decisions is marked as
// not built yet, and is not a link.

// ---- the theme ----------------------------------------------------------------------------------------------------

// A `?theme=` in the URL, else the person's own choice from the theme menu (kept in this browser), else the system's
// `prefers-color-scheme`, followed live. Choosing "System" forgets a stored choice, so a first visit never pins one.
type Theme = "light" | "dark" | "system";
const DARK = "(prefers-color-scheme: dark)";
const STORE = "onloop-theme";

function chosenTheme(): Theme {
  try {
    const url = new URLSearchParams(window.location.search).get("theme");
    if (url === "dark" || url === "light") return url;
    const saved = window.localStorage.getItem(STORE);
    if (saved === "dark" || saved === "light") return saved;
  } catch {
    /* storage can be unavailable; the theme is a convenience */
  }
  return "system";
}

function useTheme(): [Theme, boolean, (t: Theme) => void] {
  const [choice, setChoice] = useState(chosenTheme);
  const [system, setSystem] = useState(() => window.matchMedia?.(DARK).matches ?? false);
  useEffect(() => {
    const mq = window.matchMedia?.(DARK);
    if (!mq) return;
    const on = (e: MediaQueryListEvent) => setSystem(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const dark = choice === "system" ? system : choice === "dark";
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);
  const choose = (t: Theme) => {
    setChoice(t);
    try {
      if (t === "system") window.localStorage.removeItem(STORE);
      else window.localStorage.setItem(STORE, t);
    } catch {
      /* the theme is a convenience */
    }
  };
  return [choice, dark, choose];
}

function ThemeMenu({ theme, dark, choose }: { theme: Theme; dark: boolean; choose: (t: Theme) => void }) {
  const Icon = dark ? Moon : Sun;
  return (
    <DropdownMenu>
      <Tooltip content="Theme">
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" data-testid="theme" aria-label={`Theme: ${theme}`}>
            <Icon aria-hidden />
          </Button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent data-testid="theme-menu" className="min-w-[160px]">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => choose(v as Theme)}>
          {([["light", Sun, "Light"], ["dark", Moon, "Dark"], ["system", Monitor, "System"]] as const).map(([t, I, label]) => (
            <DropdownMenuRadioItem key={t} value={t} data-testid={`theme-${t}`}>
              <I aria-hidden />
              {label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---- liveness -----------------------------------------------------------------------------------------------------

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
  const clock = (
    <span data-testid="clock" className="font-mono tabular-nums text-fg max-sm:hidden">
      {new Date(nowMs).toLocaleTimeString(undefined, { hour12: false })}
    </span>
  );
  if (hb.data?.read_only && hb.data.frozen !== false) {
    // a copy served read-only (no writer): nothing new can arrive, so it is never "Live" or "No news"
    const upTo = hb.data.data_up_to ? new Date(hb.data.data_up_to).toLocaleString(undefined, { hour12: false, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : null;
    return (
      <span className="inline-flex items-center gap-3 text-xs text-muted">
        {clock}
        <Tooltip content="This server has no writer: it serves a copy of a ledger, and nothing new arrives">
          <span data-testid="live" data-state="read-only" role="status" tabIndex={0} className="inline-flex items-center gap-1.5 rounded-sm">
            <span className="h-2 w-2 shrink-0 rounded-full bg-state-void" aria-hidden />
            <span className="max-md:sr-only">Read-only copy{upTo ? ` · data up to ${upTo}` : ""}</span>
            <span className="md:hidden" aria-hidden>read-only</span>
          </span>
        </Tooltip>
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
  const dot: Record<LiveState, string> = { connecting: "bg-state-void", live: "bg-done", quiet: "bg-wait", down: "bg-danger" };
  const explain =
    state === "quiet"
      ? "onloopd answers, but neither its event stream nor its heartbeat has brought anything new for over two of its ticks: it may have stalled"
      : state === "down"
        ? "Neither onloopd's event stream nor its heartbeat answers. This page shows what it last read, and keeps trying."
        : "Updates arrive from onloopd's event stream; its heartbeat is checked every 10 s";
  return (
    <span className="inline-flex items-center gap-3 text-xs text-muted">
      {clock}
      <Tooltip content={explain}>
        <span data-testid="live" data-state={state} role="status" tabIndex={0} className={cn("inline-flex items-center gap-1.5 rounded-sm", state === "down" ? "font-medium text-danger" : state === "quiet" ? "font-medium text-state-human" : "")}>
          <span className={cn("h-2 w-2 shrink-0 rounded-full", dot[state])} aria-hidden />
          <span className="max-md:sr-only">{words[state]}</span>
          {short[state] && <span className="md:hidden" aria-hidden>{short[state]}</span>}
        </span>
      </Tooltip>
      {dataAge != null && (
        <span data-testid="data-age" className="sr-only">
          data as of {duration(dataAge)} ago
        </span>
      )}
      {live.state === "stale" && <span className="sr-only">Live updates stopped.</span>}
    </span>
  );
}

function ReadOnly({ view }: { view: View }) {
  const words = view === "conversation" ? "Read-only except messages" : view === "now" || view === "decisions" ? "Read-only except your answers" : "Read-only";
  // the operator's explanation is the eye's tooltip, not words in the header (the onloop lead's step 2 review)
  return (
    <Tooltip content={`${words}. This page only reads, except a message to a role and your answer to a decision. Each is a request from a local user, not verified, never an approval. Where you can act otherwise, the page shows the command to copy.`}>
      <span className="inline-flex items-center gap-1 rounded-sm text-xs text-muted" data-testid="read-only" tabIndex={0} aria-label={words}>
        <Eye className="h-3.5 w-3.5" aria-hidden />
      </span>
    </Tooltip>
  );
}

// ---- the sidebar --------------------------------------------------------------------------------------------------

/** Each page of the shell: its label, its icon, and what it shows until its 0.7.0 page lands (null: its own page). */
interface Page { key: string; label: string; icon: ElementType; view: View; interim?: string }
const MAIN: Page[] = [
  { key: "home", label: "Home", icon: HomeIcon, view: "now" },
  { key: "decisions", label: "Decisions", icon: Inbox, view: "decisions" },
  { key: "agents", label: "Agents", icon: Bot, view: "team", interim: "Agents shows 0.6's Team view until the new Agents page lands." },
  { key: "timeline", label: "Timeline", icon: Activity, view: "replay", interim: "Timeline shows 0.6's Replay view until the new Timeline lands." },
];
const DETAIL_ICON: Record<string, ElementType> = { map: MapIcon, outcome: Target, conversation: MessagesSquare, schedules: CalendarClock, audit: ScrollText, rules: TriangleAlert };
const DETAIL_LABEL: Record<string, string> = { map: "Map", outcome: "Outcome", conversation: "Conversation", schedules: "Schedules", audit: "Audit", rules: "Rule alerts" };

function pageOf(view: View): { label: string; interim?: string; details: boolean } {
  const main = MAIN.find((p) => p.view === view);
  if (main) return { label: main.label, interim: main.interim, details: false };
  return { label: DETAIL_LABEL[view] ?? view, details: true };
}

// The sidebar's count is the Decisions page's count, the same one Home shows (tests/test_agreement.py, e2e)
function DecisionsBadge() {
  const q = useQuery({ queryKey: ["decisions"], queryFn: homeApi.decisions, refetchInterval: 60_000 });
  const n = q.data?.count ?? 0;
  if (!n) return null;
  return (
    <span className="ml-auto rounded-full bg-wait/15 px-1.5 text-xs font-medium text-state-human num" data-testid="decisions-badge" aria-label={`${n} open`}>
      {n}
    </span>
  );
}

function NavItem({ to, label, icon: Icon, active, testid, onNav, children }: { to: string; label: string; icon: ElementType; active: boolean; testid: string; onNav?: () => void; children?: ReactNode }) {
  return (
    <a
      href={to}
      onClick={onNav}
      data-testid={testid}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex h-8 items-center gap-2.5 rounded-md px-2 text-sm text-muted transition-colors hover:bg-hover hover:text-fg [&_svg]:size-4 [&_svg]:shrink-0",
        active && "bg-hover font-medium text-fg",
      )}
    >
      <Icon aria-hidden />
      {label}
      {children}
    </a>
  );
}

function Sidebar({ view, onNav }: { view: View; onNav?: () => void }) {
  const inDetails = DETAILS.includes(view);
  const [open, setOpen] = useState(inDetails);
  useEffect(() => {
    if (inDetails) setOpen(true);
  }, [inDetails]);
  return (
    <nav className="flex h-full flex-col gap-0.5 p-3" aria-label="Main">
      <a href={href("now")} onClick={onNav} className="mb-4 flex items-center gap-2 px-2 pt-1 font-semibold tracking-tight" aria-label="onloop, Home">
        <span className="grid h-6 w-6 place-items-center rounded-md bg-fg text-[11px] font-bold text-bg" aria-hidden>ol</span>
        onloop
      </a>
      {MAIN.map((p) => (
        <NavItem key={p.key} to={href(p.view)} label={p.label} icon={p.icon} active={view === p.view} testid={`nav-${p.key}`} onNav={onNav}>
          {p.key === "decisions" && <DecisionsBadge />}
        </NavItem>
      ))}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        data-testid="details-toggle"
        aria-expanded={open}
        aria-controls="nav-details"
        className="mt-5 flex h-7 items-center gap-1 rounded-md px-2 text-xs font-medium text-muted hover:text-fg"
      >
        {open ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
        Details
      </button>
      <div id="nav-details" className={cn("flex flex-col gap-0.5", !open && "hidden")}>
        {DETAILS.map((d) => (
          <NavItem key={d} to={href(d)} label={DETAIL_LABEL[d]} icon={DETAIL_ICON[d]} active={view === d} testid={`nav-${d}`} onNav={onNav} />
        ))}
      </div>
    </nav>
  );
}

// ---- the shell ----------------------------------------------------------------------------------------------------

export default function App() {
  const route = useRoute();
  const live = useLive();
  const [theme, dark, choose] = useTheme();
  const [drawer, setDrawer] = useState(false);
  const page = pageOf(route.view);
  useEffect(() => {
    document.title = `${page.details ? `Details · ${page.label}` : page.label} · onloop`;
  }, [page.label, page.details]);
  useEffect(() => setDrawer(false), [route.view]);

  return (
    <TooltipProvider delayDuration={300}>
      <Toaster>
        <div className="flex h-full">
          <aside className="hidden w-[232px] shrink-0 border-r border-line bg-sidebar lg:block" data-testid="sidebar">
            <Sidebar view={route.view} />
          </aside>
          <Dialog open={drawer} onOpenChange={setDrawer}>
            <DialogContent
              side="left"
              className="bg-sidebar p-0"
              aria-describedby={undefined}
              data-testid="drawer"
              // focus the page you are on, so opening the menu shows where you are and opens nothing else
              onOpenAutoFocus={(e) => {
                const box = e.currentTarget as HTMLElement;
                const here = box.querySelector<HTMLElement>('[aria-current="page"]') ?? box.querySelector<HTMLElement>("a[href]");
                if (here) {
                  e.preventDefault();
                  here.focus();
                }
              }}
            >
              <DialogTitle className="sr-only">Menu</DialogTitle>
              <Sidebar view={route.view} onNav={() => setDrawer(false)} />
            </DialogContent>
          </Dialog>
          <div className="flex min-w-0 flex-1 flex-col">
            <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-bg px-3 md:px-6">
              <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setDrawer(true)} aria-label="Open the menu" data-testid="menu">
                <MenuIcon aria-hidden />
              </Button>
              {/* where you are, for a phone (on a wide screen the sidebar shows it); each view keeps its own heading */}
              <p className="min-w-0 truncate text-sm font-semibold lg:hidden" data-testid="page-title">
                {page.details && <span className="font-normal text-muted">Details · </span>}
                {page.label}
              </p>
              <div className="ml-auto flex shrink-0 items-center gap-3 max-[420px]:gap-2">
                <LiveBadge live={live} />
                <ReadOnly view={route.view} />
                <ThemeMenu theme={theme} dark={dark} choose={choose} />
              </div>
            </header>
            {live.state === "stale" && (
              <div role="status" data-testid="stale" className="border-b border-wait/40 bg-wait/10 px-4 py-1.5 text-[13px] text-fg">
                Live updates stopped at {new Date(live.since ?? Date.now()).toLocaleTimeString(undefined, { hour12: false })}. This page shows what was
                read then, and reconnects by itself.
              </div>
            )}
            {page.interim && (
              <p data-testid="interim" className="flex items-center gap-2 border-b border-line px-4 py-1.5 text-xs text-muted md:px-6">
                <Badge tone="soon">0.6 view</Badge>
                {page.interim}
              </p>
            )}
            <main className={cn("min-h-0 flex-1", route.view === "conversation" || route.view === "map" ? "flex flex-col" : "overflow-y-auto")}>
              {route.view === "now" && <HomeView />}
              {route.view === "decisions" && <DecisionsView />}
              {route.view === "rules" && <RuleAlertsView />}
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
        </div>
      </Toaster>
    </TooltipProvider>
  );
}
