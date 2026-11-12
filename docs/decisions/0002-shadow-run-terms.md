# Decision 0002: terms of the first shadow run

- **Status:** accepted
- **Date:** 2026-10-12
- **Decided by:** the project lead, on the lead assistant's recommendation
- **Size:** large (a long-running process on the owner's host; leaving observe mode is a separate yes)

## Context

0.3 adds a runner that polls GitHub every minute, writes an event log, and (in act mode) merges through the gate.
Before it acts on a real project it should run beside the people and agents who act the old way, and be compared with
them (plan section 10). The first project is the one onloop was extracted from.

## Decision

1. **Start.** Only after the first project's current demo has shipped, and only when both hold: the Reviewer has cleared
   the milestones the runner needs (M1 and M2), and the host's operator gives the slot for the start, because the runner
   adds a `gh` call every minute.
2. **How it runs.** `onloop run` with `automation.mode: observe`, as the project's agents App through
   `onloop as-app`. It is read-only, opens no listening port, and can be stopped at any time. Its database is
   `$ONLOOP_DB`, outside every worktree.
3. **The policy is the project's own, derived.** The instance policy is generated from the project's roles and gate
   settings by a script, kept outside the project's repository, and never edited by hand. The database write window is
   a slot with `approver: human`.
4. **Leaving observe for act.** Only on the criteria of plan section 10 (the A3 volume, zero dangerous disagreements in
   `onloop kpi --shadow`, no runner gap), after at least 7 days, and with a **separate yes** from the project lead,
   asked for with the `kpi --shadow` output attached. Changing `automation.mode` to `act` is itself a loosening.

5. **Amendment (2026-10-12): the schedule follows the handlers.** In M2 the runner registers no handlers, so observe
   mode has nothing to compare before the autogate (M5). Once M2 is cleared the runner may start after the first
   project's demo, running intake only, to collect the baseline (lead time, idle time with work waiting, reverts). The
   7-day clock for the exit criteria starts only when the preflight and autogate handlers are registered in observe
   mode. Act mode still needs the project lead's yes.
6. **Two identity rules.** The runner renews its own installation token from the key path in its environment, and it
   refuses to start if a reviewer key variable is set there: the runner never acts as the reviewer.

7. **Amendment (2026-10-11): observe mode does not run the project's checks.** A project check may be a command the
   shadow runner must never run (for the first project, a verification against its database). Every observed merge is
   labelled `evidence_only: checks not run`. In `kpi --shadow`, a disagreement where onloop would have merged but the
   real gate refused **only for project checks** (code `G091`) is classed `checks-only`: listed, not counted as
   dangerous. A disagreement for any other reason, including any other refusal code, stays dangerous. Running the checks
   in act mode will need the project's host and database slot through `slots.py`, and is asked for with act mode.
8. **Amendment (2026-10-11): a hard stop for observe.** The instance policy turns `autogate` and `autoready` on, so that
   the shadow run records what they would do; in observe mode they write nothing. The step that grants authority is
   observe to act, and it needs its own yes. Because the instance policy lives outside every repository the gate protects,
   `onloop run --observe-only` forces observe whatever the policy says and refuses to start if the policy says act. The
   runbook starts the runner only with that flag, and the generator emits `mode: observe`.
9. **Amendment (2026-10-11): which test measures what.** The shadow run's volume criterion is the floor of
   `onloop kpi --shadow` (observed rows, at least one of each kind). `onloop acceptance` (A1 to A3, F1, F2, R1) is the
   test of the **act-mode** week: its A3 counts real gate events, which observe mode does not produce. Passing
   acceptance is not required to leave observe, and failing A3 during the shadow week is expected.
10. **Amendment (2026-10-11): unexplained disagreements fail closed.** A "would merge" followed by nothing within the
    window is *unexplained*: onloop cannot tell a person's reason from its own mistake. Unexplained rows block leaving
    observe mode until a person classifies each one in a file given to `kpi --shadow --classified`; each line carries a note, who
    ruled on it and the evidence it rests on, and a line without all three is refused. If the
    policy names `automation.external_gate_check`, a failing run of that check on the same head explains the row as an
    external refusal: listed, not dangerous. Setting or changing that name is a loosening (the human approves it).
    A recorded runner stop excuses at most `automation.max_stop_minutes` of a gap (R1); a longer silence is a gap.

## Consequences

Seven or more days of observation precede any act-mode merge. A failed criterion keeps observe mode on. Until the
start conditions hold, nothing runs on the host; all of the preparation (policy, runbook, tests) is local.
