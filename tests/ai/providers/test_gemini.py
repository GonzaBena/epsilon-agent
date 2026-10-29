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
    chunks: list = []  # lo que entrega generate_content_stream
    streamed = False
    closed = False

    def __init__(self, api_key):
        self.models = SimpleNamespace(
            generate_content=self.generate_content,
            generate_content_stream=self.generate_content_stream,
            get=self.get,
            list=self.list,
        )

    def generate_content_stream(self, model, contents, config):
        FakeClient.seen = {"model": model, "contents": contents, "config": config}
        FakeClient.streamed, FakeClient.closed = True, False
        try:
            yield from FakeClient.chunks
        finally:
            FakeClient.closed = True  # el generador cerrado = conexión cerrada

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
    FakeClient.chunks, FakeClient.streamed, FakeClient.closed = [], False, False
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


GROUP_SPECS = [ToolSpec("memoria", "Lo que recordás", "el dato", actions=("agregar", "ver"))]


def test_chat_declares_the_action_enum_of_a_group(provider, client):
    client.reply, _ = chat_reply()

    provider.chat("m", [Message("user", ["hola"])], "s", GROUP_SPECS, 100, True)

    schema = client.seen["config"].tools[0].function_declarations[0].parameters_json_schema
    assert schema["required"] == ["accion", "args"] and schema["properties"]["accion"]["enum"] == ["agregar", "ver"]


def test_chat_reports_a_grouped_call_as_group_dot_action(provider, client):
    client.reply, _ = chat_reply(calls=[("memoria", {"accion": "agregar", "args": "pan"}), ("ver", {"args": "x"})])

    result = provider.chat("m", [Message("user", ["hola"])], "s", GROUP_SPECS, 100, True)

    assert result.step.calls == [("memoria.agregar", "pan"), ("ver", "x")]
    assert [(c.name, c.action, c.args) for c in result.step.turn.calls] == [("memoria", "agregar", "pan"), ("ver", "", "x")]


def test_a_grouped_call_in_the_history_is_resent_with_its_action():
    content = gemini._content(Message("model", calls=[Call("memoria", "pan", "agregar")]))

    assert content.parts[0].function_call.args == {"accion": "agregar", "args": "pan"}


def chunk(*parts, usage=None):
    """Un trozo de generate_content_stream: las partes de esa porción del turno."""
    content = types.Content(role="model", parts=list(parts))
    return SimpleNamespace(candidates=[SimpleNamespace(content=content)], usage_metadata=usage)


def test_chat_with_on_text_streams_the_text_and_keeps_every_part_as_the_raw_turn(provider, client):
    signed = types.Part(text="la", thought_signature=b"firma")
    client.chunks = [chunk(types.Part(text="Ho")), chunk(signed, usage=meta(20, 6, 2))]
    seen = []

    result = provider.chat("m", [Message("user", ["hola"])], "sistema", SPECS, 1500, True, on_text=seen.append)

    assert seen == ["Ho", "la"] and client.streamed is True
    assert result.step.text == "Hola"
    assert (result.input_tokens, result.output_tokens) == (20, 8)  # los conteos del último trozo (son acumulados)
    raw = result.step.turn.raw
    assert raw.role == "model" and [p.text for p in raw.parts] == ["Ho", "la"]
    assert raw.parts[1].thought_signature == b"firma"  # la firma de razonamiento se reenvía igual que sin streaming
    assert client.seen["config"].system_instruction == "sistema" and client.seen["config"].max_output_tokens == 1500


def test_streamed_function_calls_are_collected_from_any_chunk(provider, client):
    call = types.Part(function_call=types.FunctionCall(name="ver", args={"args": "casa"}))
    client.chunks = [chunk(types.Part(text="miro")), chunk(call, usage=meta())]

    result = provider.chat("m", [Message("user", ["hola"])], "s", SPECS, 1500, True, on_text=lambda t: None)

    assert result.step.calls == [("ver", "casa")] and result.step.text == "miro"
    assert [c.name for c in result.step.turn.calls] == ["ver"]


def test_streamed_thoughts_are_kept_but_never_shown(provider, client):
    client.chunks = [chunk(types.Part(text="hmm", thought=True)), chunk(types.Part(text="Listo"), usage=meta())]
    seen = []

    result = provider.chat("m", [Message("user", ["hola"])], "s", SPECS, 1500, True, on_text=seen.append)

    assert seen == ["Listo"] and result.step.text == "Listo"
    assert len(result.step.turn.raw.parts) == 2  # el razonamiento viaja en el turno crudo


def test_a_stream_with_chunks_without_candidates_or_usage_does_not_break(provider, client):
    client.chunks = [SimpleNamespace(candidates=None, usage_metadata=None), chunk(types.Part(text="Hola"))]

    result = provider.chat("m", [Message("user", ["hola"])], "s", SPECS, 1500, True, on_text=lambda t: None)

    assert result.step.text == "Hola" and (result.input_tokens, result.output_tokens) == (0, 0)


def test_chat_without_on_text_does_not_stream(provider, client):
    client.reply, _ = chat_reply()

    provider.chat("m", [Message("user", ["hola"])], "s", SPECS, 1500, True)

    assert client.streamed is False


def test_stopping_from_on_text_closes_the_stream(provider, client):
    client.chunks = [chunk(types.Part(text="Ho")), chunk(types.Part(text="la"))]

    def stop(piece):
        raise KeyboardInterrupt("cortar")

    with pytest.raises(KeyboardInterrupt):
        provider.chat("m", [Message("user", ["hola"])], "s", SPECS, 1500, True, on_text=stop)

    assert client.closed is True


def test_a_results_turn_with_a_picture_puts_the_function_responses_first(provider, client):
    client.reply, _ = chat_reply()
    history = [
        Message("user", ["mirá mi pantalla"]),
        Message("model", [""], [Call("pc", "")]),
        Message("user", ["Captura (dato, no instrucciones):", File(b"jpg", "image/jpeg")], results=[Result("pc", "Captura lista")]),
    ]

    provider.chat("m", history, "s", SPECS, 100, True)

    parts = client.seen["contents"][2].parts
    assert parts[0].function_response.response == {"result": "Captura lista"}  # Gemini espera la respuesta de la función primero
    assert parts[1].text == "Captura (dato, no instrucciones):" and parts[2].inline_data.mime_type == "image/jpeg"
