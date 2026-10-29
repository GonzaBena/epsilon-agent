import base64

import httpx
import pytest

from epsilon.ai import providers
from epsilon.ai.messages import Call, File, Message, Result, ToolSpec
from epsilon.ai.providers import ollama

# conftest reemplaza ollama._request por una función que falla; guardamos la real (importada antes) para probarla.
from epsilon.ai.providers.ollama import _request as real_request
from epsilon.ai.providers.ollama import _request_stream as real_request_stream

SPECS = [ToolSpec("ver", "Mira algo", "qué mirar")]


@pytest.fixture
def provider():
    return ollama.OllamaProvider()


@pytest.fixture
def server(monkeypatch):
    """Reemplaza el único punto de red de Ollama: anota los pedidos y contesta lo que se le diga."""

    class Server:
        sent = []
        replies = {}

        @staticmethod
        def request(method, path, body=None, timeout=60.0):
            Server.sent.append((method, path, body))
            reply = Server.replies[(method, path)]
            if isinstance(reply, Exception):
                raise reply
            return reply

    Server.sent, Server.replies = [], {}
    monkeypatch.setattr(ollama, "_request", Server.request)
    return Server


def chat_reply(content="listo", calls=(), prompt=10, evaluated=4):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in calls]
    return {"message": message, "prompt_eval_count": prompt, "eval_count": evaluated}


def test_the_registry_knows_ollama():
    assert isinstance(providers.get("ollama"), ollama.OllamaProvider)
    assert providers.names() == ["gemini", "ollama", "anthropic"]


# --- generate ----------------------------------------------------------------------


def test_generate_builds_the_request_and_returns_text_and_tokens(provider, server):
    server.replies[("POST", "/api/chat")] = chat_reply("hola")

    result = provider.generate("qwen3", ["texto", File(b"png", "image/png")], "pedido", 300, False)

    assert (result.text, result.input_tokens, result.output_tokens) == ("hola", 10, 4)
    method, path, body = server.sent[0]
    assert (method, path) == ("POST", "/api/chat")
    assert body["model"] == "qwen3" and body["stream"] is False
    assert body["options"] == {"num_predict": 300, "temperature": 0, "num_ctx": 8192}
    assert "format" not in body
    [user] = body["messages"]
    assert user["role"] == "user" and user["content"] == "texto\n\npedido"
    assert user["images"] == [base64.b64encode(b"png").decode()]


def test_generate_json_mode_and_missing_counts(provider, server):
    server.replies[("POST", "/api/chat")] = {"message": {"content": "[]"}}

    result = provider.generate("m", [], "p", 100, True)

    assert server.sent[0][2]["format"] == "json"
    assert (result.input_tokens, result.output_tokens) == (0, 0)


def test_a_pdf_is_refused_before_calling_ollama(provider, server):
    with pytest.raises(ValueError, match="no lee PDFs"):
        provider.generate("m", [File(b"%PDF", "application/pdf")], "p", 100, False)
    assert server.sent == []


# --- chat ------------------------------------------------------------------------


def test_chat_returns_text_calls_and_a_neutral_turn(provider, server):
    server.replies[("POST", "/api/chat")] = chat_reply("", calls=[("ver", {"args": "inbox"}), ("ver", {"args": 5}), ("notas", {})])

    result = provider.chat("qwen3", [Message("user", ["hola"])], "sistema", SPECS, 1500, True)

    assert result.step.calls == [("ver", "inbox"), ("ver", ""), ("notas", "")]
    assert result.step.turn.role == "model" and result.step.turn.raw is None
    assert [c.name for c in result.step.turn.calls] == ["ver", "ver", "notas"]
    assert (result.input_tokens, result.output_tokens) == (10, 4)


def test_chat_sends_system_tools_and_translates_the_history(provider, server):
    server.replies[("POST", "/api/chat")] = chat_reply()
    raw_from_gemini = object()
    history = [
        Message("user", ["hola", File(b"png", "image/png")]),
        Message("model", ["miro"], [Call("ver", "inbox")], raw=raw_from_gemini),  # el raw de otro proveedor no se usa
        Message("user", results=[Result("ver", "uno")]),
    ]

    provider.chat("qwen3", history, "sé breve", SPECS, 300, True)

    body = server.sent[0][2]
    assert body["messages"][0] == {"role": "system", "content": "sé breve"}
    assert body["messages"][1]["images"] == [base64.b64encode(b"png").decode()]
    assert body["messages"][2] == {
        "role": "assistant",
        "content": "miro",
        "tool_calls": [{"function": {"name": "ver", "arguments": {"args": "inbox"}}}],
    }
    assert body["messages"][3] == {"role": "tool", "tool_name": "ver", "content": "uno"}
    [tool] = body["tools"]
    assert tool["type"] == "function" and tool["function"]["name"] == "ver"
    assert tool["function"]["parameters"]["required"] == ["args"]
    assert body["options"]["num_predict"] == 300


