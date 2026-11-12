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

The UI (`web/`) also needs Node 22 or later. It is built at release time and never committed:

```bash
cd web
npm ci
npm run build        # writes src/onloop/ui/, which onloopd serves and the wheel carries
npm run e2e          # the browser tests, on the installed Chrome; ONLOOP_PYTHON names the venv's python
```

A change to the UI also needs the vision loop's evidence (`npm run screens`, then a review by someone who did not build
it); see [docs/ui.md](docs/ui.md).

## Before you open a pull request

```bash
pytest
ruff check .
ruff format --check .
onloop selftest
python tools/confidentiality_scan.py
```

All five must pass. CI runs them on Linux and Windows and needs no secrets.

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

**No network in tests.** GitHub calls go through one thin client (`src/onloop/github.py`) and git calls through another
(`src/onloop/gitutil.py`). Tests replace them with fakes from `src/onloop/testing.py`.

**No secrets, ever.** Not in code, tests, fixtures, examples or commit history. Keys used in tests are generated in
the test.

**Versions live in one place.** See [Versions](#versions). No file, module, function, test, table or doc heading
carries a version, a milestone or a phase, and none is called `v2`, `new`, `final` or the like. Name a thing by what
it does.

## Style

- Python 3.11, type hints on public functions, `ruff` with the settings in `pyproject.toml`.
- Keep it small. onloop depends on PyYAML at run time; cryptography is optional and only for `as-app`. A
  new dependency needs a reason.
- English, plain prose, written the way an engineer would say it. Messages the tool prints are sentences a person can
  act on.
- Comments say why, not what.

## Architecture

[docs/architecture.md](docs/architecture.md) gives the package's layers and the direction they may depend on each
other, the supported surface against what is internal, the extension points and where state lives.
`tests/test_architecture.py` fails on an import that points from a lower layer to a higher one. A new top-level
module must be given a layer there.

## Versions

- Versions follow [PEP 440](https://peps.python.org/pep-0440/): `0.3.0`, `0.3.0a1`, `0.3.0rc1`. Release numbers follow
  [Semantic Versioning](https://semver.org/) in meaning: before 1.0, a minor release may change the supported surface,
  and says so in the changelog.
- The version is set in one place, `onloop.__version__` in `src/onloop/__init__.py`. `pyproject.toml` reads it, and the
  starter workflows derive their install spec from it.
- A release is an annotated tag `vX.Y.Z` (for example `v0.3.0`, `v0.3.0rc1`) on `main`, made after the changelog
  section for it is written. Tags are never moved.

## Branches

The repository is trunk-based. `main` is always releasable: CI passes on it and its changelog is current. Work happens
on short-lived branches cut from `main` and merged back within days:

| Prefix | For |
|---|---|
| `feat/` | a user-visible capability |
| `fix/` | a bug fix |
| `refactor/` | a change of structure with no change of behaviour |
| `docs/` | documentation only |
| `chore/` | tooling, formatting, dependencies, housekeeping |

A branch is named after what it changes, in kebab case: `fix/gate-older-head-review`. Delete it after the merge. Old
branches are kept, if at all, as annotated `archive/<old-name>` tags.

## Commits and pull requests

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):
  `type(scope): summary`, with the type one of `feat`, `fix`, `refactor`, `docs`, `chore`, `test`, `build`
  (packaging and dependencies) or `ci`. The scope is optional and names the area, such as `gate`, `web` or
  `supervisor`. The summary is imperative, lower case, without a full stop, and about 70 characters at most. A change
  that breaks the supported surface says so with `!` after the type and a `BREAKING CHANGE:` footer.
- One logical change per commit. Use the body to say why, and what was checked.
- Do not rewrite history that others have seen.
- A pull request describes what changed and why, and names the tests that cover it. Changes to `onloop.yaml` handling,
  the classifier or the gate say how they affect what needs the human.
- `docs/configuration.md` is a reference for every field and command, so keep it exact.

## Changelog

`CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Anything a user can notice goes under
`[Unreleased]`, in one of **Added**, **Changed**, **Deprecated**, **Removed**, **Fixed** and **Security**, written for
the user rather than as a list of commits. A thing added and removed again before a release is not listed as both. A
release renames `[Unreleased]` to `[X.Y.Z] - YYYY-MM-DD` in the same commit that sets `onloop.__version__`.

## Decision records

A decision that changes what onloop is, what it supports or how it is built gets an architecture decision record in
`docs/decisions/`:

- the file is `NNNN-what-it-decides.md`, numbered in order and never renumbered;
- it says its **Status** (`Proposed`, `Accepted`, `Superseded by NNNN`), date, who decided and its size, then
  **Context**, **Decision** and **Consequences**;
- a proposal is merged as `Proposed`, and only the project lead marks it `Accepted`;
- an accepted record is not rewritten. A later decision supersedes it, and the old one gains a line in its status
  saying so.

## Documentation

Describe what the code does, and verify it by running it. Do not document a field or a flag that is not there. If you
change a message or a flag, search `README.md` and `docs/` for it.

## Conduct

Be direct and kind. Argue with the change, not with the person.
