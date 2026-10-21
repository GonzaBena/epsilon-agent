import time

import pytest

from epsilon.ai import providers
from epsilon.ai.messages import ModelError
from epsilon.ai.providers.base import Provider
from epsilon.channels import web

KEY = "AIzaSyD-ejemplo_de_clave_larga_QzXw"


class Listing(Provider):
    def __init__(self, name, models=(), error=None, delay=0.0):
        self.name, self.models, self.error, self.delay = name, list(models), error, delay

    def list_models(self):
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.models


@pytest.fixture
def fake_providers(monkeypatch):
    registry = {}
    monkeypatch.setattr(providers, "_PROVIDERS", registry)
    web._models_cache.clear()  # lo recordado en otro test no cuenta
    for key in ("AI_MODEL", "AI_MODEL_CHAT", "AI_MODEL_ASK", "AI_MODEL_SUMMARY", "AI_MODEL_CSV", "GEMINI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    return registry


def test_models_lists_what_each_provider_offers(client, fake_providers):
    fake_providers["gemini"] = Listing("gemini", ["gemini-a", "gemini-b"])
    fake_providers["ollama"] = Listing("ollama", ["qwen3:8b"])

    data = client.get("/api/models").json()

    assert data["models"] == [
        {"id": "gemini:gemini-a", "provider": "gemini"},
        {"id": "gemini:gemini-b", "provider": "gemini"},
        {"id": "ollama:qwen3:8b", "provider": "ollama"},
    ]
    assert data["providers"] == ["gemini", "ollama"] and data["errors"] == {}


def test_a_provider_that_fails_offers_no_models_and_leaves_only_its_reason(client, fake_providers):
    fake_providers["gemini"] = Listing("gemini", ["gemini-a"])
    fake_providers["ollama"] = Listing("ollama", error=ModelError("No pude conectar con Ollama en 127.0.0.1:11434. ¿Está andando?"))

    data = client.get("/api/models").json()

    assert [m["id"] for m in data["models"]] == ["gemini:gemini-a"]
    assert data["providers"] == ["gemini"]  # el caído no ofrece ni «Usar»
    assert data["errors"] == {"ollama": "No pude conectar con Ollama en 127.0.0.1:11434. ¿Está andando?"}


def test_a_provider_with_no_models_still_counts_as_up(client, fake_providers):
    fake_providers["ollama"] = Listing("ollama", [])

    data = client.get("/api/models").json()

    assert data["models"] == [] and data["providers"] == ["ollama"] and data["errors"] == {}


def test_errors_never_carry_secrets_or_raw_exceptions(client, fake_providers, monkeypatch):
    from epsilon.settings import config

    monkeypatch.setattr(config, "redact", lambda text: text.replace(KEY, "***"))
    fake_providers["gemini"] = Listing("gemini", error=ModelError(f"mala clave {KEY}"))
    fake_providers["ollama"] = Listing("ollama", error=RuntimeError(f"detalle interno {KEY}"))

    data = client.get("/api/models").json()

    assert KEY not in str(data)
    assert data["errors"]["gemini"] == "mala clave ***"
    assert data["errors"]["ollama"] == "No pude consultar los modelos de ollama."


def test_a_slow_provider_does_not_hang_the_answer(client, fake_providers, monkeypatch):
    monkeypatch.setattr(web, "MODELS_TIMEOUT", 0.2)
    fake_providers["gemini"] = Listing("gemini", ["gemini-a"])
    fake_providers["ollama"] = Listing("ollama", ["qwen3"], delay=1.5)

    started = time.monotonic()
    data = client.get("/api/models").json()

    assert time.monotonic() - started < 1.0
    assert [m["id"] for m in data["models"]] == ["gemini:gemini-a"]
    assert "ollama" in data["errors"]


def test_models_reports_the_default_and_which_tasks_have_their_own(client, fake_providers, monkeypatch):
    fake_providers["gemini"] = Listing("gemini", ["gemini-a"])
    monkeypatch.setenv("AI_MODEL", "gemini:gemini-a")
    monkeypatch.setenv("AI_MODEL_CHAT", "ollama:qwen3")
    monkeypatch.setenv("AI_MODEL_CSV", "")  # vacío = sin modelo propio

    data = client.get("/api/models").json()

    assert data["default"] == "gemini:gemini-a" and data["overrides"] == ["chat"]


def test_models_is_protected_like_the_rest(client):
    assert client.get("/api/models", headers={"Host": "evil.com"}).status_code == 403


def test_config_state_includes_the_current_default_model(client, monkeypatch):
    monkeypatch.setenv("AI_MODEL", "ollama:qwen3")

    assert client.get("/api/config").json()["model"] == "ollama:qwen3"


# --- caché de la lista de modelos -----------------------------------------------------


class Counting(Listing):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.asked = 0

    def list_models(self):
        self.asked += 1
        return super().list_models()


@pytest.fixture
def cached(fake_providers, monkeypatch):
    web._models_cache.clear()
    monkeypatch.setattr(web, "MODELS_TTL", {"gemini": 60.0})  # el remoto se recuerda; el local (ollama) no
    fake_providers["gemini"] = Counting("gemini", ["gemini-a"])
    fake_providers["ollama"] = Counting("ollama", ["qwen3"])
    yield fake_providers
    web._models_cache.clear()


def test_a_remote_list_is_remembered_and_a_local_one_is_always_fresh(client, cached):
    first = client.get("/api/models").json()
    second = client.get("/api/models").json()

    assert first == second and [m["id"] for m in second["models"]] == ["gemini:gemini-a", "ollama:qwen3"]
    assert cached["gemini"].asked == 1  # la segunda vez salió del caché
    assert cached["ollama"].asked == 2  # Ollama es local: si bajaste un modelo, aparece al toque


def test_refresh_asks_again(client, cached):
    client.get("/api/models")
    client.get("/api/models?refresh=1")

    assert cached["gemini"].asked == 2


def test_the_cache_expires(client, cached, monkeypatch):
    monkeypatch.setattr(web, "MODELS_TTL", {"gemini": 0.1})
    client.get("/api/models")
    time.sleep(0.15)
    client.get("/api/models")

    assert cached["gemini"].asked == 2


def test_errors_are_never_remembered(client, cached):
    cached["gemini"].error = ModelError("Falta la clave de Gemini.")
    assert "gemini" in client.get("/api/models").json()["errors"]

    cached["gemini"].error = None
    data = client.get("/api/models").json()

    assert data["errors"] == {} and "gemini:gemini-a" in [m["id"] for m in data["models"]]


def test_saving_the_configuration_forgets_the_remembered_lists(client, cached):
    client.get("/api/models")

    client.put("/api/config", json={"changes": {"OLLAMA_URL": "http://localhost:11434"}, "clear": []})
    client.get("/api/models")

    assert cached["gemini"].asked == 2  # una clave o una dirección nueva pueden cambiar la lista


# --- Favoritos ---------------------------------------------------------------


def test_favorites_are_saved_and_come_back_with_the_models(client, fake_providers):
    fake_providers["gemini"] = Listing("gemini", ["gemini-a"])

    assert client.get("/api/models").json()["favorites"] == []
    reply = client.put("/api/models/favorites", json={"favorites": ["gemini:gemini-a", "ollama:qwen3:8b"]})

    assert reply.json() == {"favorites": ["gemini:gemini-a", "ollama:qwen3:8b"]}
    assert client.get("/api/models").json()["favorites"] == ["gemini:gemini-a", "ollama:qwen3:8b"]


def test_favorites_reject_a_wrong_shape_and_write_nothing(client, fake_providers):
    assert client.put("/api/models/favorites", json={"favorites": ["sin dos puntos"]}).status_code == 422
    assert client.put("/api/models/favorites", json={"favorites": "gemini:a"}).status_code == 422
    assert client.put("/api/models/favorites", json={"favorites": ["gemini:a"] * 101}).status_code == 422
    assert client.get("/api/models").json()["favorites"] == []