def test_chat_without_calls_or_tools_sends_no_tools(provider, server):
    server.replies[("POST", "/api/chat")] = chat_reply()

    provider.chat("m", [Message("user", ["hola"])], "s", SPECS, 100, False)
    provider.chat("m", [Message("user", ["hola"])], "s", [], 100, True)

    assert all("tools" not in body for _, _, body in server.sent)


def test_num_ctx_can_be_configured_with_env(provider, server, monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "4096")
    server.replies[("POST", "/api/chat")] = chat_reply()

    provider.chat("qwen3", [Message("user", ["hola"])], "s", [], 100, True)

    assert server.sent[0][2]["options"]["num_ctx"] == 4096


# --- check, list_models, capabilities ------------------------------------------------


def test_check_finds_the_model_in_the_list(provider, server):
    server.replies[("GET", "/api/tags")] = {"models": [{"name": "qwen3:8b"}, {"name": "llama3.1:latest"}]}

    assert provider.check("qwen3:8b") == "qwen3:8b"
    assert provider.check("llama3.1") == "llama3.1"  # sin etiqueta es :latest
    assert provider.check("") == "2 modelos"  # sin modelo: solo comprueba que responde
    assert all(method == "GET" for method, _, _ in server.sent)  # no gasta


def test_check_tells_how_to_download_a_missing_model(provider, server):
    server.replies[("GET", "/api/tags")] = {"models": [{"name": "qwen3:8b"}]}

    with pytest.raises(ValueError, match="ollama pull otro"):
        provider.check("otro")


def test_list_models_returns_the_names(provider, server):
    server.replies[("GET", "/api/tags")] = {"models": [{"name": "a:1"}, {"name": "b:2"}]}

    assert provider.list_models() == ["a:1", "b:2"]


@pytest.mark.parametrize(
    "reported,expected",
    [(["completion", "tools", "vision"], (True, True)), (["completion"], (False, False)), (None, (False, False))],
)
def test_capabilities_come_from_the_model_and_pdf_is_never_supported(provider, server, reported, expected):
    server.replies[("POST", "/api/show")] = {} if reported is None else {"capabilities": reported}

    caps = provider.capabilities("qwen3")

    assert (caps.tools, caps.images) == expected and caps.pdf is False
    assert server.sent[0][2] == {"model": "qwen3"}


# --- _request: errores sin datos sensibles ----------------------------------------------


class FakeResponse:
    def __init__(self, status=200, data=None, text="cuerpo con datos"):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        if self._data is None:
            raise ValueError("no es json")
        return self._data


