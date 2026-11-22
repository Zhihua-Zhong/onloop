# R7a: what moves and what goes when Flow and Activity are retired

Decision 0019, §4: **Now** stays as the person's inbox, **Replay** becomes the main view, **Map zoom 0** stays as the
overview, and **Flow** (Graph, Waiting on you, Roadmap, Calendar) and the separate **Activity** page are retired.
Nothing is removed before the project lead confirms this list. Commit 12 does the moves and the removals as
confirmed; until then both views work as they do in 0.5.0.

Each row says where a thing a person can see today will be, or why it goes. "Moves" means it must exist in its new
home before the old one is removed; the commit that builds it is named.

## Activity (`#/activity`, `GET /api/activity`)

| What a person sees today | Verdict | Where, and what it needs |
|---|---|---|
| One lane per role, turns as marks from start to end | **Moves** to Replay's lanes | Replay draws a turn as *working* only when its start is known. A turn with no recorded start (an imported team snapshot's, or a Stop with no SessionStart) shows today on Activity and not on Replay. Commit 12 adds those turns to Replay as marks at their end, as Activity does. |
| Turns per hour, and roles working at once (two density rows) | **Moves** to Replay | A density row above the lanes, per pixel column, from the same index. |
| Each role's longest quiet stretch (hatched), and "quiet for" now | **Moves** to Replay | "Quiet for" in the at-cursor table; the longest quiet in the window in "Where the time went". |
| A role's side sessions (a lead's forks) as sub-lanes | **Goes**, for now | Replay's lanes are roles; sessions show through their steps. If the person needs forks apart, a later sub-lane per session. *Question 2.* |
| "Data as of", when the ledger replays a team's daily export | **Moves** to Replay's header | The same `data_as_of` the map shows. |
| "No data before …", when the window starts before the first turn | **Moves** to Replay | Shade the lanes before the first event, as Activity does. |
| 24 h, 48 h, 7 days | **Moved** | Replay has 1 h, 6 h, 24 h, 7 days. |
| "Show as a table" | **Moved** | Replay's table: the same numbers at the cursor. |
| Now's "see activity" link (under a quiet team) | **Moves** | Points to Replay. |
| `GET /api/activity` | **Goes** after the moves | `derive.turns_per_role` stays: the map and the timeline use it. |

## Flow (`#/flow`, `GET /api/flow`)

| What a person sees today | Verdict | Where, and what it needs |
|---|---|---|
| **Graph**: cards in zones, dependency edges, resources, the constraint's trace downstream | **Goes**, the trace **moves** | The constraint and what queues behind it are already Now's first line ("merge-check holds 5 changes in its queue … next: …"). The graph itself has no home: the map shows goals and stages, Replay shows time. *Question 1.* |
| A card's side panel: its waits, its links, the command to copy | **Moves** to the map, zoom 2 | The map's item already links its PR or task; its waits and command join it. |
| **Waiting on you** mode | **Goes** | Now's task list is the one list of what waits on you (finding #20); every view counts it the same way (the agreement test). |
| **Roadmap**: each item as a bar from opened to now, and its expiry | **Goes**, expiries **move** | Replay shows time per role and per step; an item's age is on the map (zoom 2) and Now. An expiry (a lease, a slot) moves to Now's Stalled column when it is within a day. |
| **Calendar**: done in the last day, now, the next day (with jobs due) | **Goes** | Done: Replay's window and the digest on Now. Now: Now. Jobs due: the Schedules view. |
| The toolbar's "waiting on you" count and constraint chip | **Goes** | Now shows both, with the same count. |
| Now's "Flow →" and "open" links on the constraint | **Move** | To the map's stage that holds the constraint, or Replay at now. |
| `GET /api/flow`, `views.flow`, the layout engine (elkjs, about 1.4 MB) | **Go** after the moves | The agreement test stops reading Flow; the page loses its largest dependency. |

## What does not change

Now, the map, Outcome, Team, Conversation, Schedules and Audit stay. The navigation becomes Now, Replay, Map, then the
rest; Map zoom 0 opens into Replay (commit 12).

## The project lead's answers (2026-11-22, confirmed through the onloop lead)

The list is confirmed, with these answers:

1. **Flow's dependency graph goes now.** "What waits on what" returns in R7b, from the plan (b2 and b3), not as a view.
2. **Side sessions: steps are enough.** No sub-lanes for now.
3. **Order: every move first,** each checked by hand (the onloop lead checks each in Chrome); **then the removals in
   one commit,** so that one revert restores both views.

## As built (commits 12 and 13), where it differs from the list

Recorded after the removal (211e547), from a check of docs/ui.md against this list:

- **Expiries** sit beside Stalled, under their own heading "Expires within a day", not in the Stalled column, and
  include items whose expiry is already past.
- **Now's constraint links** go to the map's stage that holds the constraint, with the holder's row selected. There is
  no "Replay at now" link from the constraint.
- **Map zoom 0 does not open into Replay**: the map links nowhere into Replay. Replay is in the bar, second.
- **Now's link to Replay** is on the stall card shown when the team's turns carry no outcome lines (the card that
  linked to Activity), and opens the last 7 days, a window Replay has (Activity's 48 h is gone).
- **The calendar's done work** is in the digest on Now and on the map, and in Replay's window.

The links to Replay from map zoom 0 and from the constraint were not built; whether they are wanted is open for the
onloop lead.
