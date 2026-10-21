import pytest

from epsilon.ai import chat, usage
from epsilon.data import conversations, notes
from epsilon.memory import store

CID = "20261020-120000-aaaa"
OTHER = "20261020-120001-bbbb"
KEY = "AIzaSyD-ejemplo_de_clave_larga_QzXw"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    monkeypatch.setenv("GEMINI_MODEL", "modelo-a")
    monkeypatch.setenv("CHAT_LIMIT_USD", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (1.0, 4.0)})  # US$ por 1M tokens


def spend(conversation_id, tokens_in, action="Chat"):
    with usage.conversation(conversation_id), usage.action(action):
        usage.record("modelo-a", tokens_in, 0)


# --- turns: qué ve el modelo -------------------------------------------------------


def test_turns_only_chat_pairs():
    conversations.start(CID, "ai", 1)
    conversations.add_message(CID, "user", "hola")
    conversations.add_message(CID, "bot", "buenas", ai=True)
    conversations.add_message(CID, "user", "/memoria")
    conversations.add_message(CID, "bot", "1. Vivo en Rosario")  # salida de un comando
    conversations.add_message(CID, "user", "foto.jpg")
    conversations.add_message(CID, "bot", "Guardado: foto.jpg")

    assert chat.turns(CID, "¿y ahora?") == [
        ("user", "hola"),
        ("model", "buenas"),
        ("user", "¿y ahora?"),
    ]


def test_turns_do_not_duplicate_trailing_user_message():
    conversations.start(CID, "ai", 1)
    conversations.add_message(CID, "user", "hola")  # la web ya lo guardó, aún sin respuesta

    assert chat.turns(CID, "hola") == [("user", "hola")]


def test_turns_redact_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    conversations.start(CID, "ai", 1)

    (_, text), = chat.turns(CID, f"mi clave es {KEY}")

    assert KEY not in text and "mi clave es" in text


def test_system_includes_memory_as_data():
    store.remember("Vivo en Rosario")

    system = chat.system_prompt()

    assert "Vivo en Rosario" in system
    assert "datos" in system.lower() and "instrucciones" in system.lower()


def test_system_redacts_memory(monkeypatch):
    store.remember("nota")
    (notes.DATA_DIR / "memory.md").write_text(f"- 2026-10-20: clave {KEY}\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", KEY)

    assert KEY not in chat.system_prompt()


# --- status: contexto, costo, límite, bloqueo ----------------------------------------


def test_status_new_conversation_is_zero_even_with_global_spend():
    spend(OTHER, 5_000_000)
    conversations.start(CID, "ai", 0.5)

    status = chat.status(CID)

    assert (status["cost"], status["context"], status["blocked"]) == (0.0, 0, False)
    assert status["limit"] == 0.5


def test_status_blocks_at_limit_and_unblocks_when_raised():
    conversations.start(CID, "ai", 1)
    spend(CID, 1_000_000)  # US$ 1.00

    blocked = chat.status(CID)
    assert blocked["blocked"] and "límite" in blocked["reason"]

    conversations.add_message(CID, "user", "hola")
    conversations.set_limit(CID, 2)
    assert not chat.status(CID)["blocked"]


def test_status_blocks_when_model_has_no_price(monkeypatch):
    conversations.start(CID, "ai", 1)
    monkeypatch.setenv("GEMINI_MODEL", "modelo-sin-precio")

    status = chat.status(CID)

    assert status["blocked"] and "precio" in status["reason"]


def test_status_limit_falls_back_to_setting(monkeypatch):
    monkeypatch.setenv("CHAT_LIMIT_USD", "0.75")
    conversations.add_message(CID, "user", "hola")  # sin línea de límite

    assert chat.status(CID)["limit"] == 0.75


def test_default_limit_without_setting_is_fifty_cents():
    assert chat.default_limit() == 0.5


def test_status_context_is_last_chat_input():
    conversations.start(CID, "ai", 1)
    spend(CID, 100)
    spend(CID, 250)

    assert chat.status(CID)["context"] == 250


# --- answer ---------------------------------------------------------------------------


def test_answer_calls_the_model_with_turns_and_tags_the_spend(monkeypatch):
    seen = {}

    def fake_chat(turns, system, max_tokens=1500):
        seen.update(turns=turns, system=system, max_tokens=max_tokens)
        usage.record("modelo-a", 1000, 10)
        return "respuesta"

    monkeypatch.setattr(chat.llm, "chat", fake_chat)
    conversations.start(CID, "ai", 1)
    conversations.add_message(CID, "user", "hola")

    with usage.conversation(CID):
        assert chat.answer(CID, "hola") == "respuesta"

    assert seen["turns"] == [("user", "hola")]
    assert 0 < seen["max_tokens"] <= 2000
    assert usage.for_conversation(CID)["context"] == 1000  # quedó anotado como «Chat»


@pytest.mark.parametrize("value", ["0", "-1", "5000", "nan", "inf", "abc", "0.001"])
def test_default_limit_ignores_a_hand_edited_value_out_of_range(monkeypatch, value):
    monkeypatch.setenv("CHAT_LIMIT_USD", value)

    assert chat.default_limit() == chat.DEFAULT_LIMIT
