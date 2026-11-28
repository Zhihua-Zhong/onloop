import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Inbox, Loader2, Sparkles } from "lucide-react";
import { agentsApi, homeApi, uiPost } from "@/lib/api";
import { DETAILS, href, type View } from "@/lib/route";
import { decisionText, shortHash, useTexts } from "@/lib/texts";
import { avatarOf } from "@/lib/roleColor";
import { useRoleColor } from "@/lib/useRoleColor";
import { cn } from "@/lib/utils";
import { Avatar } from "@/components/ui/basics";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

// ⌘K (0.7.0 plan, section 1): go anywhere, or ask. A question goes to POST /ui/ask, answered by the smart layer from
// the computed facts only, every number checked and every fact it used cited (src/onloop/brief.py, ask). When onloop
// does not answer (smart off, the day's cap, an answer that did not hold up, a read-only copy), the menu says why and
// searches instead. The dialog never fades: only the dim behind it does (section 7), so the answer is readable at once.

const Ctx = createContext<{ open: boolean; setOpen: (v: boolean) => void }>({ open: false, setOpen: () => {} });

export function CommandProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, []);
  return (
    <Ctx.Provider value={{ open, setOpen }}>
      {children}
      <CommandMenu />
    </Ctx.Provider>
  );
}

export const useCommand = () => useContext(Ctx);

const DETAIL_NAMES: Partial<Record<View, string>> = {
  map: "Map",
  replay: "Replay",
  outcome: "Outcome",
  conversation: "Conversation",
  schedules: "Schedules",
  audit: "Audit",
  rules: "Rule alerts",
};

interface Item {
  group: "Ask onloop" | "Go to" | "Decisions" | "Agents";
  label: string;
  hint?: string;
  icon: ReactNode;
  run: () => void;
  ask?: boolean;
}

interface Answer {
  answer: string;
  cites: string[];
  facts_hash: string;
}

// words that say nothing about what to find
const COMMON = new Set("the and for who what when where why how which does did has have are was were with from this that them they its it's into most more many much any all you your our onloop".split(" "));

function score(text: string, q: string): number {
  if (!q) return 1;
  const t = text.toLowerCase();
  if (t.includes(q)) return 2;
  return q.split(/[\s?!.,]+/).filter((w) => w.length > 2 && !COMMON.has(w) && t.includes(w)).length ? 1 : 0;
}

