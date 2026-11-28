# The person's task list, goals and outcome KPIs

onloop's ledger is the system of record for everything that waits on the person ([decision 0013](decisions/0013-human-task-list.md)).
An ask states what it is worth ([decision 0012](decisions/0012-outcome-line-version-2.md)), and the work is measured by
the progress it makes toward a goal, not by how many pieces it is cut into ([decision 0014](decisions/0014-goals-and-outcome-kpis.md)).
Everything here observes and records. Nothing is closed, merged or written outside onloop.

## Asking the person

A role asks with `onloop ask`, then ends its turn blocked on the task it was given:

```
onloop ask --from builder --ref pr#45 --effect "approve the schema change, so the import can run on real data" \
  --goal demo/14 --weight critical --days 2 --holds "the import demo waits" --default "nothing merges" \
  --options "Approve|Approve, import paused|Wait for the review"
outcome: blocked owner=human ref=task:<the id onloop ask printed>
```

Since 0.7.0 `onloop ask` needs `--effect`, and refuses a subject that is already an open decision: it prints that
decision's id and exits 3, and nothing is asked again.

The same keys may be written on the outcome line itself (version 2 of the line):

```
outcome: blocked owner=human ref=pr#45 goal=demo/14 weight=critical days=2 effect="approve the schema change, so the import can run on real data" holds="the import demo waits" default="nothing merges"
```

| Key | What it says |
|---|---|
| `effect` | "do X, so Y": what the act changes for the whole |
| `goal` | the goal item it moves (`demo/14`), or `none` for process work on purpose |
| `weight` | `critical`, `supporting` or `skippable` |
| `cost_from` / `days` | when the delay starts to cost: a date, or a number of days from today (0: now), or `days=?` when it costs but nobody knows from when. `days` is turned into a date once, when onloop receives it, in `kpi.timezone`; no count is ever stored |
| `holds` | what waits on it; needed whenever a cost of delay is given |
| `default` | what happens if nobody answers |
| `options` | 0.7.0: the answers the person can give, 1 to 4 separated by `|`, each at most 40 characters (`options="Merge both|Wait"`). The person answers with one of them, or Done or Decline when there are none |

An ask without an effect, or with a cost of delay and no `holds`, is still taken. It is counted under Details · Rule
alerts, not listed with the decisions, and returned to its author once. Since 0.7.0 the Stop hook also sends the turn
back once, saying what is missing. The person can set such asks aside in one click ("format rule, no effect stated",
with an undo); onloop never writes an effect for them.

### One decision, one ask (0.7.0)

Every ask carries a subject key: its `ref`, normalised (`pr:12`, `#12` and `PR#12` are `pr#12`; a branch's `feat/`
prefix is dropped). An ask about a subject that is already open joins that decision, whoever asks: the decision keeps
its first author, counts every ask ("asked 3×"), and takes what the new ask adds, never losing what it had.
- **Before an ask**, a role's SessionStart and UserPromptSubmit hooks add its open decisions to its context, with their
  age, their ask count and their default, so it does not ask again.
- **At an ask**, a Stop whose outcome line asks about an open decision and brings nothing new is sent back once:
  "already open as t-…, waiting 4 h, asked 2×: do not ask again".
