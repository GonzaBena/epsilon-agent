import pytest

from epsilon.utils import hyprland

REAL_CTL = hyprland._ctl  # conftest lo reemplaza en cada test; este es el verdadero


class FakeHyprland:
    """Un hyprctl de mentira: contesta como el Hyprland nuevo (Lua) o como el viejo (keyword)."""

    def __init__(self, monkeypatch, lua: bool, answer: str = "ok"):
        self.lua = lua
        self.answer = answer
        self.calls = []
        monkeypatch.setattr(hyprland, "_ctl", self.ctl)

    def ctl(self, *args):
        self.calls.append(args)
        if args[0] == "eval":
            if args[1] == hyprland.PROBE:
                return (0, "ok") if self.lua else (1, "Invalid request")
            return (0, self.answer) if self.lua else (1, "Invalid request")
        if args[0] == "keyword":
            if self.lua:
                return (0, "keyword can't work with non-legacy parsers. Use eval.")  # sí: con código 0
            return (0, self.answer)
        if args[0] == "dispatch":
            if self.lua:  # el Hyprland nuevo espera una expresión Lua; «workspace 3» le da un error (con código 7)
                return (0, self.answer) if args[1].startswith("hl.") else (7, "error: ')' expected")
            return (0, self.answer) if args[1] in ("workspace", "movetoworkspace", "focuswindow", "killactive", "exec") else (0, "Invalid dispatcher")
        raise AssertionError(args)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ALT, X", "ALT, X"),
        ("alt, x", "ALT, X"),
        ("  Super   Shift ,  return ", "SUPER SHIFT, RETURN"),
        ("CTRL ALT, F9", "CTRL ALT, F9"),
        ("SUPER, 1", "SUPER, 1"),
    ],
)
def test_a_hotkey_is_written_in_one_canonical_form(text, expected):
    assert hyprland.normalize_hotkey(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "X",  # sin modificador: se comería la tecla al escribir
        ", X",
        "ALT,",
        "ALT, X, exec, rm -rf ~",  # un atajo no puede colar otra acción
        "ALT, X\nbind = SUPER, Q, exec, reboot",
        "ALT ALT, X",
        "HYPER, X",
        "ALT, XY",
        "ALT, F99",
        "ALT, semicolon;",
        'ALT, X")\nos.execute("reboot',  # ni código Lua
    ],
)
def test_a_hotkey_that_is_not_a_plain_modifier_and_key_is_rejected(text):
    with pytest.raises(ValueError):
        hyprland.normalize_hotkey(text)


def test_the_conf_file_has_a_single_bind_that_runs_our_script():
    text = hyprland.conf_text("alt, x", "/home/me/.local/share/epsilon/epsilon-voice-toggle")

    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert lines == ["bind = ALT, X, exec, /home/me/.local/share/epsilon/epsilon-voice-toggle"]


def test_the_lua_file_unbinds_first_so_loading_it_twice_never_doubles_the_key():
    text = hyprland.lua_text("super shift, z", "/opt/epsilon-voice-toggle")

    lines = [line for line in text.splitlines() if line and not line.startswith("--")]
    assert lines == [
        'hl.unbind("SUPER + SHIFT + Z")',
        'hl.bind("SUPER + SHIFT + Z", hl.dsp.exec_cmd("/opt/epsilon-voice-toggle"))',
    ]


@pytest.mark.parametrize("command", ["/tmp/a b", "/tmp/a\nbind = SUPER, Q, exec, reboot", '/tmp/a")os.execute("x', "/tmp/a\\b", "/tmp/a,b", "", "/tmp/[[x]]"])
def test_a_command_with_anything_unusual_is_refused_in_both_styles(command):
    with pytest.raises(ValueError):
        hyprland.conf_text("ALT, X", command)
    with pytest.raises(ValueError):
        hyprland.lua_text("ALT, X", command)


