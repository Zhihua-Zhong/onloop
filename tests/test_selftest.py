import pytest

from onloop import anchor, cli, intake, kpi, preflight, slots, watchdog


@pytest.mark.parametrize(
    "mod",
    [anchor, intake, kpi, preflight, slots, watchdog],
    ids=lambda m: m.__name__.split(".")[-1],
)
def test_every_new_checker_has_a_self_test_that_passes(mod):
    ok, lines = mod.self_test()
    assert ok, lines
    assert lines and all("ok" in line for line in lines)


def test_the_selftest_command_runs_every_checker_and_exits_0(capsys):
    assert cli.main(["selftest"]) == 0
    out = capsys.readouterr().out
    for name in (
        "slots",
        "pre-flight",
        "KPIs",
        "anchors",
        "intake",
        "turn outcome line",
        "detector: a turn without a valid outcome",
        "detector: a continue whose waker is not alive",
        "detector: a scheduled job that failed or did not run",
    ):
        assert f"{name}: bites" in out
    assert "verdict: every checker still bites" in out