- **The answer reaches the asker** at its next prompt or session start, as context from its hook ("t-… answered by the
  person: Merge both"). Until then the person can withdraw it in Decisions · Done; after that, a change of mind is a
  new answer. `onloop decisions --mine --role <role>` lists a role's open decisions and the answers to it.

Carried roles get these hooks from the carrier, and `onloop open` gives the person's session the same three for that
run only (`--settings`); a session started any other way sees nothing. Decision 0020 records the rules.

An outside wait (a CI run, checks on a commit, a review) is named as `waker=event:gh-run:<id>`,
`waker=event:gh-checks:<sha>` or `waker=event:gh-review:pr<n>`, never as a placeholder pid. The GitHub checks behind
these wait for a test repository the project lead approves; until then onloop reports such a waker as `unknown`.

## The list

`onloop tasks` (or Now, or `GET /api/tasks`) shows every open task in one order:

1. bands: `critical`, `supporting`, no weight, `skippable`, then incomplete;
2. within a band, the days until the delay costs, computed now from the stored date (a date passed is "costing
   now"), then a cost whose date is not known, then no cost;
3. then the oldest, then the id.

What puts a task on the list: an ask (`onloop ask` or an outcome line naming `owner=human`), a tracker's labelled issue
(read adapter), a pull request whose body has an `Effect:` line (an approval at the merge gate), a prompt a session
waits on, a work-graph item waiting on the person, any other wait on the person, and a role the person paused.

**A task closes only with a resolution and its evidence.** The ledger refuses anything else.

| Resolution | When |
|---|---|
| `done` | the person marked it (`onloop task resolve <id> done`, or Done on Now: "local user, not verified"); the code host shows the approval; the tracker closed it |
| `declined` | the person said no |
| `superseded` | the author moved on without the person (`done ref=X`), a newer ask replaced it, or its wait ended: shown as "resolved without you" |
| `expired` | the author or the coordinator closed it after it was returned (`outcome: done resolves=<id>`, or `onloop task resolve <id> expired --by lead`) |

A wait clearing never closes a task. An approval at the merge gate is never done by a click: approve on the code host.

**Returns.** After 7 days unresolved, a task goes back to its author; after 14, to the coordinator (the carrier's
`coordinator`, or "coordinator"). The task stays on the person's list throughout. `onloop task keep <id> --reason …`
starts both clocks again. A carried role that opted in to `event` deliveries is told; for any other role onloop logs
what it would say. Open pull requests with no push for 7 and 14 days raise the same two alerts.

**Since you were away.** `onloop tasks --digest` (and the band at the top of Now) lists what was added, resolved,
resolved without you, and returned since the last visit, the goal ticks and onloopd's own gaps, then the first tasks
of the list. Opening the page or running `--digest` records a visit (`--peek` does not).

**Reconciliation.** On every tick, onloop compares the asks it can see (outcome lines, pull requests, the tracker,
prompts) with the list, and raises an alert for each one missing. It reads nothing of a turn but its outcome line: an
ask written only in prose is not seen, so the shared role paragraph says never to ask that way.

## Read adapters

A tracker, a goal store or an outside event is read through an adapter (`adapters:` in onloopd.yaml). An adapter has
no write method. It reaches its host with a REST `GET`, or a GraphQL document whose operations are all queries; the
client refuses anything else. GitHub issues with a label are built in; others are `onloop.adapters` entry points.

## Goals and the outcome KPIs

A goal is a checklist of numbered items (`- [ ] 14. text`) in one tracker issue (`goals:` in onloopd.yaml). onloop
reads it from the issue's edit history:
- an item keeps its number when reworded;
- a tick is a box checked at an edit's own time;
- the checklist's size at each edit is the denominator, kept with its date.

A pull request links to an item with a line in its body: `Goal: demo/14`, several comma-separated, or `Goal: none`.

`onloop kpi` and the Outcome view (`#/details/outcome`, `GET /api/kpi?window=7d`) show:

- **goal ticks per merged PR**, with the checklist's size and the date it was set (and its size before, when it
  changed in the window);
- **the goal-linked share** of merged PRs: linked, `Goal: none`, and said nothing;
- **the fixed cost per PR**: hours from ready to merged, review rounds, human touches, each with its median, p90 and n;
- **flow load**: open PRs, and how many have had no push for 7 days.

Two or more open PRs from one role that link the same item, and stay within 400 changed lines together, raise one
alert: combine them before the gate.

## onloopd's own jobs and gaps

`jobs:` declares work onloopd does itself, watched like any schedule. `report-daily` writes `<out>/<day>.md` for the
day before, at its time in `kpi.timezone`, and once, marked late, after onloopd was down. At start, onloopd records how
long it was down (`daemon.gap`), with the cause from Windows' System event log when it can read one. A ledger quiet for
a day says on Now whether the team is idle or waiting, and on whom.
