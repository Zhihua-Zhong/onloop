# Decision 0014: goals, links and outcome KPIs

- **Status:** Accepted on 2026-11-16
- **Date:** 2026-11-16
- **Decided by:** the onloop lead, on the R5 plan and the first customer's answers of 2026-11-10
- **Size:** medium (read-only ingest and new KPIs; nothing acts)

## Context

A team of agents can merge more changes every week while moving more slowly toward its goal. Counting merged
changes rewards splitting work, and each split pays the fixed cost of review and the serial gate again. The measure
that cannot be gamed this way relates work to progress: goal items ticked per merged change, and the fixed cost each
change paid.

The goal is a checklist: numbered items in one tracker issue. Each number is an id that stays the same when the
wording changes. Items are appended, so the denominator moves. An item is ticked by checking its box, and the tick's
time is in the issue's edit history.

## Decision

1. **A goal is declared in onloopd.yaml** (`goals:`): an id, a read adapter, and a locator.
2. **The ingest is read-only and comes from the edit history.**
   - Each edit is diffed by item number. A box that becomes checked is a tick at the edit's own time, and one that
     becomes unchecked is an untick.
   - The item count at each edit is a denominator, with its date.
   - A reworded item keeps its id. A removed item keeps its row, marked removed.
   - A tick links the first comment that names the item after it, when there is one. The KPI counts ticks without
     such a comment apart.
3. **Every KPI carries its denominator and its date.** A goal KPI uses the denominator in force at the end of its
   window, and says when that denominator was set.
4. **Work links to a goal by a line in the PR body:** `Goal: <goal id>/<item>`, several comma-separated, or
   `Goal: none` for process work on purpose.
   - The ingest reads the line when a PR opens, becomes ready, is edited and merges.
   - It stores the link, never the body.
   - Unlinked work is counted as unlinked.
5. **The KPIs** (`onloop kpi`, `GET /api/kpi`, the Outcome view):

   | KPI | Shown as |
   |---|---|
   | goal ticks per merged PR | ticks ÷ merged PRs in the window, with the checklist's size and its date |
   | goal-linked share (flow distribution) | linked, `none` and unlinked, out of the merged PRs |
   | fixed cost per PR | the median and p90 of queue wait (ready to merged), review rounds, and human touches (human tasks naming the PR), each with its n |
   | flow load | open PRs, and how many have had no push for 7 days |
   | change lead time | as before, with its n |

   An empty window says "no merged PR in the window", never a number or `None`.
6. **Days are calendar days in `kpi.timezone`** (an IANA zone in onloopd.yaml; default the machine's own). It is the
   same zone that `days=` is converted in ([decision 0012](0012-outcome-line-version-2.md)).
7. **The baseline file becomes `onloop.kpi.baseline/2`.** Each week gains `merged_prs` and `goal_ticks{count,
   denominator, denominator_at}`. Version 1 files still validate.
8. **Combining before the gate, as advice.** Two or more open PRs from one author role that link the same goal item,
   and together stay under `batching.combine_max_lines` (default 400), open one alert to that role. onloop never
   combines branches. The batch inside onloop's own merge queue is R6 (decision 0015, to come).

## Alternatives considered

| Option | Verdict | Why |
|---|---|---|
| DORA and Flow Framework metrics alone | Kept where they match (lead time, flow load, flow distribution) | None relates work to goal progress; flow velocity counts items, the measure that is gamed |
| Linear initiatives, Atlassian goals | Not the goal store here | The goal is a checklist in an issue; other hosts can be goal adapters later |
| Polling the issue body and diffing snapshots | Fallback only | It misses edits made between polls; the edit history does not |

## Consequences

- Ledger schema 7: `goals`, `goal_items`, `goal_denominators`, `goal_ticks`, `links`.
- The GitHub goal source reads `userContentEdits` once spike R5-S1 has run on a test repository the project lead
  approves. Until then, goals are ingested from fakes in the tests.
