# Decision 0020: one decision, one ask

- **Status:** Accepted on 2026-11-28, by the onloop lead, as step 3 of the 0.7.0 build plan
- **Date:** 2026-11-28
- **Decided by:** the project lead's direction for 0.7.0 (build prototype F's direction as the product) and the onloop
  lead's rulings of 2026-11-28: new asks carry `effect` by default, the old ones are tidied with their reason and never
  given an invented effect, and the person's answer is one of the recorded options or Done and Decline
- **Size:** medium (schema 11, one new hook, a new page). onloop now tells an agent things; it still acts on nothing

## Context

The KPI plan measured 2.3 asks per decision on the dogfood (M4): agents restated the same question under another
`ref`, another role asked about it, or the ask was prose with no outcome line. A resolution reached nobody: the person
answered in onloop and then relayed the answer by hand. The 0.7.0 spike verified the hook contract that closes both
gaps: Claude Code shows a SessionStart or UserPromptSubmit hook's `additionalContext` to the agent, and a Stop hook's
`decision: block` sends the turn back once with a reason, never in a loop (`stop_hook_active`).

## Decision

1. **A subject key per ask.** The `ref`, normalised: case; `pr:12`, `#12`, `PR#12` are `pr#12`; `issue:7` is
   `issue#7`; a branch's `feat/`, `fix/`, `chore/`, `docs/` or `refactor/` prefix is dropped; `task:<id>` names the
   decision itself. Only asks are keyed (outcome lines, `onloop ask`, adapters); a gate approval, a paused role, a node
   and a wait keep their own rules.
2. **One open decision per subject.** An ask whose key matches an open decision amends it, whoever asks. The decision
   keeps its first author and lists every ask in `task_asks` (time, author, whether it brought a new fact). The author
   restating its own ask replaces the decision's fields, as before; another role joining it only adds what it says.
3. **Options and the answer.** `options=` on the outcome line (and `onloop ask --options`) records 1 to 4 answers,
   each at most 40 characters. The person answers with one of them, or Done or Decline when there are none; nothing
   is generated before the smart layer (plan, section 3). `task.resolved` carries the `answer`.
4. **The agent is told.**
   - Before it asks: its SessionStart and UserPromptSubmit hooks add its open decisions (age, ask count, default).
   - When it asks again with nothing new, or asks without an effect: its Stop is sent back once with the reason. The
     ask is logged either way, so M4 still counts it.
   - When the person answers: the answer is in its context at its next prompt or session start, and that hook's event
     marks it delivered (`delivered_at`).
   A UserPromptSubmit keeps the prompt's length, never its text.
5. **The durable undo.** Until an answer reaches its asker, the person can withdraw it (`task.unresolved`): the one way
   a resolved task opens again, enforced by the table's trigger. After that, a change of mind is a new answer.
6. **Effect by default.** `onloop ask` needs `--effect` and refuses a subject already open (exit 3, with the open id).
   An outcome line without an effect is still taken, counted under Details · Rule alerts and sent back once. The
   person can set such asks aside in one click: resolution `dismissed`, note "format rule, no effect stated", with an
   undo. onloop never writes an effect for them.

### The new event kinds (schema 11), with their OCEL 2.0 event types

| Kind | OCEL 2.0 event type |
|---|---|
| `task.unresolved`, `hook.prompt_submit`, `task.merged` | `answer withdrawn`, `prompt submitted`, `decisions merged` |

`task.merged` (0.7.0 step 5) is the person confirming a merge the smart layer proposed: `merge` closes as superseded by
`keep`, and its asks are counted in `keep`. onloopd's own record of the smart layer, `text.generated`, `text.rejected`
and `text.failed` (source `config`, subject `text:<kind>`), changes no table: the pages read it by the facts' hash.

## Consequences

- Decisions (`#/decisions`) lists the open decisions in the task list's one order, with "asked N×", the askers, the
  recorded options, and Done with each answer and whether it reached its asker. Its count is Home's and the sidebar
  badge's (`tests/test_agreement.py`).
- A carried role gets the three hooks from the carrier; `onloop open` passes them with `--settings` for that run only.
  A session started any other way sees nothing, and its asks are still counted.
- The context is advice the model chooses to follow. Whether it does is measured, as M4, from `task_asks`.
- A carried role idle on an answer gets it at its next prompt; waking it for an answer is the carrier's decision 0011
  wake rules, unchanged.
