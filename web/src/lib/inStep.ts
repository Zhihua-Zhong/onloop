import { useRef } from "react";
import { useIsFetching } from "@tanstack/react-query";

// The counts a page shows come from three reads: GET /api/home (Home's count, brief and button), GET /api/decisions
// (Decisions and the sidebar badge) and GET /api/texts (the written titles and brief). After a write (an answer, its
// Undo) all three are fetched again, and they land apart, so for a moment the badge said 7 while Home said 6 (the onloop
// lead's step 6 review). Each read shown through useInStep keeps showing what it showed while another of the three is
// still being fetched, so they all change in the same render: the page never shows two counts at once.

export const IN_STEP = ["home", "decisions", "texts"] as const;
export type InStepKey = (typeof IN_STEP)[number];

export function useInStep<T>(key: InStepKey, data: T | undefined): T | undefined {
  const others = useIsFetching({ predicate: (q) => IN_STEP.includes(q.queryKey[0] as InStepKey) && q.queryKey[0] !== key });
  const shown = useRef<T | undefined>(data);
  if (others === 0 || shown.current === undefined) shown.current = data;
  return shown.current;
}
