// The constraint's one sentence (finding #20): Now's banner and Flow's toolbar say the same thing, in the same words,
// from the same count (the waits on the constraint), so a person never reads two numbers for one fact.

export interface ConstraintFacts {
  on_kind: string; // "human", "role" or "resource"
  on_ref: string;
  waiters: number; // how many wait on it directly
}

export function constraintWho(c: ConstraintFacts): string {
  return c.on_kind === "human" ? "you" : c.on_ref;
}

export function constraintHolds(c: ConstraintFacts): string {
  const noun = c.on_kind === "human" ? "item" : "change";
  const n = c.waiters;
  return `holds ${n} ${n === 1 ? noun : `${noun}s`}${c.on_kind === "resource" ? " in its queue" : ""}`;
}

/** "lead holds 1 change", "you hold 3 items", "merge-check holds 5 changes in its queue" */
export function constraintSentence(c: ConstraintFacts): string {
  const who = constraintWho(c);
  const holds = constraintHolds(c);
  return `${who} ${who === "you" ? holds.replace(/^holds/, "hold") : holds}`;
}
