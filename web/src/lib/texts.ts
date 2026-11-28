import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/api";

// The smart layer's texts (src/onloop/brief.py, GET /api/texts): written by onloop over the computed facts, checked
// (every number is one of the facts, every suggestion one of the recorded options) and valid only for the facts they
// were written from. A kind with no text for today's facts is the facts alone; `stale` says a newer one is on its way.

export interface TextMeta {
  facts_hash: string;
  written_at: string | null;
  model: string | null;
  stale: boolean;
}

export interface BriefText extends TextMeta {
  text: { sentences: { text: string; about: string | null }[] } | null;
}

export interface DecisionText {
  id: string;
  title: string;
  subject: string;
  suggested: string | null;
  grounds: string | null;
}

export interface DecisionsText extends TextMeta {
  text: { decisions: DecisionText[] } | null;
}

export interface MergeText {
  keep: string;
  merge: string;
  why: string;
}

export interface MergesText extends TextMeta {
  text: { merges: MergeText[] } | null;
}

export interface TextsModel {
  brief: BriefText;
  decisions: DecisionsText;
  merges: MergesText;
}

export const textsApi = { all: () => get<TextsModel>("/api/texts") };

export function useTexts() {
  return useQuery({ queryKey: ["texts"], queryFn: textsApi.all, refetchInterval: 60_000 }).data;
}

/** The written title, subject and suggestion of one decision, or null (the facts then stand alone). A suggestion that
 * is not one of the decision's options now is dropped here too. */
export function decisionText(t: TextsModel | undefined, d: { id: string; options: string[] }): DecisionText | null {
  const hit = t?.decisions.text?.decisions.find((x) => x.id === d.id);
  if (!hit) return null;
  return hit.suggested && !d.options.includes(hit.suggested) ? { ...hit, suggested: null, grounds: null } : hit;
}

export const shortHash = (h: string) => h.slice(0, 7);
