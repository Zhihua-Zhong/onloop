import type { QueryClient } from "@tanstack/react-query";
import { useSyncExternalStore } from "react";
import { uiPost } from "@/lib/api";
import { pendingMarks, type Pending, type Resolution } from "@/lib/pending";

// The page's one store of marks waiting to be sent (finding #19). It outlives a card that collapses or a view that
// changes, so an undo is still possible after the row closes.

let client: QueryClient | null = null;
const errors = new Map<string, string>();

export function setMarksClient(c: QueryClient) {
  client = c;
}

export const marks = pendingMarks((id: string, resolution: Resolution) => {
  uiPost("/ui/tasks/resolve", { id, resolution })
    .then(() => {
      errors.delete(id);
      return client?.invalidateQueries();
    })
    .catch((e: unknown) => {
      errors.set(id, e instanceof Error ? e.message : String(e));
      client?.invalidateQueries();
    });
});

export function markError(id: string): string | null {
  return errors.get(id) ?? null;
}

export function usePending(id: string): Pending | null {
  return useSyncExternalStore(marks.subscribe, () => marks.get(id));
}
