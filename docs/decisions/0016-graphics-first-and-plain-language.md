# Decision 0016: graphics before text, plain language first, one palette

- **Status:** Accepted on 2026-11-18
- **Date:** 2026-11-18
- **Decided by:** the project lead's two principles (2026-11-17), the onloop lead's rulings on the overview plan
- **Size:** small (a rule, a check, design tokens); it binds every view and report from R6 on

## Context

The dogfood showed people misreading pages that lead with codes ("outcome: blocked owner=human ref=…", "waker",
"t-3f2a superseded"). Agent-built pages tend to be code-shaped: tables of ids where a picture would answer the
question.

## Decision

1. **A picture before a sentence** wherever a form exists, chosen by the question (the `dataviz` procedure): bars for
   progress, cells for stages, lanes for activity, ranked bars for "on whom".
2. **Plain language first.** A line leads with what the thing is, in plain words. A code (`pr#204`, `t-3f2a`,
   `demo/7`) appears only after it, in parentheses or after " · ". Jargon is replaced, or explained on first use.
3. **The check is enforced.** `onloop.plaintext.problems(text)` fails a line that starts with a code, shows a code
   outside parentheses, or uses a listed jargon word. Its first version covers task titles (finding #26). It extends
   to every server-written line and to the page's label functions.
4. **One palette for state, with a shape for each colour**, validated in both themes:
   - to do: an outline;
   - moving (in progress): blue, hatched;
   - done: solid blue;
   - waits on you: amber with a ring;
   - stuck: red with "!".

   The map (decision 0018) uses the same four colours for its blocks. "Moving" stays this validated blue (ruling Q27).
5. **Hands-on testing, like a person, in a real browser,** for every UI change, with the evidence in the report.

## Consequences

- Every view's text goes through the check. A new jargon word is added to the list when a reader flags it.
- A colour never appears without its shape, or without its label in the legend.
