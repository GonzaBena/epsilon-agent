from types import SimpleNamespace

from epsilon.ai import llm

# conftest reemplaza llm.generate por una función que falla. Guardamos acá la
# real (importada antes de que corra el fixture) para probarla con un cliente falso.
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


# --- llm.check: validar la clave y el modelo sin gastar tokens ---------------

import pytest
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
