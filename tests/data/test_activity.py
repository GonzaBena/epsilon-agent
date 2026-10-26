import json

import pytest

from epsilon.data import activity, notes


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    return tmp_path


def lines(data):
    return [json.loads(line) for line in (data / "activity.jsonl").read_text(encoding="utf-8").splitlines()]


def test_record_appends_one_short_redacted_line(data, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-secreta-de-prueba-123456")

    activity.record("ia", "notas_editar", "todo 3\n- [x] pan", "clave-secreta-de-prueba-123456 " + "x" * 500, True, asked=True, conversation="c1")
    activity.record("rutina", "volumen", "20", "Volumen: 20 %", True, routine="Mañana")

    first, second = lines(data)
    assert first["who"] == "ia" and first["tool"] == "notas_editar" and first["asked"] is True and first["conversation"] == "c1"
    assert first["arg"] == "todo 3 - [x] pan"  # una sola línea
    assert "clave-secreta" not in first["result"] and len(first["result"]) <= activity.MAX_TEXT
    assert second["routine"] == "Mañana" and "asked" not in second and "conversation" not in second


def test_record_rejects_an_unknown_author(data):
    with pytest.raises(ValueError):
        activity.record("persona", "recordar", "", "", True)


def test_a_write_failure_never_breaks_the_action(data, monkeypatch):
    def broken():
        raise OSError("disco lleno")

    monkeypatch.setattr(activity, "_path", broken)
    activity.record("ia", "volumen", "20", "ok", True)  # no explota


def test_read_is_newest_first_paged_and_filtered(data):
    for n in range(5):
        activity.record("ia" if n % 2 else "rutina", f"t{n}", "", "", ok=n != 3)
    (data / "activity.jsonl").open("a").write("{roto\n")  # una línea rota se saltea

    page = activity.read(0, 2)
    assert [e["tool"] for e in page["items"]] == ["t4", "t3"] and page["total"] == 5
    assert [e["tool"] for e in activity.read(2, 10)["items"]] == ["t2", "t1", "t0"]
    assert [e["tool"] for e in activity.read(who="ia")["items"]] == ["t3", "t1"]
    assert [e["tool"] for e in activity.read(failed=True)["items"]] == ["t3"]


def test_read_without_a_file_is_empty(data):
    assert activity.read() == {"items": [], "total": 0}
