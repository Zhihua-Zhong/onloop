# Decision 0003: role steps run as CI jobs; the Temporal layer is superseded

- **Status:** accepted; point 3 was carried out on 2026-11-05, when the Temporal layer was deleted
  ([decision 0004](0004-onloop-hosts-the-conversation.md))
- **Date:** 2026-11-03
- **Decided by:** the project lead, after a build-or-adopt audit of the supervisor
- **Size:** large (it changes where every role step runs)

## Context

The supervisor (docs/supervisor.md) runs each role step as a bounded headless `claude -p` call. On the unreleased
0.3.0 line its execution layer ran on Temporal:
- a long-running queue workflow per role, the asks workflow, and the intake, heartbeat, attention and wake schedules;
- activities with heartbeats, and the role's retry policy;
- an idempotent step log, and the waiting-out of a step left by a dead worker;
- two services for the host to keep alive (the dev server and the worker).

It was tried because a durable engine was the mainstream answer to the hand-written loop it replaced, and it removed
that loop's timers and leases. On the first real repository it ran read-only and then in a bounded dry run, as planned.
It found three defects that a model step would have hit:
- a step re-keyed by the role's own review;
- session ids reused across attempts;
- an unlogged CLI that only showed up at the first turn.

No live model step ran on it.

The audit compared it with the CI system the projects already use, running jobs on a self-hosted runner. For one role
step per event, CI gives most of what the Temporal layer built:

| Need | CI | Temporal layer |
|---|---|---|
| The trigger | The forge pushes the event to the runner, over an outbound connection, with no polling. | Polls every interval, keyed on (role, item, version). |
| The timeout | A timeout per job and per step. | The role's wall budget and a hard deadline. |
| WIP | Per-item concurrency groups, and the number of runners. | A WIP limit per role. |
| Timers | `schedule`. | Schedules. |
| History | Run history, logs and re-runs in the forge's own UI. | `steps.jsonl` and the server's UI. |
| Operations | The runner service, which the project already has. | Two new services. |

Both cost nothing beyond the subscription.

## Decision

1. **Roles run as CI jobs.** A job runs on a separate self-hosted runner and is triggered by the forge's events: a
   pull request opened, pushed or marked ready; a workflow completing; a schedule; a manual dispatch. Each job runs
   **`onloop step`** for one item. The reviewer role pilots first, in shadow mode, before any live post.
2. **What onloop keeps.** These are the parts CI does not give:
   - **The governed command builder** (`step.build_command`): the role's tool list under `dontAsk`, `--setting-sources`,
     `--strict-mcp-config`, `--json-schema`, the prompt on stdin, a new session id per attempt. A live run refuses
     `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`, and an unlogged CLI.
   - **The silence kill.** A step with no output for `silence` is stopped by its own pid. CI has no such watchdog.
   - **The result-file contract.** The outcome, the numbers, the structured result and the final text, written by
     onloop, so the step needs no write tool. A separate command, or a later job step, does the one write.
   - **The due test from the role table.** `version: head`, `require: {checks: green}` and `unanswered_by`, read fresh
     by GETs, so an early event costs one read.
   - **The asks, the attention rules and the digest.** These move to scheduled jobs.
3. **The Temporal layer is superseded, not deleted yet.** `onloop supervise` and the workflows, worker, activities,
   schedules, planner, messages and step log stay in the tree and their tests stay green until the reviewer pilot has
   run live, so the comparison can be made against them. Then they are deleted in one change, together with the deploy
   files and their docs (the project rule: a reversed rule leaves no flags or dead paths behind). No new work goes into
   them meanwhile: the wake schedule and `after_result`, added on 2026-11-03, are not used by the CI path.
   `after_result` is replaced there by a separate job step.

## Consequences

- **The role table moves into the repository** the jobs run on, which a pull request could change for its own run.
  To close this:
  - jobs that only read run from the base branch's copy (`pull_request_target`, `workflow_run`, `workflow_dispatch`)
    and never check out or execute the head;
  - workflow files and the role table are a reserved path class, so changing them needs the human's approval.
- **The AI runner is separate.** It runs under its own service account with its own subscription login, with no access
  to the human's folders or keys. A posting key reaches a job as a secret, and only in the posting step, never in the
  model step.
- **Lost:**
  - priority ordering by paths: a concurrency group is first in, first out;
  - an automatic retry of a model step, which was rarely right anyway;
  - the outside heartbeat check, which CI cannot do for itself and now needs a dead-man switch outside the host.
- **Measurement stays.** The shadow comparison and the KPIs are read from the CI run history and the uploaded result
  files.
