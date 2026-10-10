# onloop 0.1.0

onloop is a toolkit for running a team of AI coding agents on a GitHub repository with a human **on** the loop rather
than **in** it. The agents open, review and merge pull requests themselves; the human approves only a short list of
reserved classes (claims, safety widening, authority widening, destructive operations, money and accounts, anything
that leaves the project) and can overturn the rest.

It builds on GitHub's own mechanisms (rulesets, CODEOWNERS, required checks, GitHub Apps, Actions) and adds the parts
GitHub does not have.

## Install

```bash
pip install "onloop @ git+https://github.com/Zhihua-Zhong/onloop@v0.1.0"   # scan:allow
onloop init --human your-login --reviewer your-reviewer-app --agents your-agents-app
onloop selftest
```

Python 3.11 or later, git, and the GitHub CLI for the commands that talk to GitHub. `as-app` needs
`pip install "onloop[app] @ git+https://github.com/Zhihua-Zhong/onloop@v0.1.0"`. <!-- scan:allow -->

## What is in 0.1.0

- **Policy as code.** One `onloop.yaml` maps paths to an owner role, a review tier and a reserved class; each class
  names its approver (`human` or `reviewer`). CODEOWNERS is generated from it and checked against it.
- **Approval classification.** From a pull request's own `git diff base...head`, it computes who must approve. It
  also compares the base policy with the head policy, so a change that loosens the rules is classed as authority
  widening automatically.
- **A merge gate bound to the head SHA.** Reviewer approval, the human's approval when required, green required
  checks, path ownership by the authoring role, and the project's own checks, all on the exact head. It merges with
  `--match-head-commit` and reverts when a post-merge check fails.
- **A main-branch audit** that re-judges every merge against the policy of its own time.
- **Agent identity.** `onloop as-app` runs one `git`, `gh` or `onloop gate` command as a GitHub App installation, so
  agents never act under the human's account.
- **A ledger with pull queues** and a **watchdog** that reports stuck items, idle roles and pull requests waiting for
  a verdict.
- **`onloop init`** writes a starter policy, role charters, a decision-record template, the ledger, CODEOWNERS and two
  workflows.
- Every checker has a self-test that proves it still bites (`onloop selftest`). The tests need no network.

## Status

Alpha. The interfaces may change before 1.0.

## Known limits

- The review tier is reported but does not add gate conditions; reserved classes decide who must approve.
- Use merge commits only: the gate merges with `--merge` and the audit expects a two-parent merge.
- `as-app` guards stop accidents, not malice: a command that has been handed a token can use it. Limit what the App
  may do with its permissions and the repository ruleset.
- The audit detects what gets onto `main` some other way; it does not prevent it. Configure a ruleset as well.
- Not in 0.1.0: an event-driven dispatcher, a team dashboard, a PyPI release.

Documentation is in the repository: `README.md`, `docs/` and `examples/minimal/`. Licence: Apache-2.0.
