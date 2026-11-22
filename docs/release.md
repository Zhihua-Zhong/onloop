# Releasing onloop

A release is built from its tag by the `release` workflow and published by a person. Nothing is uploaded from a
developer's machine. Two workflows take part: `ci` (`.github/workflows/ci.yml`) checks a commit, and `release`
(`.github/workflows/release.yml`) builds, attests and drafts the release from a tag.

## Before your first release

- The repository grants the `release` workflow `contents: write`, `id-token: write` and `attestations: write` (the
  workflow asks for them; an organisation policy can still refuse them).
- Artifact attestations are free for public repositories; a private one needs a plan that has them, or the attest
  step fails.
- `src/onloop/__init__.py` keeps the version as `__version__ = "X.Y.Z"`, in double quotes: the version check reads
  that line.
- `RELEASE_NOTES.md` is read from the tagged commit, so the commit carries the release's notes.
- Recommended: a tag ruleset on `v*` that blocks updates and deletions, so a pushed tag cannot be moved.

## The steps

1. **On the release branch** (`release/X.Y`, cut from `main`; [CONTRIBUTING.md](../CONTRIBUTING.md#versions)):
   the changelog's `[Unreleased]` becomes `[X.Y.Z] - YYYY-MM-DD` in the same commit that sets `onloop.__version__`,
   and `RELEASE_NOTES.md` says what the release is for. Look at `.github/audit-allowlist.yml` for entries that expire
   soon.
2. **Push the branch, and wait for the `ci` workflow to be green on that exact commit**: the `test` job on every
   Python in the matrix and both systems (lint, the tests, `onloop selftest`, the confidentiality scan), the `wheel`
   job (the UI built in, the sdist and the wheel built as a release builds them, the vulnerability audit), and the
   `e2e` job (the browser tests).
3. **Tag that commit**, annotated, and push the tag: `git tag -a vX.Y.Z -m "onloop X.Y.Z"`, then
   `git push origin vX.Y.Z`. A tag is never moved.
4. **The `release` workflow** runs on the tag. In order, it:
   - checks that the tag names the package's version;
   - builds the UI (after its unit tests), then the sdist and the wheel from it (`python -m build`), from the tag;
   - checks the wheel and the sdist carry the UI, and runs `onloop selftest` on the wheel it is about to release;
   - writes `SHA256SUMS`;
   - attests where the wheel and the sdist were built (GitHub artifact attestations, signed with Sigstore);
   - creates a **draft** release titled "onloop X.Y.Z", with the wheel, the sdist and `SHA256SUMS`, its notes from
     `RELEASE_NOTES.md`.
   It does not wait for the `ci` workflow and does not read its result: that is step 5's.
5. **A person checks, then publishes the draft.**
   - Read the `ci` run the tag started (`gh run list --workflow ci --branch vX.Y.Z`): every job green. The tag's run
     can be red although the branch's was green, when an audit allowlist entry expired or a new advisory appeared in
     between. Then do not publish: fix it on the branch and release the next patch version.
   - Open the draft (`gh release view vX.Y.Z`, or the repository's Releases page): the three files, the title and the
     notes.
   - Run the checks under "Verifying a release" on the draft's files (`gh release download` works on a draft for
     someone with access).
   - Publish: `gh release edit vX.Y.Z --draft=false`, or "Publish release" on the page.

What `ci` checked is the commit; the release builds its own files from that commit, and of those files only the
self-test and the UI checks run again.

Until the draft is published its files cannot be downloaded by others. The tag, its commit (in a public repository)
and the attestations are public already: the attestations, with each file's digest, are written to Sigstore's public
log before the draft exists.

**When it fails.**
- If the workflow fails before "Draft the release", nothing is drafted. If it fails while drafting, a draft may hold
  fewer than three files: delete it (`gh release delete vX.Y.Z`, which keeps the tag). Attestations already made stay;
  they name files nobody published.
- A transient failure (a runner fault, a registry not answering): delete any partial draft and re-run the failed run
  on the same tag.
- A real failure, or a red `ci` run on the tag: fix it on the branch, mark `[X.Y.Z]` in the changelog as not
  released, and start again at step 1 with the next patch version. A tag that was pushed is not moved or reused.

## Verifying a release

`SHA256SUMS` shows a download is complete and undamaged. The attestation shows the file was built by this
repository's `release` workflow, from the tag. Run both: the sums file itself is not signed.

You need the GitHub CLI, signed in (`gh auth login`). The file names below are examples: use the names in
`SHA256SUMS`.

```bash
gh release download vX.Y.Z --repo Zhihua-Zhong/onloop
sha256sum --check --ignore-missing SHA256SUMS
gh attestation verify onloop-X.Y.Z-py3-none-any.whl --repo Zhihua-Zhong/onloop \
  --signer-workflow Zhihua-Zhong/onloop/.github/workflows/release.yml --source-ref refs/tags/vX.Y.Z
gh attestation verify onloop-X.Y.Z.tar.gz --repo Zhihua-Zhong/onloop \
  --signer-workflow Zhihua-Zhong/onloop/.github/workflows/release.yml --source-ref refs/tags/vX.Y.Z
```

On macOS use `shasum -a 256 --check SHA256SUMS`; on Windows outside WSL or Git Bash, compare
`Get-FileHash <file> -Algorithm SHA256` with the file's line in `SHA256SUMS`.

`gh attestation verify` checks the file's digest against an attestation signed by the `release` workflow of this
repository, run at the tag, and prints the commit it ran at. That commit must be the tag's:
`git ls-remote https://github.com/Zhihua-Zhong/onloop "refs/tags/vX.Y.Z^{}"` prints it. A file changed after it was
built, or built by another workflow or at another ref, does not verify.

Without the GitHub CLI signed in, check the sums from the Releases page as above, and verify the attestation on a
machine that is signed in (`gh attestation download`, then `gh attestation verify --bundle` offline).

## Before 0.6.0

Releases up to 0.5.0 were built by hand from a clean archive of the tagged commit and uploaded by a person; they have
no attestation.
