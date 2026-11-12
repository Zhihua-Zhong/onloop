import json

from onloop import cli, hooks


def test_every_state_event_records_and_nothing_else():
    cfg = hooks.liveness_config(directory="D:/state/liveness")
    assert set(cfg["hooks"]) == set(hooks.LIVENESS_EVENTS)
    assert {"Notification", "Stop", "UserPromptSubmit"} <= set(cfg["hooks"])
    assert not {"PreToolUse", "PostToolUse"} & set(cfg["hooks"])  # no process spawn per tool call
    for event, groups in cfg["hooks"].items():
        (command,) = [h["command"] for g in groups for h in g["hooks"]]
        assert command == f"onloop liveness record --event {event} --dir D:/state/liveness"
        assert "anchor" not in command


def test_print_only_needs_no_role(capsys):
    assert cli.main(["hooks", "--liveness"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == hooks.liveness_config()


def test_without_liveness_a_role_is_still_required(capsys):
    assert cli.main(["hooks"]) == 2
