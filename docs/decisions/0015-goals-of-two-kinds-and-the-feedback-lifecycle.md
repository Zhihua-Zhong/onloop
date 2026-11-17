# Decision 0015: goals of two kinds, and the feedback lifecycle

- **Status:** Accepted on 2026-11-18
- **Date:** 2026-11-18
- **Decided by:** the onloop lead, on the overview plan (revised 2026-11-18) and the first customer's answers
- **Size:** medium (new tables in ledger schema 9; derived state only; nothing acts)

## Context

A person on the loop gives two kinds of direction. The first is a **checklist**: numbered items, ticked one by one
(decision 0014). The second is **feedback**: about ten items given in one sitting, each of which the team researches,
specifies, builds and shows. A tick per item shows "0 of 10" all day while most of the work is under way. A checklist
bar cannot show where each item stands.

## Decision

1. **Two kinds of goal.**
   - **A checklist:** a bar of items done out of its denominator, with the denominator's date (decision 0014).
   - **A feedback batch:** a bar of items accepted out of items given. Each item moves through **six stages**:
     researched → specified → **spec accepted** → built → **shown** → **accepted**.
2. **Three of the stages are the person's gates.** "Spec accepted", "shown" and "accepted" are each a human task
   (decision 0013), opened when the stage before is reached. They are list entries, not messages to a role.
3. **A stage is derived from evidence, never typed by hand.** The evidence is:
   - an outcome-line key (`feedback=<id>`);
   - a PR body line (`Feedback: <id>`);
   - a marker line in a committed file under configured paths;
   - a gate task's resolution;
   - a tracker read through an adapter.
4. **A stage the team declares is kept, and marked.** A team that exports its own view of each item's stage (the data
   contract, decision 0017) has it shown with a "reported by the team" mark. When links prove a stage too, onloop
   derives it, and a disagreement is flagged on the cell; one is never silently preferred. Each export's stamp is its
   own event, so a stage's age is measured from its first stamp.
5. **Fail visible.**
   - A link to an unknown item is kept and raised by reconciliation.
   - A build before the spec was accepted is flagged `skipped_gate`.
   - A revert moves the item back, flagged `regressed`.
   - An item is never closed by onloop.
6. **Recurrence is explicit.** An item that repeats an earlier one says so (`recurs_of`). Text is never matched.
7. **Grouping is by origin, never by role:** a checklist, or a dated batch.

## Consequences

- Ledger schema 9 adds `feedback_batches`, `feedback`, and `feedback_links`, the gate index, and the invariants
  (overview plan §2). They are projected from `onloop feedback` and from the contract's `feedback.*.reported` events.
- The map (decision 0018) draws a batch as six stage blocks at zoom 1.

## Alternatives considered

| Alternative | Verdict |
|---|---|
| Statuses set by hand, as feedback tools do | Rejected: the customer excludes typed state |
| A checklist tick per feedback item | Rejected: it shows "0 of 10" while the work moves |
| Matching recurrences by text | Rejected: a guess, and wrong silently |
