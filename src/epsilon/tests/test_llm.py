from types import SimpleNamespace

from epsilon import llm

# conftest reemplaza llm.generate por una función que falla. Guardamos acá la
# real (importada antes de que corra el fixture) para probarla con un cliente falso.
from epsilon.llm import generate as real_generate


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
