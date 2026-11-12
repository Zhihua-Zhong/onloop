# Decision 0004: onloop hosts the conversation

- **Status:** accepted
- **Date:** 2026-11-05
- **Decided by:** the project lead, with onloop's lead
- **Size:** large (it sets what onloop is, and the order of the next two releases)

## Context

Decision 0003 moved role steps into CI jobs: each job runs `onloop step` for one item, and CI gives the trigger, the
timeout, the concurrency and the run history. That settles how one step runs. It does not settle who owns the loop
around the steps:
- who holds the goals and the clock;
- who delivers an event into the role session that should act on it;
- who tracks which role waits on which, and where the constraint is;
- who watches results, not just runs.

A desktop agent client's own features (routines, scheduled tasks, messages between sessions) were considered as that
owner. They are not the product path: each scheduled run of a routine starts in manual permission mode, so a run waits
for a person and the clock stalls.

## Decision

1. **onloop is the operations layer for AI agent teams.** It is a loop owner that runs outside any session and stays
   up. It holds goals and a clock, delivers events into role sessions, tracks who waits on whom and computes the
   constraint, and watches results.
2. **onloop carries the sessions.** A role session is driven by headless `claude -p --resume <session id>`, and the
   person talks to it through onloop's own interface. Permissions come from the governed command builder
   (`step.build_command`) and the role's allowlists, never from a client's interactive prompts.
3. **The data model is generic; the delivery is narrow.** The core nouns are role, item, wait, shared resource,
   schedule, event and decision. A forge (GitHub today), a project's gate, its locks and its role list are plugins
   configured in the project's own repository. onloop ships nothing specific to one project.
4. **The first version serves one project's lead.** Its measure is that project's development progress: work moves
   faster, and human intervention keeps falling. Acceptance is written from that project's stories, in generic terms.
5. **Release order.**
   - 0.3.0 observes and acts on nothing: scheduled-job results, the constraint view, handoffs nobody owns, system
     time, checks that fail closed, and a report of what onloop would have done against what happened.
   - 0.4.0 acts: delivery into sessions, reserved gaps, a safe stop, and no clutter and no expiry, with onloop's own
     dead-man switch.
   - The release candidates cut before this decision (0.3.0rc1 and 0.4.0rc1) are not published. Their branches are
     kept as `archive/*` tags.
6. **Mainstream first.** Each story names the mainstream tool that already does it, or the evidence that none does.
   That research runs before each release proposal and at least monthly.

## Consequences

- The Temporal execution layer, superseded by decision 0003, is deleted. The governed command builder, the silence
  kill, the result-file contract and the role table stay: they are what a hosted session is started with.
- Open questions, to be decided separately:
  - how the person-facing interface relates to the existing front ends (the work-graph panel, the canvas, the
    workbench and the dashboard); one is expected to remain;
  - whether the polling loop (runner, intake, dispatcher, handlers) is retired or becomes the event intake;
  - the baseline numbers for the progress measure, agreed with the project before 0.3.0.
