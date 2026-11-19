import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { coalescer } from "@/lib/coalesce";

export type Live = { state: "connecting" | "live" | "stale"; since: number | null };

const STALE_AFTER_MS = 5_000;

// What the header's liveness reads on each tick (lib/tick.ts): when the stream last brought news (it opened, or an
// event came), and since when it has been down. Kept outside React state, so a busy stream re-renders nothing.
let streamNewsAt: number | null = null;
let streamLostAt: number | null = null;
export const streamNews = (): number | null => streamNewsAt;
export const streamLost = (): number | null => streamLostAt;

// Live updates from onloopd's event stream (`/api/stream?plain=1`, a GET). A burst of new events refetches what the
// open view reads, once, and a steady stream at least every two seconds (lib/coalesce.ts). When the stream is down for more than five seconds the state is "stale" and the shell says
// so: the page keeps what it last read, labelled with when (fail closed, never silently old). It reconnects by itself.
export function useLive(): Live {
  const client = useQueryClient();
  const [live, setLive] = useState<Live>({ state: "connecting", since: null });
  const after = useRef(0);
  useEffect(() => {
    let source: EventSource | null = null;
    let staleTimer: number | undefined;
    const flush = coalescer(() => client.invalidateQueries());
    let retry: number | undefined;
    let closed = false;
    let lostAt: number | null = null;
    // set when onloopd did not answer (the first read or the stream): the next open fetches every query again, so a
    // view that failed while onloopd was down recovers at once instead of waiting for its next event or interval
    let missed = false;
    const open = () => {
      if (closed) return;
      source = new EventSource(`/api/stream?plain=1&after=${after.current}`);
      source.onopen = () => {
        window.clearTimeout(staleTimer);
        lostAt = null;
        streamLostAt = null;
        streamNewsAt = Date.now();
        if (missed) {
          missed = false;
          void client.invalidateQueries();
        }
        setLive({ state: "live", since: null });
      };
      source.onmessage = (e: MessageEvent) => {
        const seq = Number(e.lastEventId);
        if (Number.isFinite(seq) && seq > after.current) after.current = seq;
        streamNewsAt = Date.now();
        flush.poke();
      };
      source.onerror = () => {
        source?.close();
        missed = true;
        // The clock starts at the first failure and runs through the retries; only a reconnect stops it.
        if (lostAt === null) {
          lostAt = Date.now();
          streamLostAt = lostAt;
          const since = lostAt;
          staleTimer = window.setTimeout(() => setLive({ state: "stale", since }), STALE_AFTER_MS);
        }
        window.clearTimeout(retry);
        retry = window.setTimeout(open, 2_000);
      };
    };
    // The page has just read everything: the stream starts at the newest event.
    fetch("/api/events?latest=1&limit=1", { method: "GET", cache: "no-store" })
      .then((r) => {
        if (!r.ok) missed = true;
        return r.ok ? r.json() : { events: [] };
      })
      .then((b: { events: { seq: number }[] }) => {
        after.current = b.events[0]?.seq ?? 0;
      })
      .catch(() => {
        missed = true;
      })
      .finally(open);
    return () => {
      closed = true;
      source?.close();
      window.clearTimeout(staleTimer);
      flush.cancel();
      window.clearTimeout(retry);
    };
  }, [client]);
  return live;
}
