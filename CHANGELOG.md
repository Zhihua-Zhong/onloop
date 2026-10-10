# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-10

First release.

### Added

- `onloop.yaml`, the policy: three identities (`human`, `reviewer`, `agents`), roles, review tiers, a `paths` table
  mapping each path to an owner role, a tier and a reserved class, project `checks`, `required_checks`,
  `post_merge_checks`, `audit.since` and `apps`. `onloop policy check` validates it, including that the policy
  reserves itself.
- Six built-in reserved classes (`claims`, `safety_widening`, `authority_widening`, `destructive`,
  `money_accounts`, `leaves_project`), each with an approver of `human` or `reviewer`, and user-defined classes.
- `onloop codeowners`: CODEOWNERS generated from the policy, with `--check` and `--write`.
- `onloop classify`: computes the reserved classes touched, the approvers required, the tier and the owners from a
  change's own diff or from a list of paths. Compares the base policy with the head policy and classes any
  relaxation as authority widening. Output as text or `--json`.
- `onloop gate`: merges a pull request only when every condition holds on the exact head SHA (reviewer verdict,
  human approval when required, green required checks, ownership of the changed paths by the authoring role, the
  project's checks and `onloop.checks` plugins), merges with `--match-head-commit`, runs `post_merge_checks` and
  reverts the merge when one fails. `--dry-run` decides without merging.
- `onloop audit`: re-judges every merge on the main branch against the policy at its own first parent, and alarms on
  direct pushes, unexplained merges, merges of something other than the head, merges by the wrong identity, and
  merges whose evidence does not hold. Accepts verified gate reverts.
- `onloop as-app`: runs one `git` or `gh` command as a GitHub App installation with a short-lived token held only in
  that command's environment, with guards against printing or swapping the credential and a restricted command set
  for the reviewer identity.
- `onloop ledger`: `check` (including an append-only changelog check against a base ref), `render` as Markdown or
  HTML, and `next` to pull a role's next item.
- `onloop watchdog`: reports items stuck past their limit, roles with an empty queue, and pull requests waiting for a
  reviewer verdict.
- `onloop init`: writes a starter policy, four role charters, a decision-record template with "decisions by size",
  the ledger, CODEOWNERS and two workflows (gate checks and main audit).
- `onloop selftest`: runs a self-test for every checker proving that it still catches what it should.
- Documentation: README, concepts, configuration reference, security model and FAQ under `docs/`, and a validating
  `examples/minimal/` configuration.
- Apache-2.0 licence, `NOTICE`, `SECURITY.md` and `CONTRIBUTING.md`.

### Not included

- An event-driven dispatcher that launches headless agents from GitHub events.
- A team dashboard.
- A PyPI release.
