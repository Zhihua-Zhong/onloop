import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { uiPost } from "@/lib/api";
import { useToast } from "@/components/ui/toast";

// The person's answer to a decision (0.7.0 plan, section 2, Undo): sent at once, with a toast that offers Undo for 9 s
// (longer while the pointer is on it), and withdrawable from Decisions · Done until it reaches the role that asked,
// at that role's next prompt. A withdrawal is `task.unresolved`; after the answer arrived, a change of mind is a new
// answer. Every answer is a request from a local user, not verified, never an approval (decision 0011).

export type Resolution = "done" | "declined";

export function useAnswers() {
  const client = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<{ id: string; text: string } | null>(null);
  const refresh = () => client.invalidateQueries();

  const withdraw = async (id: string) => {
    setBusy(id);
    try {
      await uiPost("/ui/tasks/withdraw", { id });
      setError(null);
      toast("Withdrawn: the decision is open again.");
    } catch (e) {
      setError({ id, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
      await refresh();
    }
  };

  const answer = async (id: string, who: string | null, resolution: Resolution, option?: string) => {
    setBusy(id);
    try {
      await uiPost("/ui/tasks/resolve", { id, resolution, ...(option ? { answer: option } : {}) });
      setError(null);
      const said = option ?? (resolution === "done" ? "Done" : "Declined");
      toast(`${said}. ${who ?? "The asker"} sees it at its next prompt.`, () => void withdraw(id));
    } catch (e) {
      setError({ id, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
      await refresh();
    }
  };

  const tidy = async (ids: string[]) => {
    setBusy("tidy");
    try {
      await uiPost("/ui/tasks/tidy", { ids });
      setError(null);
      toast(`${ids.length} ${ids.length === 1 ? "ask" : "asks"} set aside: format rule, no effect stated.`, () => {
        void Promise.all(ids.map((id) => uiPost("/ui/tasks/withdraw", { id }))).then(refresh, refresh);
      });
    } catch (e) {
      setError({ id: "tidy", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
      await refresh();
    }
  };

  // a merge the smart layer proposed and the person confirms (0.7.0 plan, section 4.3): `merge` closes into `keep`
  const merge = async (keep: string, gone: string) => {
    setBusy("merge");
    try {
      await uiPost("/ui/tasks/merge", { keep, merge: gone });
      setError(null);
      toast("Merged: one decision now, with every ask counted in it.");
    } catch (e) {
      setError({ id: "merge", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
      await refresh();
    }
  };

  return { answer, withdraw, tidy, merge, busy, error };
}
