"""Agent identity: run one `git` or `gh` command as a GitHub App installation, never as the human.

    onloop as-app --identity agents -- git push origin my-branch
    onloop as-app --identity reviewer -- gh pr review 12 --approve --body-file verdict.md

Each call signs a short-lived JWT for the App, exchanges it for an installation token scoped to the repository,
and runs the command with that token in its environment only. Nothing is written to disk, and the token is on no
command line. The human's own review then means a human reviewed it, because no agent holds the human's account.

Identities are configured under `apps:` in `onloop.yaml`; the private key is a file whose path is read from the
environment variable the configuration names. A key inside a git work tree is refused, so it cannot be committed.

The guards stop accidents, not malice: a program that has been handed a token can use it.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import subprocess
import time
import urllib.request
from collections.abc import Callable
from typing import Any

API = "https://api.github.com"
IDENTITIES = ("agents", "reviewer")
# What the reviewer identity may run, as the first two words after `gh`. Everything else is refused, `git` included.
REVIEWER_ALLOWED = (("pr", "review"), ("pr", "comment"))
JWT_LIFETIME = 540                       # seconds; GitHub allows ten minutes


class AppError(RuntimeError):
    pass


def check_command(argv: list[str], identity: str) -> list[str]:
    """The argv to run, or raise AppError. Refuses anything the identity may not do and every form that prints a token."""
    if identity not in IDENTITIES:
        raise AppError(f"unknown identity {identity!r}; use one of {', '.join(IDENTITIES)}")
    if not argv:
        raise AppError("give a git or gh command after --")
    prog = pathlib.Path(argv[0]).name.lower().removesuffix(".exe")
    args = argv[1:]
    low = [a.lower() for a in args]
    if prog not in ("git", "gh"):
        raise AppError(f"only git and gh run here, not {argv[0]!r}")
    if identity == "reviewer":
        if prog != "gh" or tuple(low[:2]) not in REVIEWER_ALLOWED:
            raise AppError("the reviewer identity may only run `gh pr review` and `gh pr comment`")
        if any(a.split("=", 1)[0] in ("--edit-last", "--delete-last") for a in low):
            raise AppError("the reviewer identity may not edit or delete a comment; post a new one")
    if prog == "gh":
        if low[:2] == ["auth", "token"] or (low[:2] == ["auth", "status"] and "--show-token" in low):
            raise AppError("refusing a form that prints the token")
        if low[:1] == ["auth"]:
            raise AppError("refusing `gh auth`: the identity is the App, not a stored login")
    if prog == "git":
        if low[:1] == ["credential"]:
            raise AppError("refusing `git credential`: it can print the token")
        for a in low:
            if a in ("-c", "--config-env") or a.startswith(("--config-env=", "-c=", "--exec-path")):
                raise AppError("refusing git options that swap the credential or the transport")
    return [argv[0], *args]


# -- the token --------------------------------------------------------------------------------------------------

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_jwt(app_id: str, private_key_pem: str, now: float | None = None) -> str:
    """A signed RS256 JWT for the App. Needs the optional `cryptography` package (`pip install 'onloop[app]'`).

    Signed with `cryptography` directly: a JWT is three base64url parts and one RSA PKCS#1 v1.5 signature, so no JWT
    library is needed and its import chain cannot break the command.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError as e:                                       # pragma: no cover - environment dependent
        raise AppError("signing needs the cryptography package: pip install 'onloop[app]'") from e
    t = int(now if now is not None else time.time())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = b64url(json.dumps({"iat": t - 60, "exp": t + JWT_LIFETIME, "iss": str(app_id)},
                               separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode()
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())            # type: ignore[union-attr]
    except (ValueError, TypeError, AttributeError) as e:
        raise AppError(f"the private key could not be used to sign: {type(e).__name__}") from e
    return f"{header}.{claims}.{b64url(signature)}"


Http = Callable[[str, dict[str, str]], dict[str, Any]]


def _post(url: str, headers: dict[str, str]) -> dict[str, Any]:                 # pragma: no cover - the network
    req = urllib.request.Request(url, method="POST", headers=headers, data=b"{}")
    with urllib.request.urlopen(req, timeout=30) as r:                           # noqa: S310 - fixed https URL
        return json.loads(r.read())


def installation_token(app_id: str, installation_id: str, key_pem: str, repo: str, *, http: Http | None = None,
                       now: float | None = None) -> str:
    """An installation access token limited to one repository. `http` is the only network call, and tests replace it."""
    token = mint_jwt(app_id, key_pem, now)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "onloop"}
    body = (http or _post)(f"{API}/app/installations/{installation_id}/access_tokens", headers)
    if "token" not in body:
        raise AppError("GitHub did not return an installation token")
    return str(body["token"])


