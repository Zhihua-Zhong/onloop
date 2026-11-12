import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

export type Live = { state: "connecting" | "live" | "stale"; since: number | null };

const STALE_AFTER_MS = 5_000;

// Live updates from onloopd's event stream (`/api/stream?plain=1`, a GET). A burst of new events refetches what the
// open view reads, once. When the stream is down for more than five seconds the state is "stale" and the shell says
// so: the page keeps what it last read, labelled with when (fail closed, never silently old). It reconnects by itself.
export function useLive(): Live {
  const client = useQueryClient();
  const [live, setLive] = useState<Live>({ state: "connecting", since: null });
  const after = useRef(0);
  useEffect(() => {
    let source: EventSource | null = null;
    let staleTimer: number | undefined;
    let flush: number | undefined;
    let retry: number | undefined;
    let closed = false;
    let lostAt: number | null = null;
    const open = () => {
      if (closed) return;
      source = new EventSource(`/api/stream?plain=1&after=${after.current}`);
      source.onopen = () => {
        window.clearTimeout(staleTimer);
        lostAt = null;
        setLive({ state: "live", since: null });
      };
      source.onmessage = (e: MessageEvent) => {
        const seq = Number(e.lastEventId);
        if (Number.isFinite(seq) && seq > after.current) after.current = seq;
        window.clearTimeout(flush);
        flush = window.setTimeout(() => client.invalidateQueries(), 400);
      };
      source.onerror = () => {
        source?.close();
        // The clock starts at the first failure and runs through the retries; only a reconnect stops it.
        if (lostAt === null) {
          lostAt = Date.now();
          const since = lostAt;
          staleTimer = window.setTimeout(() => setLive({ state: "stale", since }), STALE_AFTER_MS);
        }
        window.clearTimeout(retry);
        retry = window.setTimeout(open, 2_000);
      };
    };
    // The page has just read everything: the stream starts at the newest event.
    fetch("/api/events?latest=1&limit=1", { method: "GET", cache: "no-store" })
      .then((r) => (r.ok ? r.json() : { events: [] }))
      .then((b: { events: { seq: number }[] }) => {
        after.current = b.events[0]?.seq ?? 0;
      })
      .catch(() => undefined)
      .finally(open);
    return () => {
      closed = true;
      source?.close();
      window.clearTimeout(staleTimer);
      window.clearTimeout(flush);
      window.clearTimeout(retry);
    };
  }, [client]);
  return live;
}
