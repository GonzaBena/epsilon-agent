import pytest

from epsilon.ai import llm
from epsilon.ai.messages import File, Message, Step, ToolSpec
from epsilon.ai.providers.base import ChatResult, GenerateResult, Provider

# conftest reemplaza llm.generate y llm.chat_tools por funciones que fallan. Guardamos acá las reales
# (importadas antes de que corra el fixture) para probarlas con un proveedor falso.
from epsilon.ai.llm import chat_tools as real_chat_tools
from epsilon.ai.llm import generate as real_generate

SPECS = [ToolSpec("ver", "Mira algo", "qué mirar")]


class FakeProvider(Provider):
    """Un proveedor que responde sin salir a ningún lado y anota lo que recibe."""

    def __init__(self, name="gemini"):
        self.name = name
        self.calls = []

    def generate(self, model, parts, prompt, max_tokens, json_mode):
        self.calls.append(("generate", model, parts, prompt, max_tokens, json_mode))
        return GenerateResult("hola", 12, 8)

    def chat(self, model, history, system, tools, max_tokens, allow_calls):
        self.calls.append(("chat", model, history, system, tools, max_tokens, allow_calls))
        return ChatResult(Step("listo", [], Message("model", ["listo"])), 12, 8)

    def check(self, model):
        self.calls.append(("check", model))
        return model


@pytest.fixture(autouse=True)
def both_providers(monkeypatch):
    """Estos tests hablan de Gemini y de Ollama sin necesitar el proveedor real de Ollama."""
    monkeypatch.setattr(llm.providers, "names", lambda: ["gemini", "ollama"])


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    asked = []

    def get(name):
        asked.append(name)
        return provider

    monkeypatch.setattr(llm.providers, "get", get)
    recorded = []
    monkeypatch.setattr(llm.usage, "record", lambda *args: recorded.append(args))
    monkeypatch.setenv("GEMINI_MODEL", "modelo-a")
    provider.asked = asked
    provider.recorded = recorded
    return provider


# --- ids de modelo -------------------------------------------------------------


@pytest.mark.parametrize(
    "model_id,expected",
    [
        ("gemini:gemini-x", ("gemini", "gemini-x")),
        ("modelo-a", ("gemini", "modelo-a")),  # sin proveedor, es de Gemini
        ("ollama:llama3.1:8b", ("ollama", "llama3.1:8b")),  # el nombre puede tener «:»
        ("raro:x", ("gemini", "raro:x")),  # un prefijo que no es un proveedor no se parte
    ],
)
def test_parse_model(model_id, expected):
    assert llm.parse_model(model_id) == expected


def test_model_for_uses_the_gemini_setting_with_its_provider(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "modelo-z")
    assert llm.model_for() == "gemini:modelo-z" == llm.current_model()
    monkeypatch.setenv("GEMINI_MODEL", "")
    assert llm.model_for() == f"gemini:{llm.DEFAULT_MODEL}"
    monkeypatch.delenv("GEMINI_MODEL")
    assert llm.model_for() == f"gemini:{llm.DEFAULT_MODEL}"


def test_task_is_set_only_inside_the_block():
    assert llm.current_task() is None
    with llm.task("summary"):
        assert llm.current_task() == "summary"
    assert llm.current_task() is None


# --- generate y chat_tools ---------------------------------------------------------------


def test_generate_calls_the_provider_of_the_model_and_records_usage_without_the_prefix(fake):
    answer = real_generate(["texto"], "hola?", max_tokens=300, json_mode=True)

    assert answer == "hola"
    assert fake.asked == ["gemini"]
    assert fake.calls == [("generate", "modelo-a", ["texto"], "hola?", 300, True)]
    assert fake.recorded == [("modelo-a", 12, 8)]  # los registros de Gemini no llevan proveedor


def test_generate_goes_to_the_provider_named_in_the_model(fake, monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "ollama:qwen3")

    real_generate([], "hola")

    assert fake.asked == ["ollama"]
    assert fake.calls[0][1] == "qwen3"
    assert fake.recorded == [("ollama:qwen3", 12, 8)]  # los de Ollama sí


def test_chat_tools_passes_everything_and_returns_the_step(fake):
    history = [llm.text_turn("user", "hola")]

    step = real_chat_tools(history, "sistema", SPECS, max_tokens=300, allow_calls=False)

    assert step.text == "listo" and step.calls == []
    assert fake.calls == [("chat", "modelo-a", history, "sistema", SPECS, 300, False)]
    assert fake.recorded == [("modelo-a", 12, 8)]


def test_conftest_blocks_the_facade():
    with pytest.raises(RuntimeError):
        llm.generate([], "x")
    with pytest.raises(RuntimeError):
        llm.chat_tools([], "x", [])


# --- turnos ---------------------------------------------------------------------


def test_turns_are_neutral_messages():
    assert llm.text_turn("model", "hola") == Message("model", ["hola"])
    assert llm.parts_turn("user", ["a", File(b"x", "image/png")]).text == "a"
    turn = llm.results_turn([("ver", "uno"), ("notas", "dos")])
    assert turn.role == "user" and [(r.name, r.result) for r in turn.results] == [("ver", "uno"), ("notas", "dos")]


# --- check ----------------------------------------------------------------------


def test_check_asks_the_provider_with_the_model_name(fake):
    assert llm.check() == "modelo-a"
    assert fake.calls == [("check", "modelo-a")]


def test_check_uses_the_default_model_when_none_is_set(fake, monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    assert llm.check() == llm.DEFAULT_MODEL


# --- qué modelo atiende cada tarea ---------------------------------------------------


@pytest.fixture
def models(monkeypatch):
    for key in ("AI_MODEL", "AI_MODEL_CHAT", "AI_MODEL_ASK", "AI_MODEL_SUMMARY", "AI_MODEL_CSV", "GEMINI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_a_task_setting_wins_over_the_default(models):
    models.setenv("AI_MODEL_CHAT", "ollama:qwen3")
    models.setenv("AI_MODEL", "gemini:x")
    models.setenv("GEMINI_MODEL", "y")

    assert llm.model_for("chat") == "ollama:qwen3"
    assert llm.model_for("ask") == "gemini:x"  # sin modelo propio, el predeterminado
    assert llm.model_for(None) == llm.default_model() == "gemini:x"


def test_the_default_falls_back_to_the_gemini_setting_and_then_to_the_built_in_one(models):
    models.setenv("GEMINI_MODEL", "y")
    assert llm.default_model() == "gemini:y"
    models.setenv("AI_MODEL", "")
    assert llm.default_model() == "gemini:y"
    models.delenv("GEMINI_MODEL")
    assert llm.default_model() == f"gemini:{llm.DEFAULT_MODEL}"


def test_the_task_in_course_picks_its_model(models):
    models.setenv("AI_MODEL_SUMMARY", "ollama:qwen3")
    models.setenv("AI_MODEL", "gemini:x")

    assert llm.model_for() == "gemini:x"
    with llm.task("summary"):
        assert llm.model_for() == llm.current_model() == "ollama:qwen3"
    assert llm.model_for() == "gemini:x"


def test_a_name_without_a_provider_in_a_task_setting_is_gemini(models):
    models.setenv("AI_MODEL_CSV", "modelo-q")

    assert llm.model_for("csv") == "gemini:modelo-q"
