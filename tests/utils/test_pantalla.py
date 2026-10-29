import pytest

from epsilon.utils import pantalla, ventanas

REAL_GRIM = pantalla._grim  # conftest lo reemplaza en cada test; este es el verdadero

# Mirar la pantalla: grim se reemplaza por un falso (conftest bloquea el verdadero), así que estos tests no capturan nada.


class FakeGrim:
    def __init__(self, monkeypatch, tmp_path, code=0, content=b"\xff\xd8jpeg", stderr=""):
        self.calls = []
        self.code, self.content, self.stderr = code, content, stderr
        self.monitors = [{"name": "DP-1", "focused": False}, {"name": "HDMI-A-1", "focused": True}]
        runtime = tmp_path / "run"
        runtime.mkdir(mode=0o700)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-1")
        monkeypatch.setattr(pantalla, "_grim", self.grim)
        monkeypatch.setattr(ventanas, "_load", lambda what: self.monitors)
        self.runtime = runtime

    def grim(self, argv, env):
        self.calls.append((argv, env))
        if self.content is not None:
            open(argv[-1], "wb").write(self.content)
        return self.code, self.stderr


@pytest.fixture
def grim(monkeypatch, tmp_path):
    return FakeGrim(monkeypatch, tmp_path)


def test_it_captures_only_the_monitor_that_has_the_focus_as_a_jpeg(grim):
    data = pantalla.capture()

    argv, _ = grim.calls[0]
    assert data == b"\xff\xd8jpeg"
    assert argv[:7] == ["grim", "-t", "jpeg", "-q", "80", "-o", "HDMI-A-1"]
    assert argv[-1].endswith(".jpg")


def test_without_a_focused_monitor_it_captures_everything(grim):
    grim.monitors = [{"name": "DP-1", "focused": False}]

    pantalla.capture()

    assert "-o" not in grim.calls[0][0]


@pytest.mark.parametrize("name", ["DP 1", "DP-1; reboot", "x" * 80, "", 'a"b', "../x"])
def test_a_monitor_name_that_is_not_plain_is_never_passed_on(grim, name):
    grim.monitors = [{"name": name, "focused": True}]

    pantalla.capture()

    assert "-o" not in grim.calls[0][0]


def test_the_file_lives_in_a_private_folder_of_the_runtime_dir_and_is_gone_afterwards(grim, monkeypatch):
    seen = {}
    original = grim.grim

    def spy(argv, env):
        from pathlib import Path

        seen["path"] = Path(argv[-1])
        seen["mode"] = seen["path"].parent.stat().st_mode & 0o777
        return original(argv, env)

    monkeypatch.setattr(pantalla, "_grim", spy)
    pantalla.capture()

    assert seen["path"].parent == grim.runtime / pantalla.FOLDER
    assert seen["mode"] == 0o700  # /tmp lo leería cualquiera; acá solo vos
    assert not seen["path"].exists()  # los bytes quedan en memoria: el archivo se borra


def test_the_file_is_deleted_even_when_grim_fails_halfway(monkeypatch, tmp_path):
    grim = FakeGrim(monkeypatch, tmp_path, code=1, content=b"a medias", stderr="compositor does not support it")

    with pytest.raises(pantalla.PantallaError):
        pantalla.capture()

    assert list((grim.runtime / pantalla.FOLDER).glob("*")) == []


def test_each_capture_gets_its_own_file_name(grim):
    pantalla.capture()
    pantalla.capture()

    first, second = (call[0][-1] for call in grim.calls)
    assert first != second


def test_a_missing_grim_says_what_to_install(monkeypatch, tmp_path):
    FakeGrim(monkeypatch, tmp_path)

    def missing(argv, env):
        raise FileNotFoundError

    monkeypatch.setattr(pantalla, "_grim", missing)

    with pytest.raises(pantalla.PantallaError, match="grim"):
        pantalla.capture()


def test_a_grim_failure_is_a_plain_message_without_system_details(monkeypatch, tmp_path):
    FakeGrim(monkeypatch, tmp_path, code=1, content=None, stderr="failed to create display: /run/user/1000/secret/path")

    with pytest.raises(pantalla.PantallaError) as error:
        pantalla.capture()

    assert "No pude capturar la pantalla" in str(error.value) and "secret" not in str(error.value)


def test_an_empty_capture_is_an_error(monkeypatch, tmp_path):
    FakeGrim(monkeypatch, tmp_path, content=b"")

    with pytest.raises(pantalla.PantallaError):
        pantalla.capture()


def test_a_capture_that_is_absurdly_big_is_refused(monkeypatch, tmp_path):
    FakeGrim(monkeypatch, tmp_path, content=b"x" * (pantalla.MAX_BYTES + 1))

    with pytest.raises(pantalla.PantallaError, match="demasiado"):
        pantalla.capture()


def test_without_a_runtime_dir_it_refuses_instead_of_falling_back_to_tmp(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    with pytest.raises(pantalla.PantallaError, match="privada"):
        pantalla.capture()


def test_the_wayland_socket_is_found_when_the_service_did_not_inherit_the_variable(monkeypatch, tmp_path):
    grim = FakeGrim(monkeypatch, tmp_path)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    (grim.runtime / "wayland-1.lock").write_text("")
    (grim.runtime / "wayland-1").write_text("")

    pantalla.capture()

    assert grim.calls[0][1]["WAYLAND_DISPLAY"] == "wayland-1"


def test_grim_gets_a_minimal_environment(grim, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyD-ejemplo_de_clave_larga_QzXw")

    pantalla.capture()

    env = grim.calls[0][1]
    assert "GEMINI_API_KEY" not in env and env["XDG_RUNTIME_DIR"] == str(grim.runtime)


def test_the_real_grim_point_reports_the_exit_code_and_stderr(monkeypatch):
    class Done:
        returncode = 3
        stderr = "boom\n"

    monkeypatch.setattr(pantalla.subprocess, "run", lambda *a, **k: Done())

    assert REAL_GRIM(["grim", "x.jpg"], {}) == (3, "boom")


def test_a_hanging_grim_is_a_clear_error(monkeypatch, tmp_path):
    FakeGrim(monkeypatch, tmp_path)

    def hang(argv, env):
        raise pantalla.subprocess.TimeoutExpired("grim", 10)

    monkeypatch.setattr(pantalla, "_grim", hang)

    with pytest.raises(pantalla.PantallaError, match="tardó"):
        pantalla.capture()