def test_write_conf_leaves_both_files_so_either_style_of_config_can_include_it(tmp_path):
    conf, lua = hyprland.write_conf(tmp_path, "ALT, X", "/opt/toggle")

    assert (conf, lua) == (tmp_path / "hyprland-voice.conf", tmp_path / "hyprland-voice.lua")
    assert "bind = ALT, X, exec, /opt/toggle" in conf.read_text()
    assert 'hl.bind("ALT + X"' in lua.read_text()


# --- Aplicar en caliente: Hyprland nuevo (Lua) y viejo (keyword) --------------------------------------------------


def test_with_lua_config_the_hotkey_is_applied_with_eval(monkeypatch):
    fake = FakeHyprland(monkeypatch, lua=True)

    hyprland.apply_hotkey("alt, x", "/opt/toggle")

    assert fake.calls[0] == ("eval", hyprland.PROBE)
    assert len(fake.calls) == 2
    kind, code = fake.calls[1]
    assert kind == "eval"
    assert code.splitlines() == ['hl.unbind("ALT + X")', 'hl.bind("ALT + X", hl.dsp.exec_cmd("/opt/toggle"))']


def test_changing_the_hotkey_unbinds_the_old_one_first_with_lua(monkeypatch):
    fake = FakeHyprland(monkeypatch, lua=True)

    hyprland.apply_hotkey("SUPER, Z", "/opt/toggle", previous="ALT, X")

    assert fake.calls[1][1].splitlines() == [
        'hl.unbind("ALT + X")',
        'hl.unbind("SUPER + Z")',
        'hl.bind("SUPER + Z", hl.dsp.exec_cmd("/opt/toggle"))',
    ]


def test_with_legacy_config_the_hotkey_is_applied_with_keyword_without_leaving_a_duplicate(monkeypatch):
    # `keyword bind` suma otro bind aunque ya exista (p. ej. el del archivo incluido con `source`): sin soltarlo antes,
    # un toque dispararía el atajo dos veces y lo cancelaría.
    fake = FakeHyprland(monkeypatch, lua=False)

    hyprland.apply_hotkey("alt, x", "/opt/toggle")

    assert fake.calls[1:] == [("keyword", "unbind", "ALT, X"), ("keyword", "bind", "ALT, X, exec, /opt/toggle")]


def test_changing_the_hotkey_unbinds_the_old_one_first_with_legacy(monkeypatch):
    fake = FakeHyprland(monkeypatch, lua=False)

    hyprland.apply_hotkey("SUPER, Z", "/opt/toggle", previous="ALT, X")

    assert fake.calls[1:] == [
        ("keyword", "unbind", "ALT, X"),
        ("keyword", "unbind", "SUPER, Z"),
        ("keyword", "bind", "SUPER, Z, exec, /opt/toggle"),
    ]


def test_an_answer_that_is_not_ok_is_an_error_even_if_hyprctl_exited_with_zero(monkeypatch):
    FakeHyprland(monkeypatch, lua=False, answer="invalid dispatcher")

    with pytest.raises(hyprland.HyprlandError, match="no aceptó"):
        hyprland.apply_hotkey("ALT, X", "/opt/toggle")


def test_a_lua_error_is_reported_instead_of_pretending_it_worked(monkeypatch):
    FakeHyprland(monkeypatch, lua=True, answer='error: [string "..."]:1: unexpected symbol')

    with pytest.raises(hyprland.HyprlandError, match="no aceptó"):
        hyprland.apply_hotkey("ALT, X", "/opt/toggle")


def test_an_invalid_hotkey_changes_nothing(monkeypatch):
    fake = FakeHyprland(monkeypatch, lua=True)

    with pytest.raises(ValueError):
        hyprland.apply_hotkey("X", "/opt/toggle", previous="ALT, X")

    assert fake.calls == []


def test_apply_returns_the_normalized_hotkey(monkeypatch):
    FakeHyprland(monkeypatch, lua=True)

    assert hyprland.apply_hotkey("alt, x", "/opt/toggle") == "ALT, X"


# --- El único punto que toca el sistema ---------------------------------------------------------------------------


