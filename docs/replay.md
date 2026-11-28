# Replay: who did what, how long it took, and what it cost

Replay shows the team's work over time, as one lane per role in proportion to time. A cursor gives every role's state,
its time and its tokens at that moment. It answers three questions at a glance: who did most of the work and what it
cost, where the time went, and what a step produced (decision 0019).

Open it at `#/details/replay`, or from **Details · Replay** in the sidebar, or from the Timeline's "Play it back" (an old
`#/replay` link that names a moment opens it; without one it opens the Timeline). With no moment in the link it opens
**live**: the window ends now,
is read again every 15 s, and the cursor stays at now.

## What you see

- **The window:** the last hour, 6 hours, day or week. Every figure is the window's, and says so ("In this 6 h window,
  up to 13:10"). "Show the ledger's totals" switches to everything since the ledger began.
- **The header:** who did most of the work in the window, with its working time and tokens, and the team's tokens;
  then each role's state at the cursor, and how many items wait on you now.
- **The lanes:** one per role, the roles that worked first. Each second of a lane is in one of four kinds of time:

  | Kind | Means | Drawn |
  |---|---|---|
  | Working | a turn running, or a model request | full colour |
  | Harness | onloop starting or finishing a carried turn, with no turn running yet | strong |
  | Waiting on you | a task the role asked you, still open | a light tint |
  | Queued | a message to the role, not yet delivered | light |

  When two overlap, the first in that order counts, so no second is counted twice. Ticks under a lane are model
  requests. A turn whose start is not known (an imported snapshot's turns, or a Stop with no SessionStart) is a short
  mark at its end. A role with nothing in the window is listed under the lanes.
- **Above the lanes:** turns per hour, and how many roles had a turn in each hour.
- **Where the data begins and ends:** a window that starts before the ledger's first event is shaded "No data before
  <time>" up to it, so a stretch with no data never reads as a quiet team. On a team's export the header says "Data
  as of <time>": where the export ends.
- **"Show as a table":** the lanes as a table, each role at the cursor: its state, "Quiet for" (since its last turn
  ended), its time in each kind, its tokens and cache reads, and its step.
- **Where the time went:** for the whole window, a bar per role of its four kinds of time, its tokens and cache reads,
  its longest quiet stretch, and its tokens by step. Above them, **You**: the share of the window in which anything
  waited on you, how many items wait now, and how long the oldest has.
- **Working now** (live only): each step onloop recorded that is open now, with its role, how long it has run and its
  tokens so far.

**Moving in time:** Play plays at 1x, 10x, 60x or 600x (Space); at the end it starts over. Previous and next change
(Shift+Left, Shift+Right) move between the lanes' state changes. Drag on the lanes or the slider to move the cursor.
The link keeps the window on screen (`to`, its end, epoch milliseconds), the cursor (`t`) and the speed, so a reload
or a shared link opens the same view, whenever it is opened, not live. A link with only `t` (made before `to`)
opens the window that ends at `t`. Live ends the window now again and drops both.

**Example** (the demo ledger, made up): in the 24 h window at now, the header reads "Most work (working time): builder,
28 min · cost 60k tokens" (when the next role worked within 10% of the first, both are named), and "Where the time went" shows the lead's tokens on `branch:feat/date-check` (recorded) and
`node:n-date` (attributed by rule), with 5% between steps.

## Cost: tokens, and how they reach a step

**Tokens are the measure.** Every token figure on the page is a sum, over model requests, of their input, output
and cache-creation tokens. Cache reads are shown apart, as "cache reads", and never added in, since they cost far
less. Claude Code's own dollar figure (`cost_usd`) is kept in the ledger and shown nowhere: it is an API-equivalent
price, not billed on a subscription, so on one it is never what the team spent.

A request belongs to the role of the session that made it (the carried role, or the role given to `onloop open`), and
to the moment onloopd received it.

Each model request is charged to:
1. **the step onloop recorded on its session** at that moment: the step a carried turn's message names
   (`onloop say ROLE TEXT --step node:a1`), else its item, or the step `onloop open` was given: **recorded**;
2. else, by **rule E**, the `Node:` trailer of the next commit on the branch its session works on: **attributed by
   rule**. onloop records the session's repository and branch when it starts the work. The rule reads the branch's own
   commits (`git log --first-parent`, oldest first), and of each commit its time and trailer only. It applies only
   when one role alone worked on that branch since the previous `Node:` commit: when several did, it cannot tell whose
   the commit is, and their requests stay between steps, marked "branch shared by N roles". The rule's other clause,
   the step named in the turn's opening message, is unavailable by design: prompts are never logged. `onloop say
   --step` is not that clause: it records the step as a field of the message, so its turn is charged under 1;
3. else **between steps**, shown with its share. A request is never charged to the nearest step.

**For rule-based attribution, give each role its own branch.** Better still, name the step: a message with `--step`
or `onloop open --step` records it, and needs no rule.

## Telemetry: where the figures come from

onloopd receives Claude Code's own OpenTelemetry export (OTLP/HTTP, JSON) on `POST /v1/logs`, with its token. It
drops the identity attributes Claude Code sends (`user.email`, `organization.id`, the account ids, `user.id`) before
anything reads them, and keeps three events, each with only its named fields:

| Claude Code's event | Ledger event | Kept |
|---|---|---|
| `api_request` | `cost.request` | model, tokens, cache tokens, duration, request id, the estimate |
| `user_prompt` | `turn.prompted` | the prompt's length, never its text |
| `tool_result` | `tool.ended` | the tool's name, success and duration, never its parameters |

Every session onloop starts sends here and nowhere else: its own OTel environment, with every inherited `OTEL_*`
variable removed, and no user settings file (whose `env` would win). A Claude Code session you start yourself, not
through onloop, sends onloopd nothing: its tokens are not in Replay. Metrics (`POST /v1/metrics`) are answered and
stored nowhere.

**Each record once.** A record that arrives twice (the exporter retrying a batch) is stored once: a model request
is keyed on its request id, any other record on its session, its prompt id (else the carried run's id) and its
sequence number. Claude Code numbers records per process, and a carried turn is a new process resuming the same
session, so the sequence starts again at each run; before 0.6.0 a resumed run's records were taken for the earlier
run's and dropped, and a ledger written then still lacks them.

**What came in.** `GET /health` counts, since onloopd started, every write by route and answer (200, and each refusal
by its status: 401, 403, 413, 415, 400), and every telemetry record:

| Count | Means |
|---|---|
| `received` | every record in the bodies that were taken |
| `not_kept_kind` | of a kind onloop does not keep, dropped on purpose: only model requests, prompts and tool results are kept |
| `unplaced` | of a kept kind, but with no session id to place it |
| `kept` | the rest: `received` = `not_kept_kind` + `unplaced` + `kept` |
| `stored` | of the kept, the ones logged |
| `duplicate` | of the kept, the ones already in the ledger (a retried batch): `kept` = `stored` + `duplicate` |

So "received 26, kept 9" is not a loss: the other 17 are kinds onloop never reads. Counts only, never a body: a
refused or dropped record is never invisible.

**The cross-check.** At the end of a carried run the carrier records the CLI's own count of the run's tokens (each
turn's usage, per model, summed over the run; never the session's running total, which on a resumed session includes
earlier runs) on `run.ended`. Every record a carried run sends is tagged with its run id (`onloop.run`, a resource
attribute onloop sets in the run's environment), so Replay compares the count with that run's own telemetry, wherever
it landed in time (a record without the tag, from an older build, counts by the run's time on its session). It says so
under the summary: "Telemetry brought 99% of the tokens the 6 carried runs in this window reported". A run below 95%
is named, in red, with its share: its tokens on the page are short. A run above 102%, or with telemetry against a
count of nothing, is named too: something was counted twice or taken from another run. A tagged record from another
session (a process the run started) is counted apart, as the run's `nested` cost. A session onloop did not start has
no such count, so it is not cross-checked.

## `onloop open <role>`: your own session, measured

```
onloop open builder --step node:a1
```

starts your interactive Claude Code session for a role, with `--setting-sources project,local` and onloop's OTel
environment, and tells onloopd the session and its step (`session.opened`, `step.started`), then the step's end when
you leave (`step.ended`). `--step` is `task:`, `pr:`, `branch:` or `node:`; `--dry-run` prints the command and the
environment (never the token). It needs onloopd running next to the ledger, and refuses a metered API key.

## Products: what a step produced

Click a step (in "Where the time went" or the table) to see what it produced **as of the cursor**: the files its
commits changed, each at its latest commit then.
- A **node** step is every commit with its `Node:` trailer on a branch onloop recorded; a **branch** step is the
  branch's commits while the step was open. A task or PR step has none here yet.
- Files are read from git in a repository onloop recorded, at their commit, up to 1 MiB; nothing is stored.
- Markdown is rendered from a parsed tree, so a document's HTML stays text and only http links are links. Images
  show. HTML shows in a sandboxed frame: onloopd serves it with `Content-Security-Policy: sandbox`, so it never has
  onloopd's origin and no script runs; "Open it on its own" keeps the sandbox.

## The API

| Route | Answers |
|---|---|
| `GET /api/replay?from=&to=&lanes=` | a window of at most 31 days, in columns: each lane at `from` and at `to` (its state, its four kinds of time so far, its tokens so far), the state changes and model requests inside (each request with its step), the steps with `step_by` (per step: recorded or rule), `between_shared` (per lane whose requests stayed between steps on a shared branch, how many roles shared it), `working_now`, `your_wait` and `coverage`: the runs, the tokens they reported and received, the share, and `low` and `high`, the runs below `low_below` (0.95) and the runs above `high_above` (1.02) or with telemetry against a count of nothing |
| `GET /api/products?step=&at=` | a node or branch step's commits and changed files as of `at` |
| `GET /api/products/file?repo=&sha=&path=` | one of those files, in its own sandbox |
| `POST /v1/logs`, `POST /v1/metrics` | Claude Code's telemetry, with onloopd's token |
| `POST /sessions/opened`, `/sessions/ended` | what `onloop open` reports |
| `GET /health` | onloopd's state, and `receipts`: every write by route and answer, the telemetry counts above, and `locks`: write attempts retried, writes failed, and the longest write transaction (`longest_tx_s`, where it was opened: `longest_tx_at`), with `journal_mode` |

## Limits

- A turn whose start was not recorded counts no working time on its lane; it shows as a mark at its end.
- Rule E reads the branch's own commits (`git log --first-parent`): a merged side branch's `Node:` commit is not
  the branch's next commit.
- Rule E needs the session's branch: a session onloop started outside a git repository leaves its requests between
  steps (a session onloop did not start sends nothing at all). On a branch several roles worked on since the last
  `Node:` commit, the rule places nothing: it reads only the branch and a commit's time, so it cannot tell whose the
  commit is.
- Products are read for node and branch steps only.
- In a browser that blocks sandboxed frames, an HTML product shows empty in the page; "Open it on its own" still
  works.
