import pytest

from epsilon.data import notes
from epsilon.skills import factory, store


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())


@pytest.mark.parametrize("name", ["informe", "correo", "prioridades"])
def test_each_seed_parses_and_passes_check_name(name):
    parsed_name, description, body = store.parse(factory.SEEDS[name])

    assert parsed_name == name and description and len(body) > 100
    store.check_name(name)
    assert len(body) <= store.MAX_BODY


def test_seeds_are_text_only_and_never_ask_for_tools():
    for text in factory.SEEDS.values():
        assert "herramienta" not in text.lower().split("---", 2)[2] or "no uses herramientas" in text.lower()


def test_seed_copies_three_skills_once():
    assert sorted(factory.seed()) == ["correo", "informe", "prioridades"]
    assert sorted(s.name for s in store.list_all()) == ["correo", "informe", "prioridades"]
    assert factory.seed() == []


def test_deleted_seed_does_not_come_back():
    factory.seed()
    store.delete("correo")

    assert factory.seed() == []
    assert store.get("correo") is None


def test_seed_does_not_overwrite_existing_skill():
    mine = "---\nname: informe\ndescription: El mío\n---\nA mi manera, siempre."
    store.save("informe", mine)

    assert sorted(factory.seed()) == ["correo", "prioridades"]
    assert store.get("informe").body == "A mi manera, siempre."


def test_seeded_skills_are_usable_not_changed():
    factory.seed()

    assert all(s.usable and not s.changed for s in store.list_all())


def test_seed_failure_does_not_break_startup(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("sin permiso")

    monkeypatch.setattr(store, "save", boom)

    assert factory.seed() == []
