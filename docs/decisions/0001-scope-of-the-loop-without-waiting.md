# Decision 0001: scope and settings of the loop without waiting

- **Status:** accepted
- **Date:** 2026-10-12
- **Decided by:** the project lead (lead assistant), after review of docs/design/loop-without-waiting-plan.md
- **Size:** medium

## Context

The 0.3 plan left five choices open (section 13 of the plan as first written). Each changes what the runner may do or
where it keeps its state.

## Decision

1. The autogate fires on `review.submitted`, `check.completed`, `verdict.reversed` and `pr.ready`, always through
   `gate.run`; turning it on counts as a loosening of the policy.
2. `automation.cross_owner: reviewer_ack` is opt-in in 0.3. The default changes at 1.0, with a changelog note.
3. There is no notification file in 0.3; the exit of `onloop slot wait` is the wake-up.
4. The runner's database is kept outside every worktree, at `$ONLOOP_DB`.
5. Running the runner on a real project for a shadow week in observe mode is the project owner's call, because it is a
   long-running process on their host. It cannot start before M2 and should not overlap a demo week.

## Consequences

The autogate's trigger set is fixed and testable. A project that opts into `reviewer_ack` gets the stricter check at
once, and the others see no change until 1.0. A session that cannot run a long command has no wake-up in 0.3.
