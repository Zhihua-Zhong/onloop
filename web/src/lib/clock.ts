// One clock for the page, ticking every second (lib/tick.ts says why). A component that reads it re-renders on each
// tick; nothing else does. The rules are in tick.ts, so node:test holds them; this file only wires them to React.
import { createContext, createElement, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { sinceFetch, tickModel, TICK_MS } from "@/lib/tick";

let nowMs = Date.now();
const listeners = new Set<() => void>();
let timer: number | undefined;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (timer === undefined) {
    nowMs = Date.now();
    timer = window.setInterval(() => {
      nowMs = Date.now();
      listeners.forEach((l) => l());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(listener);
    if (!listeners.size) {
      window.clearInterval(timer);
      timer = undefined;
    }
  };
}

export function useNowMs(): number {
  return useSyncExternalStore(subscribe, () => nowMs);
}

// When the view's answer arrived (a query's dataUpdatedAt), for the cards drawn inside it.
const FetchedAt = createContext(0);
export function Fetched({ at, children }: { at: number; children: ReactNode }) {
  return createElement(FetchedAt.Provider, { value: at }, children);
}

// Whole seconds since the answer arrived; a new value once a second, so a view re-renders once a second at most.
export function useSinceFetch(at?: number): number {
  const ctx = useContext(FetchedAt);
  return Math.floor(sinceFetch(at ?? ctx, useNowMs()));
}

// A view's answer as it stands now (tickModel): its ages rise with the clock between answers.
export function useTicking<T>(data: T, at?: number, opts: { liveQuiet?: boolean } = {}): T {
  const s = useSinceFetch(at);
  const live = opts.liveQuiet === true;
  return useMemo(() => tickModel(data, s, { liveQuiet: live }), [data, s, live]);
}
