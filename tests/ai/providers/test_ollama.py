import base64

import httpx
import pytest

from epsilon.ai import providers
from epsilon.ai.messages import Call, File, Message, Result, ToolSpec
from epsilon.ai.providers import ollama

# conftest reemplaza ollama._request por una función que falla; guardamos la real (importada antes) para probarla.
from epsilon.ai.providers.ollama import _request as real_request

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
    assert providers.names() == ["gemini", "ollama"]


# --- generate ----------------------------------------------------------------------


def test_generate_builds_the_request_and_returns_text_and_tokens(provider, server):
    server.replies[("POST", "/api/chat")] = chat_reply("hola")

    result = provider.generate("qwen3", ["texto", File(b"png", "image/png")], "pedido", 300, False)

    assert (result.text, result.input_tokens, result.output_tokens) == ("hola", 10, 4)
    method, path, body = server.sent[0]
    assert (method, path) == ("POST", "/api/chat")
    assert body["model"] == "qwen3" and body["stream"] is False
    assert body["options"] == {"num_predict": 300, "temperature": 0}
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
