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
