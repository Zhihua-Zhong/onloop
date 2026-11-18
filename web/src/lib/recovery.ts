// How a view comes back when onloopd does (the lead's finding on 28edd47): a page that loaded while onloopd was down
// kept "Failed to fetch" after onloopd returned. Pure, so node:test can hold the rules.
//
// - A query in error is tried again every few seconds, not at the view's normal pace.
// - The last good data stays on screen while a refresh fails, with a line saying so; an error replaces nothing.
// - When the event stream reconnects after a loss, every query is fetched again (lib/live.ts).

export const RETRY_WHILE_DOWN_MS = 3_000;

export function refetchEvery(normalMs: number): (q: { state: { status: string } }) => number {
  return (q) => (q.state.status === "error" ? RETRY_WHILE_DOWN_MS : normalMs);
}

export type ViewState = "loading" | "data" | "data-stale" | "down";

export function viewState(status: "pending" | "error" | "success", hasData: boolean): ViewState {
  if (hasData) return status === "error" ? "data-stale" : "data";
  return status === "error" ? "down" : "loading";
}

// "Failed to fetch" is the browser's word for "nothing answered": say what it means and what happens next.
export function downMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : "";
  if (!raw || /failed to fetch|networkerror|load failed/i.test(raw)) {
    return "onloopd cannot be reached. The page tries again every few seconds and shows the map as soon as it answers.";
  }
  return raw;
}

// A view's code that loads on first use (Map, Flow) is fetched again until it arrives. A failed dynamic import would
// otherwise stay failed for the life of the page, and with nothing to catch it the whole page went blank.
export function retrying<T>(
  load: () => Promise<T>,
  waitMs = RETRY_WHILE_DOWN_MS,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((r) => setTimeout(r, ms)),
): () => Promise<T> {
  return async () => {
    for (;;) {
      try {
        return await load();
      } catch {
        await sleep(waitMs);
      }
    }
  };
}