const go = (to: string) => {
  window.location.hash = to.replace(/^#/, "");
};

function CommandMenu() {
  const { open, setOpen } = useCommand();
  const color = useRoleColor();
  const decisions = useQuery({ queryKey: ["decisions"], queryFn: homeApi.decisions, enabled: open });
  const agents = useQuery({ queryKey: ["agents"], queryFn: agentsApi.all, enabled: open });
  const t = useTexts();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [asked, setAsked] = useState<{ q: string; state: "asking" | "answer" | "off"; answer?: Answer; why?: string } | null>(null);
  useEffect(() => {
    if (open) {
      setQ("");
      setSel(0);
      setAsked(null);
    }
  }, [open]);
  const close = () => setOpen(false);
  const ql = q.trim().toLowerCase();

  const ask = async (question: string) => {
    setAsked({ q: question, state: "asking" });
    try {
      const out = (await uiPost("/ui/ask", { q: question })) as Partial<Answer> & { off?: string };
      if (out.off || !out.answer) setAsked({ q: question, state: "off", why: out.off ?? "no answer" });
      else setAsked({ q: question, state: "answer", answer: out as Answer });
    } catch (e) {
      setAsked({ q: question, state: "off", why: e instanceof Error ? e.message.replace(/^Not taken: /, "") : String(e) });
    }
  };

  const [items, every] = useMemo<[Item[], Item[]]>(() => {
    const all: Item[] = [];
    if (ql) all.push({ group: "Ask onloop", label: q.trim(), hint: "answered from this week's facts", icon: <Sparkles />, run: () => void ask(q.trim()), ask: true });
    const pages: [string, string][] = [
      [href("now"), "Home"],
      [href("decisions"), "Decisions"],
      [href("team"), "Agents"],
      [href("timeline"), "Timeline"],
      [href("digest"), "Morning digest"],
      ...DETAILS.map((d) => [href(d), `Details · ${DETAIL_NAMES[d] ?? d}`] as [string, string]),
    ];
    for (const [to, label] of pages) all.push({ group: "Go to", label, icon: <ArrowRight />, run: () => (go(to), close()) });
    for (const d of decisions.data?.open ?? []) {
      const w = decisionText(t, d);
      all.push({ group: "Decisions", label: w?.title ?? d.title, hint: d.asked_by ?? undefined, icon: <Inbox />, run: () => (go(href("decisions", { id: d.id })), close()) });
    }
    for (const a of agents.data?.agents ?? []) {
      all.push({ group: "Agents", label: a.role, hint: a.words, icon: <Avatar {...avatarOf(a.role, color)} size={16} />, run: () => (go(href("team", undefined, [a.role])), close()) });
    }
    const matched = all.filter((i) => i.ask || score(`${i.label} ${i.hint ?? ""}`, ql) > 0);
    // a question first; otherwise the closest match first, the question last
    return [ql.endsWith("?") ? matched : [...matched.filter((i) => !i.ask), ...matched.filter((i) => i.ask)], all];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, decisions.data, agents.data, t, color]);
  // not answered: what matches, without the question; with nothing matching, where to go
  const off = asked?.state === "off";
  const matches = off ? items.filter((i) => !i.ask) : items;
  const shown = (off && !matches.length ? every.filter((i) => i.group === "Go to") : matches).slice(0, 14);

  const cite = (id: string): { label: string; to: string } => {
    if (id === "numbers") return { label: "This week's numbers", to: href("now") };
    if (id.startsWith("agent:")) return { label: id.slice(6), to: href("team", undefined, [id.slice(6)]) };
    const d = decisions.data?.open.find((x) => x.id === id);
    if (d) return { label: decisionText(t, d)?.title ?? d.title, to: href("decisions", { id }) };
    return { label: "Unusual this week", to: href("now") };
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (asked?.state === "answer") return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => Math.min(shown.length - 1, s + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => Math.max(0, s - 1));
    } else if (e.key === "Enter" && shown[sel]) {
      e.preventDefault();
      shown[sel].run();
    }
  };

  let group = "";
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent side="top" className="overflow-hidden p-0" closeButton={false} aria-describedby={undefined} data-testid="command">
        <DialogTitle className="sr-only">Search or ask</DialogTitle>
        <div className="flex items-center gap-2 border-b border-line px-4">
          <Sparkles className="size-4 shrink-0 text-muted" aria-hidden />
          <input
            autoFocus
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setSel(0);
              if (asked?.state !== "asking") setAsked(null);
            }}
            onKeyDown={onKey}
            placeholder="Search, jump, or ask a question…"
            aria-label="Search or ask"
            data-testid="command-input"
            className="h-12 min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted"
          />
          <kbd className="hidden rounded border border-line px-1.5 py-0.5 text-[11px] text-muted sm:inline">Esc</kbd>
        </div>
        {asked && asked.state !== "off" ? (
          <div className="max-h-[60vh] space-y-3 overflow-auto p-5" data-testid="command-answer" aria-live="polite">
            <div className="flex items-center gap-1.5 text-xs font-medium text-muted">
              <Sparkles className="size-3.5" aria-hidden />
              {asked.q}
            </div>
            {asked.state === "asking" ? (
              <p className="flex items-center gap-2 text-sm text-muted" data-testid="command-asking">
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
                onloop is reading this week's facts…
              </p>
            ) : (
              <>
                <p className="text-base leading-7" data-testid="command-answer-text">{asked.answer!.answer}</p>
                {asked.answer!.cites.length > 0 && (
                  <div className="flex flex-wrap items-center gap-2 text-xs text-muted" data-testid="command-cites">
                    From:
                    {[...new Map(asked.answer!.cites.map((c) => [cite(c).to + cite(c).label, cite(c)])).values()].map((c) => (
                      <button key={c.to + c.label} type="button" onClick={() => (go(c.to), close())} className="rounded-md border border-line px-2 py-0.5 text-fg hover:bg-hover">
                        {c.label}
                      </button>
                    ))}
                  </div>
                )}
                <p className="text-xs text-muted">
                  Written by onloop from this week's data · facts <span className="font-mono">{shortHash(asked.answer!.facts_hash)}</span>. Every number in it is one of
                  the facts.
                </p>
                <Button variant="link" size="sm" className="px-0" onClick={() => setAsked(null)} data-testid="command-back">
                  <ArrowLeft aria-hidden />
                  Back to results
                </Button>
              </>
            )}
          </div>
        ) : (
          <div className="max-h-[56vh] overflow-auto p-2" role="listbox" aria-label="Results" data-testid="command-results">
            {asked?.state === "off" && (
              <p className="mx-2 mb-1 mt-2 rounded-md bg-raised px-3 py-2 text-sm text-muted" data-testid="command-off">
                onloop can't answer that here: {(asked.why ?? "").replace(/[.;]\s*here is what matches\.?$/i, "").replace(/\.$/, "")}. Here is what matches.
              </p>
            )}
            {shown.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted">Nothing matches "{q}".</p>}
            {off && !matches.length && <p className="px-3 pb-1 pt-2 text-xs text-muted">Nothing matches "{q.trim()}". Where to look:</p>}
            {shown.map((it, i) => {
              const head = it.group !== group ? (group = it.group) : null;
              return (
                <div key={`${it.group}-${it.label}-${i}`}>
                  {head && <div className="px-2 pb-1 pt-3 text-xs font-medium text-muted">{head}</div>}
                  <button
                    type="button"
                    role="option"
                    aria-selected={i === sel}
                    onMouseEnter={() => setSel(i)}
                    onClick={it.run}
                    data-testid={it.ask ? "command-ask" : "command-item"}
                    className={cn("flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-sm [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-muted", i === sel && "bg-hover")}
                  >
                    {it.icon}
                    <span className="min-w-0 flex-1 truncate">{it.ask ? `Ask: ${it.label}` : it.label}</span>
                    {it.hint && <span className="hidden max-w-[40%] truncate text-xs text-muted sm:inline">{it.hint}</span>}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function SearchButton({ compact = false }: { compact?: boolean }) {
  const { setOpen } = useCommand();
  const mac = typeof navigator !== "undefined" && /Mac/.test(navigator.platform);
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      data-testid="search"
      aria-label={`Search or ask (${mac ? "⌘" : "Ctrl"} K)`}
      className={cn(
        "flex h-9 min-w-0 items-center gap-2 rounded-md border border-line bg-bg text-sm text-muted hover:bg-hover",
        compact ? "w-9 justify-center px-0" : "w-[300px] px-3 xl:w-[360px]",
      )}
    >
      <Sparkles className="size-4 shrink-0" aria-hidden />
      {!compact && (
        <>
          <span className="truncate">Search or ask "what waits on me?"</span>
          <kbd className="ml-auto shrink-0 whitespace-nowrap rounded border border-line px-1.5 py-0.5 text-[11px]">{mac ? "⌘" : "Ctrl"} K</kbd>
        </>
      )}
    </button>
  );
}
