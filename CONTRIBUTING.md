# Contributing

Thanks for helping. This file says how to set up, what to run before you open a pull request, and the few rules
that are not negotiable.

## Set up

You need Python 3.11 or later, git, and for the commands that talk to GitHub, the `gh` CLI. Tests never touch the
network.

```bash
git clone <this repository> onloop && cd onloop
uv venv
uv pip install -e '.[dev]'          # or: python -m venv .venv && pip install -e '.[dev]'
```

The `dev` extra installs pytest, ruff and cryptography.

## Before you open a pull request

```bash
pytest
ruff check .
onloop selftest
python tools/confidentiality_scan.py
```

All four must pass. CI runs lint and the tests on Linux and Windows and needs no secrets.

## The rules that are not negotiable

**Checkers need a self-test that proves they bite.** Anything in onloop that decides, validates or refuses (the
classifier, the gate, the audit, the identity guard, the CODEOWNERS check, the ledger check, the watchdog) carries a
`self_test()` that feeds it a case it must catch and fails if it lets that case through. A checker that quietly
narrows is worse than one that fails. When you add a rule, add the case that proves it. When you fix a bug in a
checker, add the case that would have caught it. Register a new checker's self-test in `onloop selftest`.

**Clean room and confidentiality.** onloop is a general-purpose toolkit. Nothing in it may come from, or refer to, a
private project: no names of people, organizations or hosts, no e-mail addresses, no machine-specific paths, no IP
addresses, no App IDs, no key material, no database or container names, no domain vocabulary from a particular
project. Examples use generic logins such as `owner-a`, `rev-bot` and `agent-bot`. Do not paste text from a private
repository, even reworded. `python tools/confidentiality_scan.py` checks the generic patterns, and
`--denylist PATH` adds a project-specific list that is kept outside the repository. A contribution that fails the scan
will not be merged.

**No network in tests.** GitHub calls go through one thin client (`onloop/github.py`) and git calls through another
(`onloop/gitutil.py`). Tests replace them with fakes from `onloop/testing.py`.

**No secrets, ever.** Not in code, tests, fixtures, examples or commit history. Keys used in tests are generated in
the test.

**Versions live in tags and the changelog, not in names.** No file, function or table is called `v2`, `new`, `final`
or the like.

## Style

- Python 3.11, type hints on public functions, `ruff` with the settings in `pyproject.toml`.
- Keep it small. onloop depends on PyYAML at run time; cryptography is optional and only for `as-app`. A
  new dependency needs a reason.
- English, plain prose, written the way an engineer would say it. Messages the tool prints are sentences a person can
  act on.
- Comments say why, not what.

## Commits and pull requests

- One logical change per commit. A short imperative subject (about 70 characters) with an optional prefix for the
  area, such as `gate: refuse a review of an older head`. Use the body to say why.
- Do not rewrite history that others have seen.
- A pull request describes what changed and why, and names the tests that cover it. Changes to `onloop.yaml` handling,
  the classifier or the gate say how they affect what needs the human.
- Update `CHANGELOG.md` under `[Unreleased]` for anything a user can notice, and the docs when behaviour or a field
  changes. `docs/configuration.md` is a reference for every field and command, so keep it exact.

## Documentation

Describe what the code does, and verify it by running it. Do not document a field or a flag that is not there. If you
change a message or a flag, search `README.md` and `docs/` for it.

## Conduct

Be direct and kind. Argue with the change, not with the person.
