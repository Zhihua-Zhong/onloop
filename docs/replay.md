# Replay: who did what, how long it took, and what it cost

Replay shows the team's work over time, as one lane per role in proportion to time. A cursor gives every role's state,
its time and its tokens at that moment. It answers three questions at a glance: who did most of the work and what it
cost, where the time went, and what a step produced (decision 0019).

Open it at `#/replay`, or from the **Replay** tab. With no moment in the link it opens **live**: the window ends now,
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
  requests. A role with nothing in the window is listed under the lanes.
- **The table:** each role at the cursor: its state, its time in each kind, its tokens and cache reads, and its step.
- **Where the time went:** for the whole window, a bar per role of its four kinds of time, its tokens and cache reads,
  and its tokens by step. Above them, **You**: the share of the window in which anything waited on you, how many items
  wait now, and how long the oldest has.
- **Working now** (live only): each step onloop recorded that is open now, with its role, how long it has run and its
  tokens so far.

**Moving in time:** Play plays at 1x, 10x, 60x or 600x (Space); at the end it starts over. Previous and next change
(Shift+Left, Shift+Right) move between the lanes' state changes. Drag on the lanes or the slider to move the cursor.
The link keeps the moment and the speed, so a reload or a shared link opens the same view.

**Example** (the demo ledger, made up): in the 24 h window at now, the header reads "Most work: builder, 28 min
working, 60k tokens", and "Where the time went" shows the lead's tokens on `branch:feat/date-check` (recorded) and
`node:n-date` (attributed by rule), with 5% between steps.

## Cost: tokens, and how they reach a step

**Tokens are the measure.** Input, output and cache creation are counted; cache reads are shown apart, since they
cost far less. Claude Code's own dollar figure (`cost_usd`) is kept in the ledger and shown nowhere: it is an
API-equivalent price, not billed on a subscription, and never what the team spent.

Each model request is charged to:
1. **the step onloop recorded on its session** at that moment (a carried turn's item, or the step `onloop open` was
   given): **recorded**;
2. else, by **rule E**, the `Node:` trailer of the next commit on the branch its session works on: **attributed by
   rule**. onloop records the session's repository and branch when it starts the work, and reads a commit's time and
   trailer only. The rule's other clause, the step named in the turn's opening message, is unavailable by design:
   prompts are never logged;
3. else **between steps**, shown with its share. A request is never charged to the nearest step.

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
variable removed, and no user settings file (whose `env` would win). A session onloop did not start exports nothing
from onloop's own folders. Metrics (`POST /v1/metrics`) are answered and stored nowhere.

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
| `GET /api/replay?from=&to=&lanes=` | a window of at most 31 days, in columns: each lane at `from` and at `to`, the state changes and model requests inside, the steps, `working_now` and `your_wait` |
| `GET /api/products?step=&at=` | a node or branch step's commits and changed files as of `at` |
| `GET /api/products/file?repo=&sha=&path=` | one of those files, in its own sandbox |
| `POST /v1/logs`, `POST /v1/metrics` | Claude Code's telemetry, with onloopd's token |
| `POST /sessions/opened`, `/sessions/ended` | what `onloop open` reports |

## Limits

- A turn whose start was not recorded counts no working time on its lane.
- Rule E needs the session's branch: sessions onloop did not start, or that ran outside a git repository, leave
  their requests between steps.
- Products are read for node and branch steps only.
- In a browser that blocks sandboxed frames, an HTML product shows empty in the page; "Open it on its own" still
  works.
