from epsilon import state


def test_set_and_snapshot():
    state.set("telegram", "running", "Conectado como @mi_bot")

    assert state.snapshot()["telegram"] == {
        "state": "running",
        "detail": "Conectado como @mi_bot",
    }


def test_snapshot_is_a_copy():
    state.set("web", "running")

    state.snapshot()["web"]["state"] = "stopped"

    assert state.snapshot()["web"]["state"] == "running"


def test_a_later_state_replaces_the_earlier_one():
    state.set("telegram", "starting")
    state.set("telegram", "stopped", "token rechazado")

    assert state.snapshot()["telegram"]["state"] == "stopped"


def test_every_change_prints_one_readable_line(capsys):
    state.set("telegram", "stopped", "Revisá TELEGRAM_TOKEN")

    out = capsys.readouterr().out
    assert out == "Canal telegram: detenido. Revisá TELEGRAM_TOKEN\n"


def test_each_state_has_a_plain_label(capsys):
    for name, expected in [
        ("starting", "conectando"),
        ("running", "activo"),
        ("skipped", "sin configurar, se omite"),
        ("stopped", "detenido"),
    ]:
        state.set("x", name)
        assert expected in capsys.readouterr().out


def test_reset_clears_everything():
    state.set("web", "running")

    state.reset()

    assert state.snapshot() == {}
