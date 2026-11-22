# Releasing onloop

A release is built by CI from its tag and published by a person. Nothing is uploaded from a developer's machine.

## The steps

1. **On the release branch** (`release/X.Y`, cut from `main`; [CONTRIBUTING.md](../CONTRIBUTING.md#versions)):
   the changelog's `[Unreleased]` becomes `[X.Y.Z] - YYYY-MM-DD` in the same commit that sets `onloop.__version__` in
   `src/onloop/__init__.py`, and `RELEASE_NOTES.md` says what the release is for.
2. **Push the branch, and wait for CI to be green on that exact commit**: the tests on every Python in the matrix and
   both systems, the wheel job (the UI built in, the vulnerability audit), and the browser tests.
3. **Tag that commit**, annotated, and push the tag: `git tag -a vX.Y.Z -m "onloop X.Y.Z"`, then
   `git push origin vX.Y.Z`. A tag is never moved.
4. **The `release` workflow** runs on the tag (`.github/workflows/release.yml`). It:
   - checks that the tag names the package's version;
   - builds the UI, then the wheel and the sdist, from the tag;
   - checks the wheel carries the UI;
   - writes `SHA256SUMS`;
   - attests where the wheel and the sdist were built (GitHub artifact attestations, signed with Sigstore);
   - creates a **draft** release with the wheel, the sdist and `SHA256SUMS`, its notes from `RELEASE_NOTES.md`.
5. **A person publishes the draft**, after reading that CI is green on the tag's commit (the `ci` workflow also runs
   on `v*` tags) and that the draft holds the three files. Until then nothing is public.

If the workflow fails, nothing is drafted: fix the cause on the branch, and release a new patch version. A tag that
was pushed is not moved or reused.

## Verifying a release

Each file comes with its sha256 in `SHA256SUMS`, and with an attestation that says which workflow run, at which
commit of this repository, built it.

```bash
gh release download vX.Y.Z --repo Zhihua-Zhong/onloop
sha256sum --check SHA256SUMS
gh attestation verify onloop-X.Y.Z-py3-none-any.whl --repo Zhihua-Zhong/onloop
gh attestation verify onloop-X.Y.Z.tar.gz --repo Zhihua-Zhong/onloop
```

`gh attestation verify` checks the file's digest against an attestation signed for this repository's workflow, and
prints the workflow and the commit it ran at. A file that was changed after it was built, or built elsewhere, does not
verify.

## Before 0.6.0

Releases up to 0.5.0 were built by hand from a clean archive of the tagged commit and uploaded by a person; they have
no attestation.
