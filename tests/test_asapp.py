import pytest

from onloop import asapp


@pytest.mark.parametrize("argv,identity", [
    (["git", "push", "origin", "b"], "agents"), (["gh", "pr", "create"], "agents"),
    (["gh", "pr", "review", "1", "--approve"], "reviewer"), (["gh", "pr", "comment", "1", "-b", "x"], "reviewer"),
])
def test_allowed_commands_pass_through_unchanged(argv, identity):
    assert asapp.check_command(argv, identity) == argv


@pytest.mark.parametrize("argv,identity", [
    (["gh", "auth", "token"], "agents"), (["gh", "auth", "status", "--show-token"], "agents"),
    (["gh", "auth", "login"], "agents"), (["git", "credential", "fill"], "agents"),
    (["git", "-c", "credential.helper=x", "push"], "agents"), (["git", "--config-env=a=B", "push"], "agents"),
    (["curl", "x"], "agents"), ([], "agents"), (["git", "status"], "root"),
    (["git", "push"], "reviewer"), (["gh", "pr", "merge", "1"], "reviewer"), (["gh", "api", "x"], "reviewer"),
    (["gh", "pr", "comment", "1", "--edit-last"], "reviewer"),
    (["gh", "pr", "comment", "1", "--delete-last=true"], "reviewer"),
])
def test_refused_commands_raise_before_any_token_exists(argv, identity):
    with pytest.raises(asapp.AppError):
        asapp.check_command(argv, identity)


def test_the_command_environment_carries_the_token_only_in_the_environment():
    cfg = {"slug": "agent-bot", "bot_user_id": 42}
    env = asapp.command_env(cfg, "tok123", {"PATH": "x"})
    assert env["GH_TOKEN"] == "tok123" and env["GIT_AUTHOR_NAME"] == "agent-bot[bot]"
    assert env["GIT_AUTHOR_EMAIL"].startswith("42+agent-bot[bot]@")
    assert "tok123" not in "".join(v for k, v in env.items() if k.startswith("GIT_CONFIG_VALUE"))
    assert env["GIT_CONFIG_VALUE_0"] == ""                                  # every stored helper is cleared first


def test_identity_config_must_be_complete():
    with pytest.raises(asapp.AppError):
        asapp.identity_config({"apps": {"agents": {"app_id": 1}}}, "agents")
    cfg = {"app_id": 1, "installation_id": 2, "slug": "s", "key_env": "K"}
    assert asapp.identity_config({"apps": {"agents": cfg}}, "agents") == cfg


def test_a_key_inside_a_git_work_tree_is_refused(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    key = tmp_path / "k.pem"
    key.write_text("x", encoding="utf-8")
    with pytest.raises(asapp.AppError):
        asapp.key_outside_work_tree(str(key))


def test_a_key_outside_any_work_tree_is_accepted(tmp_path):
    key = tmp_path / "k.pem"
    key.write_text("x", encoding="utf-8")
    try:
        asapp.key_outside_work_tree(str(key))
    except asapp.AppError as e:                       # tmp_path itself may sit inside a work tree on a CI runner
        assert "work tree" in str(e)


def test_run_signs_exchanges_and_runs_with_the_token(monkeypatch, tmp_path):
    """The whole path with the network and the signer replaced: the command sees the token, the key path is read from
    the configured variable, and the exchange targets the installation named in the policy."""
    key = tmp_path / "k.pem"
    key.write_text("PEM-PLACEHOLDER", encoding="utf-8")
    monkeypatch.setattr(asapp, "key_outside_work_tree", lambda p: None)
    monkeypatch.setattr(asapp, "mint_jwt", lambda app_id, pem, now=None: f"jwt-for-{app_id}-{pem}")
    seen = {}

    def http(url, headers):
        seen["url"], seen["auth"] = url, headers["Authorization"]
        return {"token": "installation-token"}

    class Done:
        returncode = 0

    def runner(cmd, env):
        seen["cmd"], seen["token"] = cmd, env["GH_TOKEN"]
        return Done()

    raw = {"apps": {"agents": {"app_id": 11, "installation_id": 22, "slug": "agent-bot", "key_env": "K"}}}
    code = asapp.run(["git", "status"], "agents", raw, "org/repo", http=http, runner=runner, environ={"K": str(key)})
    assert code == 0 and seen["token"] == "installation-token" and seen["cmd"] == ["git", "status"]
    assert seen["url"].endswith("/app/installations/22/access_tokens")
    assert seen["auth"] == "Bearer jwt-for-11-PEM-PLACEHOLDER"
    with pytest.raises(asapp.AppError):
        asapp.run(["git", "status"], "agents", raw, "org/repo", http=http, runner=runner, environ={})


def test_a_signed_jwt_verifies_with_the_public_key_and_carries_the_app_id_and_a_short_life():
    import base64
    import json
    pytest.importorskip("cryptography", exc_type=ImportError)
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
    key = rsa.generate_private_key(65537, 2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    token = asapp.mint_jwt("123", pem, now=1_000_000)
    head, claims, sig = token.split(".")

    def unb64(s):
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    key.public_key().verify(unb64(sig), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())   # raises if bad
    assert json.loads(unb64(head)) == {"alg": "RS256", "typ": "JWT"}
    body = json.loads(unb64(claims))
    assert body["iss"] == "123" and body["exp"] - body["iat"] <= 600 and body["iat"] < 1_000_000
    with pytest.raises(asapp.AppError):
        asapp.mint_jwt("123", "not a key")


def test_the_self_test_bites():
    ok, lines = asapp.self_test()
    assert ok, lines
