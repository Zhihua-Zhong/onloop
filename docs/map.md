# The map

The map answers one question at a glance: **where is the work stuck, and is it me?** It is one picture with levels:
you zoom in on purpose, and every level counts the same things as Now and the task list (decision 0018).

Open it at `#/map`, or from the **Map** tab.

## The levels

| Zoom | URL | A block is | It shows |
|---|---|---|---|
| 0 | `#/map` | a goal | each checklist, each feedback batch, and "Changes in flight", grouped by where they came from, never by role |
| 1 | `#/map/<goal>` | a stage of that goal | the stages in order, each with its count, colour, oldest item and, when it is red or amber, which item and why |
| 2 | `#/map/<goal>/<stage>` | a cell: who holds what in a stage | the goal's grid, a column per stage and a row per holder, with the stage you opened outlined; below it, the items one holder has in that stage |
| 3, 4 | reserved | a role's turns, a turn's tool calls | these come with the tool events of a later contract |

**The stages at zoom 1:**
- a checklist: to do → in progress → done;
- a feedback batch: the team's own stages, as its export declares them, then accepted;
- changes: opened → checks → review → merged (in the data's last week).

**The grid at zoom 2** (mock-up v2) shows all of a goal's open work in one table:
- **The columns** are the goal's stages. Each header says how many items are open in it now and, for changes, how
  many reached it in the data's last week.
- **The rows** are who holds the work, worst first: the most live jammed work, then the most idle, then the most open.
- **A cell** is a bar as long as its count, coloured by its worst item. Merged has its own scale, so a busy week of
  merges never shrinks the open cells. A click, or Enter on a focused cell, opens that stage at that holder.
- **For changes**, Opened is split. A change with no move for 7 days or more is **idle**: parked or abandoned, in its
  own column first, apart from the live queue. Your own tasks are never idle, since they wait on you.
- **Idle is set apart at every level**, not only in the grid. The header's "N jammed" and a block's tally count live
  work only, and the tally adds "N idle" ("3 jammed · 2 idle · 1 moving"). The reason line names the worst live
  change, never an idle one. A stage, holder or block holding only idle work is grey and reads "idle". On zoom 0 an
  idle change is a muted square.
- **The bottleneck** is the live stage with the most jammed changes; its header is outlined in red.
- **Roles with nothing open** are named in one line under the table.
- **On a phone,** only the columns with something in them, and the stage you opened, are shown; a line names the
  rest.
- **The counts agree:** a stage's cells (with Idle, for Opened) add up to its zoom 1 count, and each holder's cell to
  the items listed below the table (`tests/test_agreement.py`).

## Colour and shape

Every block takes the **worst colour of its children**, and every colour has a shape, so nothing depends on colour
alone.

| Colour | Shape | Means |
|---|---|---|
| Red, "jammed" | ! | an agents' step past its threshold with no move, or a wait on a role or resource past the wait threshold |
| Amber, "waits on you" | ◆ | the person's step: one of your tasks, or a wait on you |
| Blue, "moving" | ▶ | moving within its threshold |
| Grey, "not started" | □ | no evidence yet |
| Green, "done" | ■ | every child is done |

- **Every red or amber block** carries one line naming the child and why, so a colour never needs a guess.
- **A stage with no evidence source** is hatched and says "not reported", never 0. An export without check runs, for
  example, reports no checks stage.

## The words

- **"N wait on you"** counts only your own tasks and waits. That is the same number as the header, as Now and as the
  task list.
- When a team's export marks an item as your step, the map says **"marked yours by the team"**. It is never added to
  your count.
- **"Team quiet at most"** is the longest stretch in the data's last day when no role had a turn running or ending.
  An item's own "no move for 3 days" is its reason line.
- **"≥ 9 days"** means the start is older than anything onloop has seen; its waits row says "began before the first
  export".
- **A change's checks** read "checks: 1 failed (e2e) · 4 passed · 1 skipped". Skipped runs are counted and never
  named; a failed or running check is named exactly as it was recorded.
- **A goal with no title** shows its id in plain words: "release-plan" reads "Release plan".

## Moving around

- **Zoom and pan:** the wheel or a pinch zooms the canvas, and a drag pans it. Zooming never changes the level; from
  1.6× each block shows an **Open** button.
- **Going in:** a double-click, Enter on a focused block, a click on its title or **Open** goes in. The camera moves
  into the block for about a quarter of a second, or not at all with reduced motion. A second click during the move
  is ignored.
- **Going back:** Escape goes up a level (at zoom 0 it does nothing), and the breadcrumb links every level above.
  Back and Forward walk the levels, and a link lands on the same level.
- **The keyboard:** Tab moves across the blocks in reading order, one stop each.
- **"Show as a list"** gives every level as tables, at any width.
- **Under 768 px**, each level is one column. At zoom 0 it is: the signals, "Most waiting work", the batches, the
  checklists, the changes, then the waits.

## When onloopd is down

The page keeps the map as last read, with a line saying onloopd cannot be reached, and tries again every few seconds.
When onloopd answers, the map comes back without a reload.

## Thresholds

How long an agents' step may go without a move before it turns red is set in onloopd.yaml (see
[configuration.md](configuration.md#overview)):

```yaml
overview:
  stuck:
    declared: 2h   # a stage a team's export declares
    wait: 2h       # a wait on a role or a resource
```

The defaults are given 2 days, declared 3 days, checklist 5 days, change 3 days and wait 1 day. The legend always
shows the thresholds in use.

## History of colours

On every tick, onloopd compares each block's and stage's colour with the last one recorded. When it differs, onloopd
logs one `block.turned` event: `{block, from, to, reason, at}`. A later "since you looked" sentence and alerts read
these events.

## The API

`GET /api/map?path=` gives one level per call:
- empty: zoom 0;
- `<goal>`: zoom 1;
- `<goal>/<stage>`: zoom 2.

A path that names nothing is a 404 with a plain sentence.

## Known limits in 0.5.0

- **Who holds a change that waits on nobody** depends on the export. When it sends `by` (contract 1.2, see
  [data-contract.md](data-contract.md)), the change is held by its role, such as "builder". Only without
  `by`, from a 1.0 or 1.1 export, or with `by: team`, is it held by "the team".
- **Zoom 3 and 4** wait for the tool events.
