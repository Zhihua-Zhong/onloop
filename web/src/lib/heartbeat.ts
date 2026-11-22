// onloopd's heartbeat, read once for the whole page (GET /api/heartbeat, every HEARTBEAT_POLL_MS). The header's
// liveness and Now's Health card both read this query, so they never show two different heartbeats.
import { useQuery } from "@tanstack/react-query";
import { HEARTBEAT_POLL_MS } from "@/lib/tick";

export interface Beat {
  now: string;
  at: string | null;
  age_s: number | null;
  period_s: number;
  last_seq: number | null;
  read_only?: boolean; // a server with no writer serves a copy: nothing new can arrive
  data_up_to?: string | null;
}

export function useHeartbeat() {
  return useQuery({
    queryKey: ["heartbeat"],
    queryFn: async (): Promise<Beat> => {
      const r = await fetch("/api/heartbeat", { method: "GET", cache: "no-store" });
      if (!r.ok) throw new Error(`onloopd answered ${r.status}`);
      return r.json();
    },
    refetchInterval: HEARTBEAT_POLL_MS,
    refetchIntervalInBackground: true,
    retry: false,
  });
}
