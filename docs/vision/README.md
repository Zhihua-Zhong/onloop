# Canvas — vision loop

Pictures taken by `web/e2e/screens.spec.ts` from the real panel serving a fixed, generic graph (`web/e2e/fixture_server.py`).
Regenerate with `cd web && npm run build && npx playwright test`.

| | light | dark |
|---|---|---|
| 1440 px | `canvas-light-1440.png`, `panel-light-1440.png` | `canvas-dark-1440.png` |
| 390 px | `canvas-light-390.png`, `panel-light-390.png` | `canvas-dark-390.png` |

## Rubric (answered from the 1440 px light picture, with the 390 px one beside it)

**Where does the eye land first?** On the two orange-outlined cards, "Choose the release date" and "Which stack for the front end?", and
on the orange "Waiting on you 2" pill at the top left that counts them. Orange is the only saturated outline on the page; the red
"Blocked" card and the red outlines of starved directions come second. That is the intended order: what is waiting on the person first.

**What can be decided in five seconds?** Whether anything needs the person (two items, in the header and on the canvas), which
directions are starved (red outline and badge on two zones), and which task is blocked and by what (the dashed edge into
"Tag and publish"). Clicking a waiting card gives the question, the default that applies if nobody answers, and the expiry in the
same panel, so the next step (answer, or let the default run) is clear without leaving the page.

**What is noise?**
- Finished cards are faded but still take the same room as live ones; "Hide finished" removes them, but the default shows them.
- Every card carries a left strip and a state label and a kind icon: the state is said three times. One of the three can go.
- The minimap is a pale box on a pale page; at this size it adds little. It earns its place only on a large graph.
- The connection handles show as small circles on card edges even when nothing is being dragged.

**Against Dataiku Flow.** Dataiku Flow draws one dataset-to-recipe pipeline and lets the eye follow it left to right; it has no
notion of "this is waiting on you". This canvas gives up the single left-to-right story, because directions are zones that
hold several small graphs, and in return puts the person's own queue first by colour and by count. Flow is better at showing how
one result is built from the previous ones across a long chain; this is better at showing where attention is needed across many
parallel directions. Zone-level edges between directions are not drawn, so a dependency that crosses directions is not visible.

## Evidence line for every round

Instrument: Playwright with the installed Chrome (headless), which is not subject to the desktop app's in-app-browser block. URL `http://localhost:18913/app/`
served by `web/e2e/fixture_server.py`. Language: English (the canvas has one language today). State: first open, nothing selected, unless the file name
says otherwise. **Data loaded: yes**, checked by the tests (any console error, a failed request or an off-origin request fails a test), not assumed.
The fixture is generic; the pictures contain no real work-graph content. The first instance keeps a note of what each instrument can see.

## Round log (reviewer: a separate vision subagent that did not write the change; pictures are the four `canvas-*` files, URL `/app/`, English, first-open, nothing selected)

**Round 1 → round 2 → round 3.** After round 1 the reviewer found: the minimap clipping a zone, red used for both "starved" and "blocked", the
chip row cut at 390 px, zoom buttons over cards, and an unreadable crop at 390 px. Done since: the phone opens on the first card waiting on the
person at a readable zoom with a "Fit all" button; state is said once per card; finished cards are one line; handles show on hover; a "Next for you"
stepper walks the waiting cards; "starved" is neutral; the minimap is small and translucent; zone titles and card text are larger; the phone header is
two short rows.

**Round 4** (this commit): a "Queue" lens (what waits on you, soonest expiry first, an item opens the inspector); the unfiled pile collapses to one summary lane
with "Show N"; scroll pans and Ctrl/Cmd-wheel zooms (pinch zooms); a blocked card is tinted and ringed so it is not skipped next to the amber ones.

**Still open (round 5 candidates):** the long cross-direction dependency is faint but still crosses other zones; the red blocked card is easy to miss
next to the amber ones; "Write-back" and "Starved" are unexplained jargon to a first-time viewer; at 390 px the zone title is cropped at the left edge;
1440 px leaves wide empty margins on a small graph; dark-theme zone outlines are low-contrast.

## Known weaknesses
- The side panel on a phone is full-width and hides the canvas; closing it works (Escape, the close button, a click on the page).
- Dark theme is checked for contrast by eye only.
- The rubric above was written for round 0 (before the changes listed in the round log) and is kept as the baseline.
