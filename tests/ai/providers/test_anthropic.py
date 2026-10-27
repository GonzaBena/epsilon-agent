from types import SimpleNamespace

import anthropic as sdk
import httpx
import pytest

from epsilon.ai import providers
from epsilon.ai.messages import Call, File, Message, Result, ToolSpec
from epsilon.ai.providers import anthropic

KEY = "clave-de-prueba-larga-QzXw9988"
SPECS = [ToolSpec("ver", "Mira algo", "qué mirar"), ToolSpec("notas", "Lista notas", "filtro", ("listar", "leer"))]


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    return anthropic.AnthropicProvider()


def usage(i=12, o=5, read=0, write=0):
    return SimpleNamespace(
        input_tokens=i, output_tokens=o, cache_read_input_tokens=read, cache_creation_input_tokens=write
    )


def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(id, name, input):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


class FakeClient:
    """Imita anthropic.Anthropic: responde sin salir a internet y anota lo que recibe."""

    seen: dict = {}
    reply = None
    error: Exception | None = None

    def __init__(self, api_key, timeout=None):
        self.messages = SimpleNamespace(create=self.create)
        self.models = SimpleNamespace(retrieve=self.retrieve, list=self.list)

    def create(self, **kwargs):
        FakeClient.seen = kwargs
        if FakeClient.error:
            raise FakeClient.error
        return FakeClient.reply

    def retrieve(self, model):
        FakeClient.seen = {"retrieve": model}
        if FakeClient.error:
            raise FakeClient.error

    def list(self, limit=None):
        return [SimpleNamespace(id="claude-a"), SimpleNamespace(id="claude-b")]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(anthropic.anthropic, "Anthropic", FakeClient)
    FakeClient.seen = {}
    FakeClient.error = None
    FakeClient.reply = SimpleNamespace(content=[text("hola")], usage=usage())
    return FakeClient


def status_error(cls, code):
    response = httpx.Response(code, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(f"secreto {KEY}", response=response, body=None)


def test_the_registry_knows_anthropic():
    assert isinstance(providers.get("anthropic"), anthropic.AnthropicProvider)
    assert providers.names()[-1] == "anthropic"


def test_without_a_key_it_says_so(monkeypatch, client):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="clave de Anthropic"):
        anthropic.AnthropicProvider().generate("m", [], "hola", 10, False)


def test_generate_sends_files_and_prompt_and_counts_cache_as_input(provider, client):
    client.reply = SimpleNamespace(content=[text("ho"), text("la")], usage=usage(10, 4, read=5, write=1))
    pdf, png = File(b"%PDF", "application/pdf"), File(b"\x89PNG", "image/png")
    result = provider.generate("claude-x", ["texto", png, pdf], "¿qué es?", 99, False)
    assert (result.text, result.input_tokens, result.output_tokens) == ("hola", 16, 4)
    content = client.seen["messages"][0]["content"]
    assert [b["type"] for b in content] == ["text", "image", "document", "text"]
    assert content[-1]["text"] == "¿qué es?"
    assert client.seen["max_tokens"] == 99 and client.seen["model"] == "claude-x"
    assert "temperature" not in client.seen and "thinking" not in client.seen


def test_json_mode_asks_for_json_in_the_prompt(provider, client):
    provider.generate("m", [], "dame datos", 10, True)
    assert "JSON" in client.seen["messages"][0]["content"][-1]["text"]


def test_chat_reads_text_and_tool_calls(provider, client):
    client.reply = SimpleNamespace(
        content=[text("miro"), tool_use("toolu_9", "notas", {"accion": "leer", "args": "inbox"})], usage=usage()
    )
    history = [Message("user", ["¿qué hay?"])]
    result = provider.chat("m", history, "sistema", SPECS, 50, True)
    assert result.step.text == "miro"
    assert result.step.calls == [("notas.leer", "inbox")]
    assert client.seen["system"] == "sistema"
    assert client.seen["tools"][1]["input_schema"]["properties"]["accion"]["enum"] == ["listar", "leer"]
    assert "tool_choice" not in client.seen


def test_chat_without_calls_allowed_sends_tool_choice_none(provider, client):
    provider.chat("m", [Message("user", ["hola"])], "s", SPECS, 50, False)
    assert client.seen["tool_choice"] == {"type": "none"}


def test_tool_results_use_the_id_of_the_call_they_answer(provider, client):
    client.reply = SimpleNamespace(
        content=[tool_use("toolu_A", "ver", {"args": "casa"}), tool_use("toolu_B", "ver", {"args": "auto"})],
        usage=usage(),
    )
    first = provider.chat("m", [Message("user", ["mirá"])], "s", SPECS, 50, True).step.turn
    history = [Message("user", ["mirá"]), first, Message("user", results=[Result("ver", "casa ok"), Result("ver", "auto ok")])]
    provider.chat("m", history, "s", SPECS, 50, True)
    messages = client.seen["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert [b["id"] for b in messages[1]["content"]] == ["toolu_A", "toolu_B"]
    assert [(b["tool_use_id"], b["content"]) for b in messages[2]["content"]] == [
        ("toolu_A", "casa ok"),
        ("toolu_B", "auto ok"),
    ]


def test_history_from_another_provider_gets_matching_synthetic_ids(provider, client):
    history = [
        Message("user", ["hola"]),
        Message("model", ["miro"], [Call("notas", "inbox", "leer")]),
        Message("user", results=[Result("notas.leer", "contenido")]),
    ]
    provider.chat("m", history, "s", SPECS, 50, True)
    messages = client.seen["messages"]
    use = messages[1]["content"][-1]
    assert use["type"] == "tool_use" and use["input"] == {"accion": "leer", "args": "inbox"}
    assert messages[2]["content"][0]["tool_use_id"] == use["id"]


def test_errors_are_explained_without_the_key(provider, client):
    cases = [
        (sdk.AuthenticationError, 401, "rechazó la clave"),
        (sdk.NotFoundError, 404, "no existe"),
        (sdk.RateLimitError, 429, "límite de uso"),
        (sdk.InternalServerError, 500, "código 500"),
    ]
    for cls, code, expected in cases:
        client.error = status_error(cls, code)
        with pytest.raises(ValueError, match=expected) as raised:
            provider.chat("m", [Message("user", ["hola"])], "s", [], 50, True)
        assert KEY not in str(raised.value)


def test_check_and_list_models_do_not_generate(provider, client):
    assert provider.check("claude-x") == "claude-x" and client.seen == {"retrieve": "claude-x"}
    assert provider.check("") == "2 modelos"
    assert provider.list_models() == ["claude-a", "claude-b"]


def test_capabilities(provider):
    caps = provider.capabilities("claude-x")
    assert (caps.tools, caps.images, caps.pdf, caps.thinking) == (True, True, True, False)
