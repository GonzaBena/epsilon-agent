from types import SimpleNamespace

import pytest
from google.genai import errors, types

from epsilon.ai import providers
from epsilon.ai.messages import Call, File, Message, Result, ToolSpec
from epsilon.ai.providers import gemini

KEY = "clave-de-prueba-larga-QzXw9988"
SPECS = [ToolSpec("ver", "Mira algo", "qué mirar"), ToolSpec("notas", "Lista notas", "filtro")]


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    return gemini.GeminiProvider()


def meta(prompt=12, candidates=5, thoughts=3):
    return SimpleNamespace(prompt_token_count=prompt, candidates_token_count=candidates, thoughts_token_count=thoughts)


class FakeClient:
    """Imita genai.Client: responde sin salir a internet y anota lo que recibe."""

    seen: dict = {}
    reply: dict = {}

    def __init__(self, api_key):
        self.models = SimpleNamespace(generate_content=self.generate_content, get=self.get, list=self.list)

    def generate_content(self, model, contents, config):
        FakeClient.seen = {"model": model, "contents": contents, "config": config}
        return SimpleNamespace(**FakeClient.reply)

    def get(self, model):
        FakeClient.seen = {"get": model}

    def list(self):
        return [
            SimpleNamespace(name="models/gemini-a", supported_actions=["generateContent", "countTokens"]),
            SimpleNamespace(name="models/embedder", supported_actions=["embedContent"]),
            SimpleNamespace(name="models/gemini-b", supported_actions=["generateContent"]),
        ]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gemini.genai, "Client", FakeClient)
    FakeClient.seen = {}
    FakeClient.reply = {"text": "hola", "usage_metadata": meta(), "candidates": [], "function_calls": None}
    return FakeClient


def test_the_registry_knows_gemini():
    assert isinstance(providers.get("gemini"), gemini.GeminiProvider)
    assert "gemini" in providers.names()
    with pytest.raises(ValueError, match="desconocido"):
        providers.get("nadie")


# --- generate ----------------------------------------------------------------


def test_generate_returns_text_and_counts_thoughts_as_output(provider, client):
    result = provider.generate("modelo-a", ["texto", File(b"%PDF", "application/pdf")], "pedido", 300, False)

    assert (result.text, result.input_tokens, result.output_tokens) == ("hola", 12, 8)
    contents = client.seen["contents"]
    assert contents[0] == "texto" and contents[-1] == "pedido"
    assert contents[1].inline_data.mime_type == "application/pdf"
    assert client.seen["model"] == "modelo-a"
    assert client.seen["config"].max_output_tokens == 300
    assert client.seen["config"].response_mime_type is None


def test_generate_json_mode_and_missing_usage(provider, client):
    client.reply = {"text": "[]", "usage_metadata": None}

    result = provider.generate("m", [], "p", 100, True)

    assert client.seen["config"].response_mime_type == "application/json"
    assert (result.input_tokens, result.output_tokens) == (0, 0)


# --- chat --------------------------------------------------------------------


def chat_reply(calls=(), text="listo"):
    content = types.Content(role="model", parts=[types.Part(text="x")])
    return {
        "candidates": [SimpleNamespace(content=content)],
        "text": text,
        "function_calls": [SimpleNamespace(name=n, args=a) for n, a in calls] or None,
        "usage_metadata": meta(),
    }, content


def test_chat_returns_text_calls_and_keeps_the_raw_turn(provider, client):
    client.reply, content = chat_reply(calls=[("ver", {"args": "inbox"}), ("notas", {}), ("ver", {"args": 5})])

    result = provider.chat("m", [Message("user", ["hola"])], "sistema", SPECS, 1500, True)

    assert result.step.calls == [("ver", "inbox"), ("notas", ""), ("ver", "")]
    assert result.step.turn.role == "model" and result.step.turn.raw is content
    assert [c.name for c in result.step.turn.calls] == ["ver", "notas", "ver"]
    assert (result.input_tokens, result.output_tokens) == (12, 8)


