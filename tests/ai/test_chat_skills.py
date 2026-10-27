import pytest

from epsilon import core, tools
from epsilon.ai import chat, permissions
from epsilon.data import notes
from epsilon.settings import config
from epsilon.skills import runtime, store

SKILL = "---\nname: {name}\ndescription: {desc}\n---\nHacé {name} así."


def make(name, desc="Hace algo"):
    return store.save(name, SKILL.format(name=name, desc=desc))


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    monkeypatch.setattr(permissions, "_granted", {})


def test_catalog_lists_only_usable_skills_capped():
    make("uno", "Primera")
    make("dos", "x" * 400)
    make("tres")
    store.set_enabled("tres", False)

    lines = runtime.catalog().splitlines()

    assert lines[0] == "- dos: " + "x" * (runtime.MAX_LIST_DESC - 1) + "…"
    assert lines[1] == "- uno: Primera"
    assert len(lines) == 2


def test_catalog_is_capped_in_count():
    for i in range(config.number("SKILLS_MAX_LISTED") + 5):
        make(f"s{i:02d}")

    assert len(runtime.catalog().splitlines()) == config.number("SKILLS_MAX_LISTED")


def test_catalog_skips_changed_on_disk():
    make("uno")
    (notes.DATA_DIR / "skills" / "uno" / "SKILL.md").write_text("cualquier cosa", encoding="utf-8")

    assert runtime.catalog() == ""


def test_system_prompt_includes_skill_list_and_omits_block_when_empty():
    assert "skills.usar" not in chat.system_prompt()

    make("uno", "Primera")
    system = chat.system_prompt()

    assert "skills.usar" in system and "- uno: Primera" in system
    assert "datos, no instrucciones" in system


def test_skill_usar_returns_wrapped_body():
    make("uno")

    reply = core.AI_RUNNERS["skill_usar"]("/Uno ")

    assert "Hacé uno así." in reply.text and "escritas por la persona" in reply.text


def test_skill_usar_unknown_or_unusable_is_not_available():
    make("uno")
    make("dos")
    store.set_enabled("dos", False)

    unknown = core.AI_RUNNERS["skill_usar"]("nada").text
    off = core.AI_RUNNERS["skill_usar"]("dos").text

    assert "No hay una skill" in unknown and "uno" in unknown and "dos" not in unknown
    assert "No hay una skill" in off
    assert "Hacé dos así." not in off


def test_skills_group_is_allowed_by_default_and_offered_only_with_skills():
    tool = tools.route("skills.usar")
    assert permissions.default_state(tool) == "allow"

    assert "skills" not in [spec.name for spec in chat._specs()]
    make("uno")
    assert "skills" in [spec.name for spec in chat._specs()]


def test_ai_has_no_tool_that_writes_skills():
    skill_tools = [t for t in tools.ai_tools() if "skill" in (t.ai_group + tools.ai_id(t))]

    assert [tools.ai_id(t) for t in skill_tools] == ["skill_usar"]
    assert skill_tools[0].ai == "read"