def key_outside_work_tree(path: str) -> None:
    """Refuse a key that sits inside a git work tree: it could be committed by accident."""
    p = pathlib.Path(path).resolve()
    r = subprocess.run(["git", "-C", str(p.parent), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        raise AppError(f"refusing a private key inside a git work tree ({r.stdout.strip()}): keep it outside the repository")


def command_env(identity_cfg: dict[str, Any], token: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment the command runs in: the token for gh, a credential helper for git over HTTPS, the App's author."""
    env = dict(base if base is not None else os.environ)
    slug = identity_cfg["slug"]
    name = f"{slug}[bot]"
    bot_id = identity_cfg.get("bot_user_id")
    email = f"{bot_id}+{name}@users.noreply.github.com" if bot_id else f"{name}@users.noreply.github.com"
    env.update({
        "GH_TOKEN": token, "GITHUB_TOKEN": token,
        "GIT_AUTHOR_NAME": name, "GIT_COMMITTER_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_EMAIL": email,
        "GIT_TERMINAL_PROMPT": "0",
        # git never falls back to a stored login: every other helper is cleared, then one reads the token at run time
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.https://github.com.helper",
        "GIT_CONFIG_VALUE_1": '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f',
        "GIT_CONFIG_KEY_2": "url.https://github.com/.insteadOf", "GIT_CONFIG_VALUE_2": "git@github.com:",  # scan:allow
    })
    return env


def identity_config(policy_raw: dict[str, Any], identity: str) -> dict[str, Any]:
    apps = policy_raw.get("apps") or {}
    cfg = apps.get(identity)
    if not isinstance(cfg, dict) or not all(cfg.get(k) for k in ("app_id", "installation_id", "slug", "key_env")):
        raise AppError(f"`apps.{identity}` in onloop.yaml needs app_id, installation_id, slug and key_env")
    return cfg


def run(argv: list[str], identity: str, policy_raw: dict[str, Any], repo: str, *, http: Http | None = None,
        runner: Callable[..., Any] | None = None, environ: dict[str, str] | None = None) -> int:
    """Check the command, mint a token and run it. Returns the command's exit code."""
    cmd = check_command(argv, identity)
    cfg = identity_config(policy_raw, identity)
    env_in = environ if environ is not None else dict(os.environ)
    key_path = env_in.get(cfg["key_env"])
    if not key_path:
        raise AppError(f"set {cfg['key_env']} to the path of the {identity} App's private key (.pem)")
    key_outside_work_tree(key_path)
    key_pem = pathlib.Path(key_path).read_text(encoding="utf-8")
    token = installation_token(str(cfg["app_id"]), str(cfg["installation_id"]), key_pem, repo, http=http)
    env = command_env(cfg, token, env_in)
    p = (runner or subprocess.run)(cmd, env=env)
    return int(p.returncode)


def self_test() -> tuple[bool, list[str]]:
    """The command guard must refuse what it should and allow what it should."""
    cases = [
        ("agents: git push is allowed", ["git", "push", "origin", "b"], "agents", False),
        ("agents: gh pr create is allowed", ["gh", "pr", "create"], "agents", False),
        ("agents: gh auth token is refused", ["gh", "auth", "token"], "agents", True),
        ("agents: gh auth status --show-token is refused", ["gh", "auth", "status", "--show-token"], "agents", True),
        ("agents: git credential is refused", ["git", "credential", "fill"], "agents", True),
        ("agents: git -c swapping the helper is refused", ["git", "-c", "credential.helper=x", "push"], "agents", True),
        ("agents: a program other than git or gh is refused", ["curl", "https://example.com"], "agents", True),
        ("reviewer: gh pr review is allowed", ["gh", "pr", "review", "1", "--approve"], "reviewer", False),
        ("reviewer: git is refused", ["git", "push"], "reviewer", True),
        ("reviewer: gh pr merge is refused", ["gh", "pr", "merge", "1"], "reviewer", True),
        ("reviewer: editing a comment is refused", ["gh", "pr", "comment", "1", "--edit-last"], "reviewer", True),
        ("an unknown identity is refused", ["git", "status"], "root", True),
    ]
    ok, lines = True, []
    for label, argv, ident, want_refused in cases:
        try:
            check_command(argv, ident)
            refused = False
        except AppError:
            refused = True
        right = refused == want_refused
        ok &= right
        lines.append(f"  {'ok' if right else 'FAILED'} {label}")
    return ok, lines
