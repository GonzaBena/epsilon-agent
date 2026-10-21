import subprocess
from types import SimpleNamespace

import pytest

from epsilon.utils import sistema

MEMINFO = "MemTotal:       16384000 kB\nMemFree:         1000 kB\nMemAvailable:    8192000 kB\nSwapTotal: 2048000 kB\nSwapFree: 1024000 kB\n"
SERVICE = "ActiveState=active\nSubState=running\nActiveEnterTimestamp=Wed 2026-10-21 20:15:37 -03\n"


@pytest.fixture
def proc(tmp_path, monkeypatch):
    files = {"MEMINFO": MEMINFO, "UPTIME": "273600.55 1000.0\n", "LOADAVG": "0.52 0.40 0.31 1/500 12345\n"}
    for name, text in files.items():
        path = tmp_path / name.lower()
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(sistema, name, path)
    monkeypatch.setattr(sistema, "_systemctl", lambda: SERVICE)
    return tmp_path


@pytest.mark.parametrize("count,text", [(0, "0 B"), (1023, "1023 B"), (1536, "1,5 KB"), (5 * 1024**3, "5,0 GB"), (3 * 1024**4, "3,0 TB")])
def test_sizes_use_a_decimal_comma(count, text):
    assert sistema._size(count) == text


@pytest.mark.parametrize("seconds,text", [(30, "0 min"), (90, "1 min"), (3 * 3600 + 120, "3 h 2 min"), (3 * 86400 + 4 * 3600, "3 d 4 h 0 min")])
def test_durations(seconds, text):
    assert sistema._duration(seconds) == text


def test_ram_reads_available_memory_and_swap(proc):
    assert sistema.ram() == [("Memoria", "7,8 GB disponibles de 15,6 GB"), ("Swap", "1000,0 MB libres de 2,0 GB")]


def test_ram_without_swap_has_no_swap_row(proc, monkeypatch):
    (proc / "meminfo").write_text("MemTotal: 1024 kB\nMemAvailable: 512 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n", encoding="utf-8")

    assert [name for name, _ in sistema.ram()] == ["Memoria"]


def test_uptime_and_load(proc):
    assert sistema.uptime() == [("Encendido hace", "3 d 4 h 0 min"), ("Carga (1, 5, 15 min)", "0.52 · 0.40 · 0.31")]


def test_disk_shows_free_space_and_the_size_of_the_data(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"x" * 2048)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_bytes(b"x" * 1024)

    rows = dict(sistema.disk(tmp_path))

    assert "libres de" in rows["Disco"] and "% usado" in rows["Disco"]
    assert rows["Datos de Epsilon"] == "3,0 KB"


def test_symlinks_are_not_followed_when_measuring(tmp_path):
    big = tmp_path.parent / "afuera.bin"
    big.write_bytes(b"x" * 100_000)
    (tmp_path / "enlace").symlink_to(big)

    assert dict(sistema.disk(tmp_path))["Datos de Epsilon"].endswith(" B")


def test_a_huge_folder_is_reported_as_more_than(tmp_path, monkeypatch):
    for i in range(5):
        (tmp_path / f"{i}.txt").write_bytes(b"x")
    monkeypatch.setattr(sistema, "MAX_FILES", 3)

    assert dict(sistema.disk(tmp_path))["Datos de Epsilon"].startswith("más de")


def test_service_state(proc):
    assert sistema.service() == [("Servicio epsilon", "active (running) desde Wed 2026-10-21 20:15:37 -03")]


def test_an_inactive_service_has_no_start_time(proc, monkeypatch):
    monkeypatch.setattr(sistema, "_systemctl", lambda: "ActiveState=inactive\nSubState=dead\nActiveEnterTimestamp=\n")

    assert sistema.service() == [("Servicio epsilon", "inactive (dead)")]


@pytest.mark.parametrize("boom", [FileNotFoundError(), subprocess.TimeoutExpired("systemctl", 5), PermissionError()])
def test_missing_or_slow_systemctl_is_just_not_available(proc, monkeypatch, boom):
    def fail():
        raise boom

    monkeypatch.setattr(sistema, "_systemctl", fail)

    assert sistema.service() == [("Servicio epsilon", "no disponible")]


def test_an_empty_answer_from_systemctl_is_not_available(proc, monkeypatch):
    monkeypatch.setattr(sistema, "_systemctl", lambda: "")

    assert sistema.service() == [("Servicio epsilon", "no disponible")]


def test_missing_proc_files_do_not_break_the_report(proc, tmp_path, monkeypatch):
    for name in ("MEMINFO", "UPTIME", "LOADAVG"):
        monkeypatch.setattr(sistema, name, tmp_path / "no-existe")

    text = sistema.report("", tmp_path)

    assert "| Memoria | no disponible |" in text and "| Encendido hace | no disponible |" in text


def test_report_is_a_markdown_table_with_everything_by_default(proc, tmp_path):
    text = sistema.report("", tmp_path)

    assert text.startswith("**Sistema**\n\n| Dato | Valor |\n|---|---|\n")
    for label in ("Disco", "Datos de Epsilon", "Memoria", "Encendido hace", "Servicio epsilon"):
        assert f"| {label} |" in text


def test_report_can_pick_one_item(proc, tmp_path):
    text = sistema.report("RAM", tmp_path)

    assert "| Memoria |" in text and "| Disco |" not in text and "Servicio" not in text


def test_an_unknown_option_is_refused_and_nothing_runs(proc, tmp_path, monkeypatch):
    monkeypatch.setattr(sistema, "_systemctl", lambda: pytest.fail("no debería ejecutarse"))

    with pytest.raises(ValueError, match="Opciones: disco, ram, uptime, servicio"):
        sistema.report("rm -rf /", tmp_path)


def test_systemctl_runs_with_fixed_arguments_no_shell_and_a_minimal_environment(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return SimpleNamespace(stdout="ActiveState=active\n")

    monkeypatch.setattr(sistema.subprocess, "run", fake_run)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyD-ejemplo_de_clave_larga_QzXw")
    monkeypatch.setenv("PATH", "/usr/bin")

    assert REAL_SYSTEMCTL() == "ActiveState=active\n"
    assert seen["argv"] == ["systemctl", "--user", "show", "epsilon", "-p", "ActiveState,SubState,ActiveEnterTimestamp"]
    assert "shell" not in seen and seen["timeout"] == sistema.TIMEOUT
    assert "GEMINI_API_KEY" not in seen["env"] and seen["env"]["PATH"] == "/usr/bin"


REAL_SYSTEMCTL = sistema._systemctl  # el conftest lo bloquea en cada test; esta es la verdadera
