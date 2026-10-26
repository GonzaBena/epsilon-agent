import pytest

from epsilon.data import notes
from epsilon.skills import runtime, store

SKILL = "---\nname: correo\ndescription: Redacta un correo\n---\nEscribí un borrador breve."


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())


def test_wrap_labels_body_as_person_written_instructions():
    text = runtime.wrap(store.save("correo", SKILL))

    assert "«correo»" in text and "escritas por la persona" in text
    assert "no cambian tus permisos" in text
    assert text.endswith("Escribí un borrador breve.")


def test_compose_adds_the_request():
    text = runtime.compose(store.save("correo", SKILL), "avisar a Marta")

    assert "Escribí un borrador breve." in text and text.endswith("avisar a Marta")


def test_usable_false_when_disabled_or_changed():
    store.save("correo", SKILL)
    assert runtime.usable("correo").name == "correo"

    store.set_enabled("correo", False)
    assert runtime.usable("correo") is None
    store.confirm("correo")
    (notes.DATA_DIR / "skills" / "correo" / "SKILL.md").write_text(SKILL + "x", encoding="utf-8")
    assert runtime.usable("correo") is None


def test_why_not_messages():
    store.save("correo", SKILL)
    store.set_enabled("correo", False)
    assert "apagada" in runtime.why_not("correo")

    store.confirm("correo")
    (notes.DATA_DIR / "skills" / "correo" / "SKILL.md").write_text(SKILL + "x", encoding="utf-8")
    assert "cambió" in runtime.why_not("correo")
    assert "No existe" in runtime.why_not("nada")


def test_is_skill_command():
    store.save("correo", SKILL)

    assert runtime.is_skill_command("correo") is True
    assert runtime.is_skill_command("nada") is False
    assert runtime.is_skill_command("../x") is False
