import os
import socket
import subprocess
import threading

import pytest

from epsilon.voice import client


def run_script(script, runtime, path_dirs=(), *words):
    env = {"PATH": os.pathsep.join([*map(str, path_dirs), "/usr/bin", "/bin"]), "XDG_RUNTIME_DIR": str(runtime), "HOME": str(runtime)}
    return subprocess.run([str(script), *words], env=env, capture_output=True, text=True, timeout=10)


def test_the_socket_lives_in_the_runtime_dir_of_the_user(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    assert client.socket_path() == tmp_path / client.SOCKET_NAME


def test_without_a_runtime_dir_it_falls_back_to_run_user(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

    assert client.socket_path() == client.Path(f"/run/user/{os.getuid()}") / client.SOCKET_NAME


def test_the_script_is_written_executable_and_only_by_us(tmp_path):
    path = client.write_script(tmp_path)

    assert path == tmp_path / client.SCRIPT_NAME
    assert path.stat().st_mode & 0o777 == 0o755
    assert path.read_text().startswith("#!/bin/sh")


@pytest.fixture
def server(tmp_path):
    """Un servidor de verdad en el socket: guarda lo que le llegó y contesta «ok»."""
    path = tmp_path / client.SOCKET_NAME
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(path))
    listener.listen(1)
    received = []

    def serve():
        conn, _ = listener.accept()
        with conn:
            received.append(conn.recv(100).decode())
            conn.sendall(b"ok\n")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield received, thread
    listener.close()


def test_the_script_sends_toggle_by_default(tmp_path, server):
    received, thread = server
    script = client.write_script(tmp_path / "bin")

    done = run_script(script, tmp_path)
    thread.join(5)

    assert done.returncode == 0
    assert received == ["toggle\n"]


def test_the_script_can_send_another_word(tmp_path, server):
    received, thread = server
    script = client.write_script(tmp_path / "bin")

    run_script(script, tmp_path, (), "status")
    thread.join(5)

    assert received == ["status\n"]


def test_without_epsilon_running_the_script_says_so(tmp_path):
    script = client.write_script(tmp_path / "bin")
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    notify = fake_bin / "notify-send"
    notify.write_text(f'#!/bin/sh\necho "$@" > {tmp_path}/notified\n')
    notify.chmod(0o755)

    done = run_script(script, tmp_path, (fake_bin,))

    assert done.returncode == 1
    assert "no está corriendo" in (tmp_path / "notified").read_text()


def test_the_ask_script_defaults_to_the_ask_word(tmp_path, server):
    received, thread = server
    script = client.write_script(tmp_path / "bin", "ask")

    done = run_script(script, tmp_path)
    thread.join(5)

    assert script.name == client.ASK_SCRIPT_NAME
    assert done.returncode == 0
    assert received == ["ask\n"]


def test_the_toggle_script_is_unchanged_by_the_ask_one(tmp_path):
    assert client.write_script(tmp_path).name == client.SCRIPT_NAME
    assert client.SCRIPT == client.script_text("toggle")
