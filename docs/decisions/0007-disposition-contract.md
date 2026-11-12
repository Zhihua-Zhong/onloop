# Decision 0007: the disposition contract

- **Status:** Accepted on 2026-11-09
- **Date:** number reserved 2026-11-09; written 2026-11-09
- **Decided by:** the project lead, approving the technical design of 2026-11-09; the contract's details come from the
  first customer's answer of 2026-11-09, which accepted it with the changes recorded here
- **Size:** medium (it adds one line to every role turn and a detector that reads it)

## Context

A role session that ends a turn on a stated intention ("I will set X before opening the PR") with nothing scheduled to
wake it stops silently: nothing records that work is owed, and the person finds out by asking. A role that says it
will continue when a background process finishes stops just as silently when that process dies (story 11: a merge
queue's two background processes reached their cap at night, every role was correctly idle, and three approved
changes did not merge for eight hours).

Judging free text for intentions is unreliable; a product that tried it removed it. Paperclip's runtime instead requires
every run to end in a recorded disposition. onloop takes that rule, as a line the role writes and onloop parses.

## Decision

1. **Every role turn ends with one outcome line.** The last non-empty line of the turn's final message is:

   ```
   outcome: <value> [key=value ...]
   ```

   It is one line, not prose and not inside a code fence. onloop reads it from the Stop hook's
   `last_assistant_message`; nothing else in the message is read for it.
2. **The values.**

   | Value | Means | Keys | Waits on (in the ledger) |
   |---|---|---|---|
   | `done` | this item is finished | `ref` optional | nothing |
   | `idle` | the queue is empty; nothing is owed | none | nothing |
   | `in_review` | the work waits on a reviewer; the author still owns it | `ref` and `owner` optional | the `owner` role, if one is named, with reason `missing_verdict`; cleared by that role's next turn naming the same `ref` |
   | `blocked` | it cannot move until someone else acts | `owner` and `ref` required | the `owner` role, or the human when `owner=human`, with reason `blocked` and the `ref`; cleared by the blocked role's own next turn |
   | `handoff` | it now belongs to another role | `owner` and `ref` required | the `owner` role (the receiver), with reason `handoff` and the `ref`; cleared by the receiver's next turn naming the same `ref` |
   | `continue` | the role will go on when its waker fires | `ref` and `waker` required | the waker resource, with reason `continue`; cleared by the role's own next turn |

   Every wait shows in the constraint view with its reason, so a block, a handoff or a review that nobody picks up is
   visible as what it is (stories 2 and 4).

   - `ref` is a pull request, an issue or a file, so the claim can be checked against the record of truth instead of
     trusted.
   - `owner` is a role name.
   - `waker` names what will wake the role: `pid:<process id>`, `cron:<schedule id>` (a schedule declared to onloop)
     or `monitor:<name>` (a check onloop can call). onloop checks that the waker is alive, not only that one was
     named.
   - A value has no spaces, or is written in double quotes without a double quote inside. A key may appear once.
     A key a value does not take is an error.
3. **Fail closed.** A missing line, an empty or undecodable message, an unknown value, a missing or extra key, or a
   malformed waker is a turn without a valid outcome. It is never read as `done` or `idle`.
4. **Rollout.**
   - **Observe-only first.** For about a week, onloop reports per role the turns with a missing or unverifiable
     outcome, and what it would have done, without acting.
   - **Then, by opt-in per role, a corrective wake.** At most one per turn: the role is asked once to state its
     outcome. If it still does not, the item goes to the coordinator with its age.
5. **The paragraph lives once.** The instruction that asks for the line is one paragraph in the team's shared role
   template, and every role charter points to it, so there are no copies to drift. onloop ships the generic paragraph
   in [examples/roles/outcome-line.md](../../examples/roles/outcome-line.md).

## Consequences

- The overlay ledger already holds what the contract needs ([decision 0006](0006-overlay-ledger.md)): the outcome
  values, a `ref` on items and waits, and `waker` resources. A `continue` becomes a wait on its waker, so a dead waker
  shows in the constraint query as a wait on a dead resource.
- The parser (`src/onloop/disposition.py`) is the one place that reads the line. The detector that raises a turn
  without a valid outcome, and the one that checks wakers, read the parser's result.
- The customer changes its role prompts after its freeze lifts. Until then, onloop's reports count missing outcomes
  without consequences.
