# Dashboard and demo site

`onloop dashboard --out DIR` writes `DIR/index.html`: one self-contained page with its own CSS and JS. It has no CDN,
no font download, no server and no external request, so it opens from disk, from a static host or from an email
attachment. It follows the system's light or dark theme (a button switches it) and fits a phone.

```bash
onloop dashboard --out site                       # the policy and the ledger only
onloop dashboard --out site --github --repo OWNER/NAME     # plus pull requests and the audit history, through `gh`
```

Every number on the page is computed from the inputs. Nothing is typed. When a figure cannot be computed because
GitHub was not read, the page shows `n/a`, not zero.

## The panels

| Panel | Reads | Shows |
|---|---|---|
| The loop | policy, pull requests, audit | the six stages (author, classify, reviewer, human only if reserved, gate, audit) with a count at each |
| Policy map | `onloop.yaml` | each path with its owner, tier, reserved class and approver; rows that need the human are shaded |
| Queues | `ledger.yaml`, roles | one column per role: what it does now and what it does next; an empty queue shows as idle |
| Waiting on the human | `ledger.yaml`, pull requests | pending decisions with their recommendations, and open pull requests only the human can approve |
| Watchdog | the watchdog's own checks | items stuck past their limit, idle roles, pull requests waiting too long for a verdict |
| Recent merges | the audit's own judgement | each merge or commit with its audit verdict and, for an alarm, the reason |

With `--github` the page reads open pull requests, and the last `--limit` merges on the base branch, re-judged by the
audit against the policy as it read at each merge. The files of a pull request come from the host's list, for display
only; the gate itself reads `git diff`.

## The demo site

`onloop demo --out site` builds two pages from `examples/minimal` (or `--example DIR`):

- `index.html`: the dashboard over a made-up repository history: six merges, a gate revert and a direct push (so an
  alarm shows), and four open pull requests in different states;
- `how-it-works.html`: six scripted pull requests. For each, the page shows the exact output of `onloop classify` and
  `onloop gate --dry-run`: a routine change, a claims file, the same claim with the human's approval, a change that
  relaxes the rules, an approval of an older commit, and a red check hiding behind a green one.

The pull requests are inputs the demo builds in memory, but the classifier, the gate and the audit that judge them
are the real ones, and the page is what they returned. Change an input in `onloop/demo.py` and the verdict changes. The
project checks in `checks:` are not run, because the demo has no repository to run them in.

## Publishing the demo

`.github/workflows/pages.yml` builds the site from `examples/minimal` and deploys it with `actions/deploy-pages`. The
repository owner enables it once: Settings, Pages, Source "GitHub Actions", then sets the repository variable
`PAGES_ENABLED` to `true`. Until then the build runs on every push to `main` and the deploy is skipped. The site then
follows `main`.