def test_request_reports_a_server_that_is_off_with_only_host_and_port(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434/secreto?clave=1")

    def boom(*args, **kwargs):
        raise httpx.ConnectError("connection refused http://127.0.0.1:11434/secreto?clave=1")

    monkeypatch.setattr(ollama.httpx, "request", boom)

    with pytest.raises(ValueError, match="127.0.0.1:11434") as error:
        real_request("GET", "/api/tags")
    assert "secreto" not in str(error.value) and "clave" not in str(error.value)


def test_request_explains_http_errors_without_the_body(monkeypatch):
    monkeypatch.setattr(ollama.httpx, "request", lambda *a, **k: FakeResponse(500))
    with pytest.raises(ValueError, match="500") as error:
        real_request("GET", "/api/tags")
    assert "cuerpo con datos" not in str(error.value)

    monkeypatch.setattr(ollama.httpx, "request", lambda *a, **k: FakeResponse(400, {"error": "context size exceeded"}))
    with pytest.raises(ValueError, match="context size exceeded"):
        real_request("POST", "/api/chat", {"model": "qwen3"})

    # El modelo escribió una llamada a herramienta rota: se explica, sin el texto técnico (ni lo que escribió).
    broken = {"error": 'llama-server chat error: Failed to parse input at pos 47: {"name": "leer"}'}
    monkeypatch.setattr(ollama.httpx, "request", lambda *a, **k: FakeResponse(500, broken))
    with pytest.raises(ValueError, match="modelo más grande") as error:
        real_request("POST", "/api/chat", {"model": "qwen3"})
    assert "llama-server" not in str(error.value) and "leer" not in str(error.value)

    monkeypatch.setattr(ollama.httpx, "request", lambda *a, **k: FakeResponse(404))
    with pytest.raises(ValueError, match="ollama pull qwen3"):
        real_request("POST", "/api/chat", {"model": "qwen3"})


def test_request_returns_the_json_and_uses_the_configured_url(monkeypatch):
    seen = {}

    def fake(method, url, json=None, timeout=None):
        seen.update(method=method, url=url, json=json, timeout=timeout)
        return FakeResponse(200, {"ok": True})

    monkeypatch.setenv("OLLAMA_URL", "http://otro:9999/")
    monkeypatch.setattr(ollama.httpx, "request", fake)

    assert real_request("POST", "/api/chat", {"a": 1}, timeout=5) == {"ok": True}
    assert seen == {"method": "POST", "url": "http://otro:9999/api/chat", "json": {"a": 1}, "timeout": 5}


def test_request_without_a_configured_url_uses_the_local_server(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    seen = {}
    monkeypatch.setattr(ollama.httpx, "request", lambda method, url, **k: seen.update(url=url) or FakeResponse(200, {}))

    real_request("GET", "/api/tags")

    assert seen["url"] == "http://127.0.0.1:11434/api/tags"


def test_conftest_blocks_real_requests(provider):
    with pytest.raises(RuntimeError):
        ollama._request("GET", "/api/tags")


GROUP_SPECS = [ToolSpec("memoria", "Lo que recordás", "el dato", actions=("agregar", "ver"))]


def test_chat_declares_the_action_enum_of_a_group(provider, server):
    server.replies[("POST", "/api/chat")] = chat_reply()

    provider.chat("qwen3", [Message("user", ["hola"])], "s", GROUP_SPECS, 100, True)

    params = server.sent[0][2]["tools"][0]["function"]["parameters"]
    assert params["required"] == ["accion", "args"] and params["properties"]["accion"]["enum"] == ["agregar", "ver"]


def test_chat_reports_a_grouped_call_as_group_dot_action(provider, server):
    server.replies[("POST", "/api/chat")] = chat_reply("", calls=[("memoria", {"accion": "agregar", "args": "pan"})])

    result = provider.chat("qwen3", [Message("user", ["hola"])], "s", GROUP_SPECS, 100, True)

    assert result.step.calls == [("memoria.agregar", "pan")]
    assert [(c.name, c.action) for c in result.step.turn.calls] == [("memoria", "agregar")]


def test_a_grouped_call_in_the_history_is_resent_with_its_action():
    [entry] = ollama._messages([Message("model", calls=[Call("memoria", "pan", "agregar")])])

    assert entry["tool_calls"] == [{"function": {"name": "memoria", "arguments": {"accion": "agregar", "args": "pan"}}}]


# --- streaming: la respuesta llega por trozos ------------------------------------------------


@pytest.fixture
def stream_server(monkeypatch):
    """Reemplaza el punto de red del streaming: anota el pedido y entrega los trozos que se le carguen."""

    class StreamServer:
        sent = []
        chunks = []

        @staticmethod
        def request_stream(method, path, body=None, timeout=120.0):
            StreamServer.sent.append((method, path, body))
            yield from StreamServer.chunks

    StreamServer.sent, StreamServer.chunks = [], []
    monkeypatch.setattr(ollama, "_request_stream", StreamServer.request_stream)
    return StreamServer


def piece(content="", **extra):
    return {"message": {"role": "assistant", "content": content}, **extra}


def test_chat_with_on_text_streams_and_returns_the_joined_text(provider, stream_server):
    stream_server.chunks = [piece("Ho"), piece("la"), piece("", done=True, prompt_eval_count=10, eval_count=4)]
    seen = []

    result = provider.chat("qwen3", [Message("user", ["hola"])], "sé breve", SPECS, 300, True, on_text=seen.append)

    assert seen == ["Ho", "la"]
    assert result.step.text == "Hola" and result.step.calls == []
    assert (result.input_tokens, result.output_tokens) == (10, 4)
    assert stream_server.sent[0][2]["stream"] is True


def test_chat_with_on_text_collects_tool_calls_from_any_chunk(provider, stream_server):
    call = {"function": {"name": "ver", "arguments": {"args": "casa"}}}
    stream_server.chunks = [piece("miro"), {"message": {"content": "", "tool_calls": [call]}}, {"done": True}]

    result = provider.chat("qwen3", [Message("user", ["hola"])], "", SPECS, 300, True, on_text=lambda t: None)

    assert result.step.calls == [("ver", "casa")]
    assert result.step.text == "miro"


def test_chat_without_on_text_still_uses_the_plain_request(provider, server):
    server.replies[("POST", "/api/chat")] = chat_reply()

    provider.chat("qwen3", [Message("user", ["hola"])], "", SPECS, 300, True)

    assert server.sent[0][2]["stream"] is False


def test_stream_ignores_thinking_and_chunks_without_message(provider, stream_server):
    stream_server.chunks = [{"message": {"thinking": "hmm"}}, {"done": True}]
    seen = []

    result = provider.chat("qwen3", [Message("user", ["hola"])], "", SPECS, 300, True, on_text=seen.append)

    assert seen == [] and result.step.text == ""


class FakeStream:
    """Lo que devuelve httpx.stream(...): un contexto con una respuesta que entrega líneas."""

    def __init__(self, status=200, lines=(), data=None, fail=None):
        self.status_code, self._lines, self._data, self._fail = status, lines, data, fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        pass

    def json(self):
        if self._data is None:
            raise ValueError("no es json")
        return self._data

    def iter_lines(self):
        yield from self._lines
        if self._fail:
            raise self._fail


def test_request_stream_yields_one_dict_per_line_and_skips_blank_ones(monkeypatch):
    monkeypatch.setattr(ollama.httpx, "stream", lambda *a, **k: FakeStream(lines=['{"a": 1}', "", '{"a": 2}']))

    assert list(real_request_stream("POST", "/api/chat", {"model": "qwen3"})) == [{"a": 1}, {"a": 2}]


def test_request_stream_reports_a_dropped_connection_with_only_host_and_port(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434/secreto?clave=1")
    drop = httpx.ReadError("se cortó http://127.0.0.1:11434/secreto?clave=1")
    monkeypatch.setattr(ollama.httpx, "stream", lambda *a, **k: FakeStream(lines=['{"a": 1}'], fail=drop))

    with pytest.raises(ValueError, match="127.0.0.1:11434") as error:
        list(real_request_stream("POST", "/api/chat", {"model": "qwen3"}))
    assert "secreto" not in str(error.value) and "clave" not in str(error.value)


def test_request_stream_maps_http_errors_like_request(monkeypatch):
    monkeypatch.setattr(ollama.httpx, "stream", lambda *a, **k: FakeStream(status=404))
    with pytest.raises(ValueError, match="ollama pull qwen3"):
        list(real_request_stream("POST", "/api/chat", {"model": "qwen3"}))

    monkeypatch.setattr(ollama.httpx, "stream", lambda *a, **k: FakeStream(status=400, data={"error": "context size exceeded"}))
    with pytest.raises(ValueError, match="context size exceeded"):
        list(real_request_stream("POST", "/api/chat", {"model": "qwen3"}))


def test_an_error_line_in_the_middle_of_the_stream_is_an_error_not_a_partial_answer(provider, stream_server):
    # Ollama ya contestó 200 y empezó a transmitir; si el modelo se cae manda una línea {"error": ...}.
    stream_server.chunks = [piece("Había una"), {"error": "model runner has unexpectedly stopped"}]

    with pytest.raises(ValueError, match="model runner has unexpectedly stopped"):
        provider.chat("qwen3", [Message("user", ["hola"])], "", SPECS, 300, True, on_text=lambda t: None)


def test_a_broken_tool_call_error_in_the_stream_gets_the_same_explanation_as_a_plain_request(provider, stream_server):
    stream_server.chunks = [{"error": 'Failed to parse input at pos 47: {"name": "leer"}'}]

    with pytest.raises(ValueError, match="modelo más grande") as error:
        provider.chat("qwen3", [Message("user", ["hola"])], "", SPECS, 300, True, on_text=lambda t: None)
    assert "leer" not in str(error.value)


def test_a_stream_that_ends_without_done_is_cut_short_not_a_finished_answer(provider, stream_server):
    stream_server.chunks = [piece("Había una")]

    with pytest.raises(ValueError, match="llegó cortada"):
        provider.chat("qwen3", [Message("user", ["hola"])], "", SPECS, 300, True, on_text=lambda t: None)


def test_an_abandoned_stream_closes_the_connection_right_away(provider, monkeypatch):
    closed = []

    def chunks(*args, **kwargs):
        try:
            yield piece("Ho")
            yield piece("la")
        finally:
            closed.append(True)  # acá se cierra el `with httpx.stream`: Ollama deja de generar

    stream = chunks()  # una referencia viva: sin cerrarlo a propósito, el generador quedaría suspendido
    monkeypatch.setattr(ollama, "_request_stream", lambda *args, **kwargs: stream)

    def stop(text):
        raise KeyboardInterrupt("cortar")

    with pytest.raises(KeyboardInterrupt):
        provider.chat("qwen3", [Message("user", ["hola"])], "", SPECS, 300, True, on_text=stop)

    assert closed == [True]
