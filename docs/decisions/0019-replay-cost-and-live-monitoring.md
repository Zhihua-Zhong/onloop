# Decision 0019: replay, cost and live monitoring (R7a)

- **Status:** Accepted on 2026-11-20, by the onloop lead, with the OCEL 2.0 mapping added to §5. §1's steering was
  corrected on 2026-11-20 to match the code, on the onloop lead's ruling: carried turns stay at `project`
- **Date:** 2026-11-20
- **Decided by:** the project lead's rulings of 2026-11-20 (R7 has two parts, and replay and monitoring come first;
  telemetry stays per process), with the onloop lead's acceptance of spikes S1, S2 and S3
- **Size:** large (a new main view, a new ingest path and schema 10). Nothing new acts, except a launcher that the
  person runs

## Context

A person on the loop cannot see what each agent did, how long it took, or what it cost. The Claude app does not show
this for a team of sessions. The R7 experiments found that a replay of the work over time is valuable on its own,
before any plan is drawn on it.

Four spikes measured the ground (in the project's notes, not the repository):
- **S1:** Claude Code's own OpenTelemetry export works on a subscription login with no API key. It gives one
  `api_request` event per model call, with tokens, a timestamp and a turn id (`prompt.id`).
  - A settings file's `env` overwrites the shell, and a repository's project settings are ignored for the exporter
    and endpoint variables. Only `none` still applies from them.
  - So a session is steered only by its own setting sources and environment.
- **S2:** without a record of which step a session is on, "the step named in the turn's opening message, else the
  next commit's step" attributes 96% of step tokens correctly.
  - No rule can tell overhead (reports, tooling) from step work, and overhead was half the tokens.
- **S4** (after acceptance, before a8): a local settings file's `none` does not stop a session whose own environment
  selects `otlp`, and still stops one without it. When the user settings file is a source, its `env` wins over the
  process's, so onloop's environment cannot be guaranteed there.
- **S3:** a replay frame computed from per-lane prefix sums costs about 0.02 ms, even at a million events. Folding
  every event up to the cursor costs 17 ms at 100 000 events and 247 ms at a million.

## Decision

### 1. Telemetry: per process, into onloopd, identity dropped on arrival

- **Steering:** every session onloop starts gets onloop's own OTel environment, with every inherited `OTEL_*`
  variable removed first: OTLP/HTTP JSON to onloopd on the loopback address, with its token, onloop's role as the one
  resource attribute, and prompt, response and tool content off. It never sends to another project's collector. No
  session onloop starts loads the user settings file, whose `env` would win over the process's (S4).
  - **A carried turn** runs with `--setting-sources project` and `--restricted`, so no settings file loads. Its hooks
    come only through `--settings`. A restricted turn must not pick up permissions someone granted interactively, so
    `local` is not added (the onloop lead's ruling, 2026-11-20).
  - **A session from `onloop open`** runs with `--setting-sources project,local`. A local file's `none` does not stop
    it, since its own environment selects `otlp` (S4).
- **The receiver is in onloopd:** `POST /v1/metrics` and `POST /v1/logs`, JSON only, on the same loopback-only server
  as the page.
  - It drops `user.email`, `organization.id`, `user.account_uuid`, `user.account_id` and `user.id` before anything is
    written.
  - It keeps the `api_request` fields (below) as ledger events.
  - A body that is not OTLP/JSON is answered and stored nowhere.
- **a8, `onloop open <role>`:** it starts the person's own interactive Claude Code session for an onloop role, with
  the same setting sources and environment. So the person's onloop sessions are measured too.
- **onloop's own folders** set the three exporter selectors to `none` in their `.claude/settings.local.json`. An
  onloop session started any other way therefore exports nothing. That was done on 2026-11-20, on the user's yes.
- **Tokens are the measure.** Cost in dollars is shown, if at all, as "an estimate at list price". Claude Code says
  so, and on a subscription it is not billing.

| Option | Source | Verdict |
|---|---|---|
| Claude Code's own OTLP export, per process, into onloopd | S1; Claude Code's monitoring docs (`monitoring-usage`) | **Chosen**: mainstream, and no download |
| Parse the transcripts' usage fields only | S2; what experiment 2 did | Kept as the fallback for sessions onloop did not start |
| A separate OpenTelemetry Collector | the OpenTelemetry project | Rejected for now: a download, and onloopd's receiver is enough |
| onloop as a routing proxy for every project's telemetry | the project lead's ruling, 2026-11-20 | **Rejected**: another team's data would pass through onloop |
| Steering by a repository's project settings | Claude Code's settings reference: the OTel exporter variables are ignored there | Impossible, except to switch export off |

### 2. Attribution: dispatched work records its step; the rest by rule, labelled

- **When onloop dispatches the work** (a task, a carried turn, `onloop open <role>`), it records the step on the
  session as it starts. No inference is needed.
- **Otherwise, rule E:** the step named in the turn's opening message (a task, a PR, a branch, a node trailer), else
  the step of the next commit on that branch. Every figure from it reads "attributed by rule".
- **A "between steps" bar** holds the tokens and time of turns no step names. They are never charged to the nearest
  step: S2 found that would about double each step's cost.

| Option | Source | Verdict |
|---|---|---|
| Record the step when onloop dispatches | S2's recommendation; R7b's b1 | **Chosen**, wherever onloop starts the work |
| Rule E: the opening message, else the next commit | S2: 96% of step tokens | **Chosen** for the rest, labelled |
| The turn's own tool calls | S2: 78% | Rejected: a turn's tools name many steps |
| `prompt.id` and `session.id` alone | S1 | Not a step: they group a turn and a session |
| A switch log the agents write | the R7 pilot's `worklog.jsonl` | Rejected for the product: it depends on discipline, and the pilot's own log had gaps |

### 3. The replay data model: indexed, served in windows

- **On the page:** per lane, sorted state-change times with prefix sums of each kind of time, and sorted token
  times with a prefix sum. A frame is two binary searches per lane.
- **Four kinds of time:** working, queued, waiting on the person, and harness.
- **onloopd serves windows,** never the whole history: `GET /api/replay?from=&to=&lanes=`. It answers in columns
  (times, kinds, lanes, values as parallel arrays), plus each lane's totals at `from`. So a frame never needs
  anything before the window.
- **Index building:** the page builds the index once per window, in a web worker at large windows. S3 measured
  0.8 s for a million events.
- **Drawing:** only the events inside the visible time range are drawn.

| Option | Source | Verdict |
|---|---|---|
| Per-lane prefix sums, binary search per frame | S3: about 0.02 ms per frame at 1 M events | **Chosen** |
| Fold every event up to the cursor (live.html's `stateAt`) | S3: 247 ms per frame at 1 M events | Rejected for the product |
| Snapshots in the ledger every N events | the event-sourcing pattern | Not needed while the page's index is this fast; kept in reserve for months-long windows |
| The whole history as one JSON file | the R7 pilot's `live.json` | Rejected: tens of megabytes at a million events |
| A trace-timeline library (as Jaeger's UI uses) | Jaeger; Chrome DevTools' Performance panel | The idiom is adopted (lanes, a scrubber, events as marks). A library is chosen in the plan only if one is already in `node_modules` |

### 4. Navigation: Now, Replay, and the map's overview

- **Now** stays, as the person's inbox.
- **Replay** becomes the main view: time-proportional lanes, with Activity's lanes folded in. It plays at 1×, 10×,
  60× or 600×, jumps between events, and shows each lane's four kinds of time, cost, and products at the cursor.
- **Map zoom 0** stays, as the overview that opens into Replay.
- **Flow (Graph and Roadmap) and the separate Activity page are retired.** Before any code is removed, the builder
  writes a list of what moves and what goes, view by view, and the project lead confirms it.

| Option | Source | Verdict |
|---|---|---|
| Now + Replay + Map zoom 0 | the project lead's direction, 2026-11-20 | **Chosen** |
| Replay as a sixth view beside the others | the 0.5.0 navigation | Rejected: the views were already too many (decision 0018's context) |
| Replay inside the map, as zoom 3 | decision 0018's reserved levels | Rejected: replay is about time, the map about goals; zoom 3 and 4 stay reserved |

### 5. Schema 10: the new event kinds

- **The source `otel`:** an event from onloopd's OTLP receiver. It is a migration in the pattern of version 8, since
  the source list is a CHECK.
- **New kinds:**
  - `cost.request`, from `api_request`: `session.id`, `prompt.id`, `model`, `input_tokens`, `output_tokens`,
    `cache_read_tokens`, `cache_creation_tokens`, `duration_ms`, `request_id`, `query_source`. `cost_usd` is kept,
    labelled an estimate.
  - `turn.prompted`, from `user_prompt`: `session.id`, `prompt.id`, and the length only.
  - `tool.ended`, from `tool_result`: `session.id`, `prompt.id`, `tool_name`, `success`, `duration_ms`. No
    parameters. Contract 1.x keeps the same name reserved for a team's export.
  - `step.started` and `step.ended`, logged by onloop when it dispatches work, and by `onloop open`: `session.id`,
    and the step (a task, a PR, a branch or a node).
  - `session.opened`, from `onloop open`: the role and `session.id`.
- **Nothing identifying** is stored: the receiver's drop list applies before the event is written.

**Each kind as OCEL 2.0** (Object-Centric Event Log 2.0, the process-mining exchange standard: <https://www.ocel-standard.org/>,
specification at <https://arxiv.org/abs/2403.01975>). The project lead asked for this mapping as the first step toward
a universal work interface for human and AI teams. It follows the project's mapping of the data contract to OCEL. So
schema 10 needs no reshaping when human sources (a chat, a tracker, meeting minutes) arrive later: a person's work
becomes the same events on the same object types.
- **Object ids** are typed: `session:<id>`, `prompt:<id>`, `role:<id>`, `step:<kind>:<ref>`, `tool:<name>`.
- **Events are atomic in OCEL.** So a duration is an attribute (`duration_ms`), and a step's span is its two events,
  `step started` and `step ended`.
- **The source `otel` is provenance,** not process data. It is an event attribute at most.

| Kind | OCEL event type | Event attributes | E2O, object (qualifier) | O2O, object to object (qualifier) |
|---|---|---|---|---|
| `cost.request` | `model request` | `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `duration_ms`, `query_source`, `cost_usd` (an estimate) | session (`in session`), prompt (`for prompt`), role (`actor`) | session to role (`run by`) |
| `turn.prompted` | `prompt received` | `length` | session (`in session`), prompt (`opens`) | prompt to session (`part of`) |
| `tool.ended` | `tool call ended` | `success`, `duration_ms` | session (`in session`), prompt (`during`), tool (`called`) | |
| `step.started`, `step.ended` | `step started`, `step ended` | | step (`started` / `ended`), session (`in session`), role (`actor`) | step to its task, PR, branch or node (`is`); step to session (`worked in`) |
| `session.opened` | `session opened` | | session (`opened`), role (`for role`) | session to role (`run by`) |

- **A person's step later** is the same `step` object, with the person's role (`kind=human`) as its `actor`.
- **A tracker's issue or a chat thread** becomes an object that a step links to (`is`). So nothing in schema 10
  changes shape.

### 6. Acceptance: a glance trial with three questions

A fresh evaluator, neither the builder nor the lead, answers three questions from a cold load. Each must come within
15 s, against a key registered before the run:
1. who did most of the work in the last day, and what it cost;
2. where the time went;
3. what a given step produced.

Also:
- the agreement test covers cost and time totals across the views;
- a property test over the dogfood ledger shows the replay at 60× has the same states as the ledger at each
  timestamp.

## Consequences

- R7a is released as 0.6.0, after the glance trial and the project lead's yes.
- Cost per step is exact where onloop dispatches, and labelled where it is attributed. That is a reason to build
  dispatch (R7b's b1) early.
- onloop never relays another team's telemetry. A customer's cost needs a later contract field, which the customer
  decides after its demo.
- Retiring Flow and Activity removes code, so it waits on the moves-and-goes list's confirmation.