def test_ctl_returns_the_exit_code_and_the_trimmed_answer(monkeypatch):
    class Done:
        returncode = 7
        stdout = "error: algo\n"

    monkeypatch.setattr(hyprland.subprocess, "run", lambda *a, **k: Done())

    assert REAL_CTL("eval", "x") == (7, "error: algo")


def test_a_missing_hyprctl_says_this_only_works_with_hyprland(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(hyprland.subprocess, "run", missing)

    with pytest.raises(hyprland.HyprlandError, match="hyprctl"):
        REAL_CTL("eval", "x")


def test_hyprctl_hanging_is_a_clear_error(monkeypatch):
    def hang(*args, **kwargs):
        raise hyprland.subprocess.TimeoutExpired("hyprctl", 5)

    monkeypatch.setattr(hyprland.subprocess, "run", hang)

    with pytest.raises(hyprland.HyprlandError, match="Hyprland"):
        REAL_CTL("eval", "x")


# --- Mover cosas: workspaces, ventanas y apps (Voice Companion, paso 5) ---------------------------------------------------


@pytest.fixture
def windows(monkeypatch):
    """Lo que Hyprland diría de sus ventanas (`hyprctl -j clients|activewindow|activeworkspace`)."""

    def client(address, cls, title, workspace, focus):
        return {"address": address, "class": cls, "title": title, "workspace": {"id": workspace, "name": str(workspace)}, "mapped": True, "hidden": False, "focusHistoryID": focus}

    data = {
        "clients": [client("0xaaa", "kitty", "agy", 1, 0), client("0xbbb", "kitty", "notas", 2, 3), client("0xccc", "firefox", "Mi banco", 2, 1), client("0xddd", "obsidian", "Diario", 3, 2)],
        "activewindow": client("0xaaa", "kitty", "agy", 1, 0),
        "activeworkspace": {"id": 1, "name": "1"},
    }
    monkeypatch.setattr(hyprland.ventanas, "_load", lambda what: data[what])
    return data


@pytest.mark.parametrize(
    "text, expected",
    [
        ("3", ("go", 3)), (" 10 ", ("go", 10)), ("ir 2", ("go", 2)), ("ir al 4", ("go", 4)), ("anterior", ("go", "previous")),
        ("mover 3", ("move", 3)), ("mover a 5", ("move", 5)), ("Mover A 1", ("move", 1)),
    ],
)
def test_the_workspace_request_is_understood_from_a_closed_set_of_phrases(text, expected):
    assert hyprland.parse_workspace(text) == expected


@pytest.mark.parametrize("text", ["", "0", "11", "-1", "tres", "special:scratchpad", "3; exec reboot", "e+1", "mover", "mover 99"])
def test_anything_else_is_not_a_workspace(text):
    with pytest.raises(ValueError):
        hyprland.parse_workspace(text)


def test_going_to_a_workspace_uses_the_lua_api_and_confirms_where_you_are(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)
    windows["activeworkspace"] = {"id": 3, "name": "3"}

    text = hyprland.go_to_workspace(3)

    assert fake.calls[1] == ("dispatch", 'hl.dsp.focus({ workspace = "3" })')
    assert "3" in text


def test_going_back_to_the_previous_workspace(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)

    hyprland.go_to_workspace("previous")

    assert fake.calls[1] == ("dispatch", 'hl.dsp.focus({ workspace = "previous" })')


def test_with_a_classic_config_the_same_orders_use_the_old_dispatchers(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=False)

    hyprland.go_to_workspace(2)
    hyprland.move_window(4)
    hyprland.close_window()

    assert fake.calls[1:] == [
        ("dispatch", "workspace", "2"),
        ("eval", hyprland.PROBE),
        ("dispatch", "movetoworkspace", "4"),
        ("eval", hyprland.PROBE),
        ("dispatch", "killactive"),
    ]


def test_moving_the_active_window_names_the_app_and_the_destination(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)

    text = hyprland.move_window(4)

    assert fake.calls[1] == ("dispatch", 'hl.dsp.window.move({ workspace = "4" })')
    assert "kitty" in text and "4" in text


@pytest.mark.parametrize("bad", [0, 11, "3", None, 2.5, True])
def test_a_workspace_outside_one_to_ten_is_refused_before_touching_hyprland(monkeypatch, windows, bad):
    fake = FakeHyprland(monkeypatch, lua=True)

    with pytest.raises(ValueError):
        hyprland.move_window(bad)

    assert fake.calls == []


def test_focusing_by_name_uses_the_exact_address_from_the_window_list(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)

    text = hyprland.focus_window("Obsidian")

    assert fake.calls[1] == ("dispatch", 'hl.dsp.focus({ window = "address:0xddd" })')
    assert "obsidian" in text.lower() and "3" in text


def test_the_title_also_finds_a_window_but_a_class_match_wins(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)

    hyprland.focus_window("banco")

    assert fake.calls[1][1] == 'hl.dsp.focus({ window = "address:0xccc" })'


def test_several_windows_of_the_same_app_focus_the_most_recent_one(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)

    hyprland.focus_window("kitty")

    assert fake.calls[1][1] == 'hl.dsp.focus({ window = "address:0xaaa" })'  # la de focusHistoryID más chico


def test_different_apps_matching_equally_are_listed_not_guessed(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)
    windows["clients"][3]["class"] = "kitty-notes"  # ahora hay dos apps que contienen «kitty»... y la exacta sigue ganando
    windows["clients"][0]["class"] = "kitty-a"
    windows["clients"][1]["class"] = "kitty-b"

    text = hyprland.focus_window("kitty")

    assert fake.calls == []
    assert "kitty-a" in text and "kitty-b" in text and "cuál" in text


def test_a_window_that_does_not_exist_is_said_plainly(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)

    text = hyprland.focus_window("photoshop")

    assert fake.calls == [] and "No encontré" in text


def test_a_hidden_or_unmapped_window_is_never_a_target(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)
    for client in windows["clients"]:
        client["mapped"] = False

    assert "No encontré" in hyprland.focus_window("kitty")
    assert fake.calls == []


def test_a_window_title_never_reaches_the_answer_because_it_can_be_private(monkeypatch, windows):
    FakeHyprland(monkeypatch, lua=True)

    assert "banco" not in hyprland.focus_window("Mi banco").lower().replace("firefox", "")


def test_closing_closes_only_the_active_window_and_says_which(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)

    text = hyprland.close_window()

    assert fake.calls[1] == ("dispatch", "hl.dsp.window.close()")
    assert "kitty" in text


def test_closing_with_no_window_in_focus_does_nothing(monkeypatch, windows):
    fake = FakeHyprland(monkeypatch, lua=True)
    windows["activewindow"] = {}

    text = hyprland.close_window()

    assert fake.calls == [] and "ninguna ventana" in text


def test_running_a_command_goes_through_hyprland_exec_in_both_styles(monkeypatch):
    lua = FakeHyprland(monkeypatch, lua=True)
    hyprland.run("gio launch '/usr/share/applications/Youtube Music.desktop'")
    classic = FakeHyprland(monkeypatch, lua=False)
    hyprland.run("omarchy-launch-browser")

    assert lua.calls[1] == ("dispatch", "hl.dsp.exec_cmd(\"gio launch '/usr/share/applications/Youtube Music.desktop'\")")
    assert classic.calls[1] == ("dispatch", "exec", "omarchy-launch-browser")


@pytest.mark.parametrize("command", ["", 'x"); os.execute("reboot', "a\\b", "uno\ndos", "a;b", "a|b", "$(x)", "a`b`"])
def test_a_command_with_anything_unusual_is_never_sent_to_hyprland(monkeypatch, command):
    fake = FakeHyprland(monkeypatch, lua=True)

    with pytest.raises(ValueError):
        hyprland.run(command)

    assert fake.calls == []


def test_a_warning_from_hyprland_is_an_error_even_with_exit_code_zero(monkeypatch, windows):
    FakeHyprland(monkeypatch, lua=True, answer="warning: =[C]:-1: Bad workspace")

    with pytest.raises(hyprland.HyprlandError, match="no aceptó"):
        hyprland.go_to_workspace(3)
