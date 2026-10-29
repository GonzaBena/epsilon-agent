import pytest

from epsilon import core
from epsilon.ai import permissions
from epsilon.utils import apps, hyprland
from tests.test_core_tools import CID, OTHER, allow, results, say, script, step  # noqa: F401 - `script` es un fixture

# La IA moviéndose por el escritorio: espacios de trabajo, ventanas y apps. Hyprland y las apps instaladas son de mentira:
# estos tests no tocan el sistema (y nada de lo que diga el modelo llega a un shell).


@pytest.fixture
def desk(monkeypatch, tmp_path):
    """Un escritorio de mentira: anota las órdenes y contesta con textos fijos."""
    calls = []
    monkeypatch.setattr(hyprland, "go_to_workspace", lambda target: calls.append(("go", target)) or f"Estás en el espacio {target}.")
    monkeypatch.setattr(hyprland, "move_window", lambda number: calls.append(("move", number)) or f"Mandé la ventana al espacio {number}.")
    monkeypatch.setattr(hyprland, "focus_window", lambda query: calls.append(("focus", query)) or f"Enfoqué {query} (espacio 1).")
    monkeypatch.setattr(hyprland, "close_window", lambda: calls.append(("close",)) or "Cerré la ventana de kitty.")
    monkeypatch.setattr(hyprland, "run", lambda command: calls.append(("run", command)))
    folder = tmp_path / "applications"
    folder.mkdir()
    for file_name, name in (("firefox.desktop", "Firefox"), ("chromium.desktop", "Chromium"), ("spotify.desktop", "Spotify")):
        (folder / file_name).write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec=true\nGenericName=Navegador web\n" if name != "Spotify" else f"[Desktop Entry]\nType=Application\nName={name}\nExec=true\n")
    monkeypatch.setattr(apps, "data_dirs", lambda: [folder])
    return calls


def ask_model(script, call, reply="Listo"):
    script.steps = [step(calls=[call]), step(reply)]


def test_a_workspace_number_changes_the_workspace(script, desk):
    ask_model(script, ("pc.workspace", "3"))

    assert say("pasame al 3").text == "Listo"

    assert desk == [("go", 3)]
    assert results(script.seen[1][0])[0].endswith("Estás en el espacio 3.")


def test_mover_sends_the_active_window_instead(script, desk):
    ask_model(script, ("pc.workspace", "mover 4"))

    say("mandá esta ventana al 4")

    assert desk == [("move", 4)]


def test_the_previous_workspace(script, desk):
    ask_model(script, ("pc.workspace", "anterior"))

    say("volvé al anterior")

    assert desk == [("go", "previous")]


@pytest.mark.parametrize("bad", ["11", "special:scratchpad", "3; exec reboot", "tres"])
def test_a_workspace_that_makes_no_sense_never_reaches_hyprland(script, desk, bad):
    ask_model(script, ("pc.workspace", bad))

    say("andá a cualquier lado")

    assert desk == []
    assert "espacio de trabajo" in results(script.seen[1][0])[0]


def test_focusing_a_window_passes_the_name_through_the_window_list(script, desk):
    ask_model(script, ("pc.foco", "firefox"))

    say("pasame a firefox")

    assert desk == [("focus", "firefox")]


def test_an_installed_app_is_opened_through_its_desktop_file(script, desk):
    allow("app_abrir")
    ask_model(script, ("pc.abrir", "spotify"))

    say("abrí spotify")

    ((kind, command),) = desk
    assert kind == "run" and command.startswith("gio launch '") and command.endswith("spotify.desktop'")
    assert "Spotify" in results(script.seen[1][0])[0]


def test_your_own_alias_wins_and_runs_exactly_your_command(script, desk, monkeypatch):
    allow("app_abrir")
    monkeypatch.setenv("PC_APPS", "navegador=omarchy-launch-browser,notas=obsidian --new-window")
    ask_model(script, ("pc.abrir", "Navegador"))

    say("abrí el navegador")

    assert desk == [("run", "omarchy-launch-browser")]


def test_two_equally_good_apps_are_listed_so_the_person_chooses(script, desk):
    allow("app_abrir")
    ask_model(script, ("pc.abrir", "navegador"))

    say("abrí el navegador")

    assert desk == []
    sent = results(script.seen[1][0])[0]
    assert "Firefox" in sent and "Chromium" in sent and "cuál" in sent.lower()


def test_an_unknown_app_is_said_with_a_few_names_and_nothing_is_run(script, desk):
    allow("app_abrir")
    ask_model(script, ("pc.abrir", "photoshop"))

    say("abrí photoshop")

    assert desk == []
    sent = results(script.seen[1][0])[0]
    assert "No conozco" in sent and "Spotify" in sent


@pytest.mark.parametrize("evil", ["firefox; reboot", "$(reboot)", "firefox && rm -rf ~", "`reboot`", "a\nb", "../../bin/sh"])
def test_text_from_the_model_is_never_a_command(script, desk, evil):
    allow("app_abrir")
    ask_model(script, ("pc.abrir", evil))

    say("abrí algo")

    assert not any(call[0] == "run" for call in desk)


def test_closing_a_window_asks_first_in_a_normal_chat(script, desk):
    ask_model(script, ("pc.cerrar", ""))

    reply = say("cerrá esta ventana")

    assert reply.actions and desk == []  # pregunta y todavía no cerró nada


def test_by_voice_closing_a_window_is_never_done(script, desk):
    permissions.set_mode(CID, permissions.SAFE_AUTO)
    ask_model(script, ("pc.cerrar", ""))

    say("cerrá esta ventana")

    assert desk == []
    assert results(script.seen[1][0]) == [core.UNAVAILABLE]


def test_by_voice_the_rest_runs_without_asking(script, desk):
    permissions.set_mode(CID, permissions.SAFE_AUTO)
    script.steps = [step(calls=[("pc.workspace", "2"), ("pc.abrir", "spotify")]), step("Listo")]

    reply = say("pasame al 2 y abrí spotify")

    assert reply.text == "Listo" and not reply.actions
    assert [call[0] for call in desk] == ["go", "run"]


def test_a_system_error_comes_back_as_text_not_a_crash(script, monkeypatch):
    def broken(target):
        raise hyprland.HyprlandError("Hyprland no aceptó la orden (error)")

    monkeypatch.setattr(hyprland, "go_to_workspace", broken)
    ask_model(script, ("pc.workspace", "3"))

    assert say("al 3").text == "Listo"
    assert "no aceptó" in results(script.seen[1][0])[0]


def test_opening_an_app_asks_first_in_a_normal_chat(script, desk):
    ask_model(script, ("pc.abrir", "spotify"))

    reply = say("abrí spotify")

    assert reply.actions and desk == []