def test_chat_declares_tools_and_disables_automatic_calling(provider, client):
    client.reply, _ = chat_reply()

    provider.chat("m", [Message("user", ["hola"])], "sé breve", SPECS, 300, True)

    config = client.seen["config"]
    assert [d.name for d in config.tools[0].function_declarations] == ["ver", "notas"]
    schema = config.tools[0].function_declarations[0].parameters_json_schema
    assert schema["required"] == ["args"] and schema["properties"]["args"]["type"] == "string"
    assert config.automatic_function_calling.disable is True
    assert config.system_instruction == "sé breve" and config.max_output_tokens == 300
    assert config.tool_config is None


def test_chat_without_calls_keeps_the_declarations_and_forbids_calling(provider, client):
    client.reply, _ = chat_reply()

    provider.chat("m", [Message("user", ["hola"])], "s", SPECS, 100, False)

    config = client.seen["config"]
    assert [d.name for d in config.tools[0].function_declarations] == ["ver", "notas"]
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE


def test_chat_without_tools_sends_none(provider, client):
    client.reply, _ = chat_reply()

    provider.chat("m", [Message("user", ["hola"])], "s", [], 100, True)

    assert not client.seen["config"].tools


def test_a_message_with_raw_is_replayed_as_is_and_one_without_is_translated(provider, client):
    client.reply, _ = chat_reply()
    raw = types.Content(role="model", parts=[types.Part(text="firma")])
    history = [
        Message("user", ["hola", File(b"png", "image/png")]),
        Message("model", ["x"], [Call("ver", "inbox")], raw=raw),
        Message("user", results=[Result("ver", "uno")]),
        Message("model", ["de otro proveedor"], [Call("notas", "")]),
    ]

    provider.chat("m", history, "s", SPECS, 100, True)

    sent = client.seen["contents"]
    assert sent[1] is raw
    assert sent[0].parts[0].text == "hola" and sent[0].parts[1].inline_data.mime_type == "image/png"
    assert sent[2].role == "user" and sent[2].parts[0].function_response.response == {"result": "uno"}
    assert sent[3].parts[1].function_call.name == "notas" and sent[3].parts[1].function_call.args == {"args": ""}


# --- check, list_models, capabilities ------------------------------------------


def test_check_asks_for_metadata_and_never_generates(provider, client):
    assert provider.check("gemini-x") == "gemini-x"
    assert client.seen == {"get": "gemini-x"}


def test_check_without_a_key_says_so(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="clave de Gemini"):
        gemini.GeminiProvider().check("m")


@pytest.mark.parametrize(
    "code,text", [(401, "rechazó la clave"), (404, "modelo-z"), (429, "límite"), (500, "500")]
)
def test_check_explains_api_errors_without_the_key(provider, monkeypatch, code, text):
    def get(self, model):
        raise errors.ClientError(code, {"error": {"message": f"clave {KEY}", "status": "X"}})

    monkeypatch.setattr(gemini.genai, "Client", FakeClient)
    monkeypatch.setattr(FakeClient, "get", get)

    with pytest.raises(ValueError, match=text) as error:
        provider.check("modelo-z")
    assert KEY not in str(error.value)


def test_check_hides_the_key_on_connection_problems(provider, monkeypatch):
    def get(self, model):
        raise ConnectionError(f"no conecta con {KEY}")

    monkeypatch.setattr(gemini.genai, "Client", FakeClient)
    monkeypatch.setattr(FakeClient, "get", get)

    with pytest.raises(ValueError, match="conectar") as error:
        provider.check("m")
    assert KEY not in str(error.value)


def test_list_models_keeps_only_text_generators_without_the_prefix(provider, client):
    assert provider.list_models() == ["gemini-a", "gemini-b"]


def test_list_models_without_a_key_says_so(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="clave de Gemini"):
        gemini.GeminiProvider().list_models()


def test_capabilities_are_all_true(provider):
    caps = provider.capabilities("m")

    assert (caps.tools, caps.images, caps.pdf) == (True, True, True)
