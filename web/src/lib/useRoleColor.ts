import { useQuery } from "@tanstack/react-query";
import { useCallback } from "react";
import { agentsApi } from "@/lib/api";
import { colorFor } from "@/lib/roleColor";

// The team's colours: each role's slot comes from the team GET /api/agents reports (the same query the sidebar's
// "Agents now" keeps), so every page colours a role the same way.
export function useRoleColor(): (role: string) => string {
  const q = useQuery({ queryKey: ["agents"], queryFn: agentsApi.all, refetchInterval: 30_000 });
  const team = (q.data?.agents ?? []).map((a) => a.role).join("\u0000");
  return useCallback((role: string) => colorFor(team ? team.split("\u0000") : [], role), [team]);
}
