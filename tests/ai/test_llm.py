from types import SimpleNamespace

import pytest

from epsilon.ai import llm

# conftest reemplaza llm.generate por una función que falla. Guardamos acá la
# real (importada antes de que corra el fixture) para probarla con un cliente falso.
from epsilon.ai.llm import ToolSpec
from epsilon.ai.llm import chat_tools as real_chat_tools
from epsilon.ai.llm import generate as real_generate


class FakeClient:
    """Imita genai.Client: responde sin salir a internet."""

    def __init__(self, api_key):
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, model, contents, config):
        usage_metadata = SimpleNamespace(
            prompt_token_count=12,
            candidates_token_count=5,
            thoughts_token_count=3,  # el "pensamiento" también se cobra como salida
        )
        return SimpleNamespace(text="hola", usage_metadata=usage_metadata)


def test_generate_records_usage(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setenv("GEMINI_MODEL", "modelo-a")
    monkeypatch.setattr(llm.genai, "Client", FakeClient)
    recorded = []
    monkeypatch.setattr(llm.usage, "record", lambda *args: recorded.append(args))

    answer = real_generate([], "hola?")

    assert answer == "hola"
    assert recorded == [("modelo-a", 12, 8)]  # salida = 5 de respuesta + 3 de pensamiento


def test_generate_records_zero_when_usage_is_missing(monkeypatch):
    class NoUsageClient(FakeClient):
        def generate_content(self, model, contents, config):
            return SimpleNamespace(text="hola", usage_metadata=None)

    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setattr(llm.genai, "Client", NoUsageClient)
    recorded = []
    monkeypatch.setattr(llm.usage, "record", lambda *args: recorded.append(args))

    assert real_generate([], "hola?") == "hola"
    assert recorded[0][1:] == (0, 0)


# --- llm.chat_tools: conversación con herramientas ----------------------------


def tools_client(calls=(), text="listo", seen=None):
    """Cliente falso que contesta con `calls` (lista de (nombre, args)) y anota lo que recibe."""

    class ToolsClient(FakeClient):
        def generate_content(self, model, contents, config):
            if seen is not None:
                seen["contents"] = contents
                seen["config"] = config
            meta = SimpleNamespace(
                prompt_token_count=12, candidates_token_count=5, thoughts_token_count=3
            )
            turn = SimpleNamespace(role="model", parts=[])
            function_calls = [SimpleNamespace(name=n, args=a) for n, a in calls]
            return SimpleNamespace(
                candidates=[SimpleNamespace(content=turn)],
                text=text,
                function_calls=function_calls or None,
                usage_metadata=meta,
            )

    return ToolsClient


SPECS = [ToolSpec("ver", "Mira algo", "qué mirar"), ToolSpec("notas", "Lista notas", "filtro")]


def test_chat_tools_returns_text_and_records_usage(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setenv("GEMINI_MODEL", "modelo-a")
    monkeypatch.setattr(llm.genai, "Client", tools_client())
    recorded = []
    monkeypatch.setattr(llm.usage, "record", lambda *args: recorded.append(args))

    step = real_chat_tools([llm.text_turn("user", "hola")], "sistema", SPECS)

    assert step.text == "listo"
    assert step.calls == []
    assert step.turn.role == "model"
    assert recorded == [("modelo-a", 12, 8)]


def test_chat_tools_returns_calls_with_their_argument(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setattr(
        llm.genai, "Client", tools_client(calls=[("ver", {"args": "inbox"}), ("notas", {}), ("ver", {"args": 5})])
    )
    monkeypatch.setattr(llm.usage, "record", lambda *args: None)

    step = real_chat_tools([llm.text_turn("user", "hola")], "sistema", SPECS)

    assert step.calls == [("ver", "inbox"), ("notas", ""), ("ver", "")]


def test_chat_tools_declares_tools_and_disables_automatic_calling(monkeypatch):
    seen = {}
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setattr(llm.genai, "Client", tools_client(seen=seen))
    monkeypatch.setattr(llm.usage, "record", lambda *args: None)

    real_chat_tools([llm.text_turn("user", "hola")], "sé breve", SPECS, max_tokens=300)

    config = seen["config"]
    declarations = config.tools[0].function_declarations
    assert [d.name for d in declarations] == ["ver", "notas"]
    assert declarations[0].description == "Mira algo"
    schema = declarations[0].parameters_json_schema
    assert schema["type"] == "object"
    assert schema["required"] == ["args"]
    assert schema["properties"]["args"]["type"] == "string"
    assert config.automatic_function_calling.disable is True
    assert config.system_instruction == "sé breve"
    assert config.max_output_tokens == 300


def test_chat_tools_by_default_lets_the_model_call(monkeypatch):
    seen = {}
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setattr(llm.genai, "Client", tools_client(seen=seen))
    monkeypatch.setattr(llm.usage, "record", lambda *args: None)

    real_chat_tools([llm.text_turn("user", "hola")], "sistema", SPECS)

    assert seen["config"].tool_config is None


def test_chat_tools_without_calls_keeps_the_declarations_and_forbids_calling(monkeypatch):
    seen = {}
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setattr(llm.genai, "Client", tools_client(seen=seen))
    monkeypatch.setattr(llm.usage, "record", lambda *args: None)

    real_chat_tools([llm.text_turn("user", "hola")], "sistema", SPECS, allow_calls=False)

    config = seen["config"]
    assert [d.name for d in config.tools[0].function_declarations] == ["ver", "notas"]
    assert config.tool_config.function_calling_config.mode == llm.types.FunctionCallingConfigMode.NONE


def test_chat_tools_without_tools_sends_none(monkeypatch):
    seen = {}
    monkeypatch.setenv("GEMINI_API_KEY", "clave-falsa")
    monkeypatch.setattr(llm.genai, "Client", tools_client(seen=seen))
    monkeypatch.setattr(llm.usage, "record", lambda *args: None)

    real_chat_tools([llm.text_turn("user", "hola")], "sistema", [])

    assert not seen["config"].tools


def test_results_turn_is_a_user_turn_with_one_response_per_result():
    turn = llm.results_turn([("ver", "uno"), ("notas", "dos")])

    assert turn.role == "user"
    assert [p.function_response.name for p in turn.parts] == ["ver", "notas"]
    assert turn.parts[0].function_response.response == {"result": "uno"}


def test_text_turn_has_role_and_text():
    turn = llm.text_turn("model", "hola")
    assert (turn.role, turn.parts[0].text) == ("model", "hola")


def test_conftest_blocks_chat_tools():
    with pytest.raises(RuntimeError):
        llm.chat_tools([], "x", [])


def test_current_model_uses_env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "modelo-z")
    assert llm.current_model() == "modelo-z"
    monkeypatch.setenv("GEMINI_MODEL", "")
    assert llm.current_model() == llm.DEFAULT_MODEL


# --- llm.check: validar la clave y el modelo sin gastar tokens ---------------

from google.genai import errors


def api_error(code, message="x"):
    return errors.ClientError(code, {"error": {"message": message, "status": "X"}})


def checking(monkeypatch, get, key="clave-de-prueba-larga-123456"):
    """Prepara llm.check con un cliente falso cuyo models.get es `get`."""
    monkeypatch.setenv("GEMINI_API_KEY", key)
    created = []

    def fake_client(api_key):
        created.append(api_key)
        return SimpleNamespace(models=SimpleNamespace(get=get))

    monkeypatch.setattr(llm.genai, "Client", fake_client)
    return created


def test_check_asks_for_model_metadata_and_never_generates(monkeypatch):
    asked = []
    # El cliente falso NO tiene generate_content: si check generara, fallaría.
    checking(monkeypatch, lambda model: asked.append(model))
    monkeypatch.setenv("GEMINI_MODEL", "gemini-x")

    assert llm.check() == "gemini-x"
    assert asked == ["gemini-x"]


def test_check_uses_the_default_model_when_none_is_set(monkeypatch):
    asked = []
    checking(monkeypatch, lambda model: asked.append(model))
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    assert llm.check() == llm.DEFAULT_MODEL == asked[0]


def test_check_without_a_key_says_so(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="clave de Gemini"):
        llm.check()


@pytest.mark.parametrize("code", [400, 401, 403])
def test_check_explains_a_rejected_key(monkeypatch, code):
    def get(model):
        raise api_error(code, "API key not valid. Please pass a valid API key.")

    checking(monkeypatch, get)

    with pytest.raises(ValueError, match="rechazó la clave"):
        llm.check()


def test_check_explains_an_unknown_model(monkeypatch):
    def get(model):
        raise api_error(404)

    checking(monkeypatch, get)
    monkeypatch.setenv("GEMINI_MODEL", "modelo-que-no-existe")

    with pytest.raises(ValueError, match="modelo-que-no-existe"):
        llm.check()


def test_check_explains_the_rate_limit(monkeypatch):
    def get(model):
        raise api_error(429)

    checking(monkeypatch, get)

    with pytest.raises(ValueError, match="límite"):
        llm.check()


def test_check_reports_other_api_errors_with_their_code(monkeypatch):
    def get(model):
        raise api_error(500)

    checking(monkeypatch, get)

    with pytest.raises(ValueError, match="500"):
        llm.check()


def test_check_explains_connection_problems_without_leaking_the_key(monkeypatch):
    key = "clave-de-prueba-larga-QzXw9988"

    def get(model):
        raise ConnectionError(f"no conecta con la clave {key}")

    checking(monkeypatch, get, key=key)

    with pytest.raises(ValueError) as error:
        llm.check()

    assert "conectar" in str(error.value)
    assert key not in str(error.value)


def test_generate_falls_back_to_the_default_model_when_the_setting_is_empty(monkeypatch):
    # La configuración "borra" un ajuste dejándolo vacío, no inexistente.
    asked = []

    class Spy(FakeClient):
        def generate_content(self, model, contents, config):
            asked.append(model)
            return super().generate_content(model, contents, config)

    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba-larga-123456")
    monkeypatch.setenv("GEMINI_MODEL", "")
    monkeypatch.setattr(llm.genai, "Client", Spy)
    monkeypatch.setattr(llm.usage, "record", lambda *a: None)

    real_generate([], "hola")

    assert asked == [llm.DEFAULT_MODEL]


def test_check_keeps_the_client_alive_during_the_request(monkeypatch):
    # El cliente real cierra su conexión si se libera antes de terminar
    # ("client has been closed"). Este falso reproduce eso: models solo guarda
    # una referencia débil, como la librería.
    import weakref

    class FragileClient:
        def __init__(self, api_key):
            ref = weakref.ref(self)

            def get(model):
                if ref() is None:
                    raise RuntimeError("Cannot send a request, as the client has been closed.")

            self.models = SimpleNamespace(get=get)

    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-prueba-larga-123456")
    monkeypatch.setattr(llm.genai, "Client", FragileClient)

    assert llm.check() == llm.DEFAULT_MODEL


def test_check_names_the_type_of_unexpected_errors(monkeypatch):
    def get(model):
        raise ConnectionError("lo que sea")

    checking(monkeypatch, get)

    with pytest.raises(ValueError, match="ConnectionError"):
        llm.check()
