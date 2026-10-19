import json
import os
import re

import pytest

from epsilon import conversations, notes

VALID = "20261019-153000-ab12"
TOKEN = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    # Todo va a una carpeta temporal, nunca a tus datos reales.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path


def folder(tmp_path):
    return tmp_path / "conversations"


def lines(tmp_path, conversation_id):
    path = folder(tmp_path) / f"{conversation_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- Ids ---------------------------------------------------------------------


def test_new_id_has_date_time_and_four_hex_digits():
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{4}", conversations.new_id())


def test_new_id_does_not_create_a_file(tmp_path):
    # El archivo nace con el primer mensaje: así «Nuevo chat» no deja archivos vacíos.
    conversations.new_id()

    assert not folder(tmp_path).exists()


def test_new_id_skips_ids_that_already_exist(tmp_path, monkeypatch):
    suffixes = iter(["aaaa", "aaaa", "bbbb"])
    monkeypatch.setattr(conversations.secrets, "token_hex", lambda n: next(suffixes))
    monkeypatch.setattr(conversations, "_now", lambda: __import__("datetime").datetime(2026, 10, 19, 15, 30, 0))

    first = conversations.new_id()
    conversations.add_message(first, "user", "hola")
    second = conversations.new_id()

    assert first.endswith("aaaa") and second.endswith("bbbb")


@pytest.mark.parametrize("value", [VALID, "20260101-000000-0000", "20991231-235959-ffff"])
def test_valid_ids(value):
    assert conversations.is_valid_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "../etc/passwd",
        "../../x",
        "20261019-153000-ab1",  # corto
        "20261019-153000-ab123",  # largo
        "20261019-153000-AB12",  # mayúsculas
        "20261019-153000-zzzz",  # no es hexadecimal
        "20261019-153000-ab12\n",  # salto de línea al final
        "20261019-153000-ab12/../x",
        "x" * 500,
        None,
        123,
    ],
)
def test_invalid_ids(value):
    assert not conversations.is_valid_id(value)


@pytest.mark.parametrize("bad", ["../escape", "", "20261019-153000-zzzz", "a/b"])
def test_invalid_ids_never_reach_the_disk(tmp_path, bad):
    with pytest.raises(ValueError):
        conversations.add_message(bad, "user", "x")
    with pytest.raises(ValueError):
        conversations.messages(bad)

    assert not folder(tmp_path).exists()
    assert not (tmp_path.parent / "escape.jsonl").exists()


# --- Escribir ----------------------------------------------------------------


def test_first_message_creates_the_file_in_the_conversations_folder(tmp_path):
    conversations.add_message(VALID, "user", "hola")

    assert (folder(tmp_path) / f"{VALID}.jsonl").exists()


def test_each_message_is_one_json_line_with_a_timestamp(tmp_path):
    conversations.add_message(VALID, "user", "guardá esto: ñandú")
    conversations.add_message(VALID, "bot", "Guardado", actions=[{"id": "guardar", "label": "Guardar"}])

    first, second = lines(tmp_path, VALID)
    assert first["type"] == "message" and first["role"] == "user"
    assert first["text"] == "guardá esto: ñandú"  # sin escapar los acentos
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*", first["ts"])
    assert second["actions"] == [{"id": "guardar", "label": "Guardar"}]


def test_only_appends_never_overwrites(tmp_path):
    folder(tmp_path).mkdir()
    path = folder(tmp_path) / f"{VALID}.jsonl"
    path.write_text('{"type": "message", "role": "user", "text": "viejo", "ts": "x"}\n', encoding="utf-8")

    conversations.add_message(VALID, "user", "nuevo")

    rows = lines(tmp_path, VALID)
    assert [r["text"] for r in rows] == ["viejo", "nuevo"]


def test_roles_are_checked():
    with pytest.raises(ValueError, match="role"):
        conversations.add_message(VALID, "admin", "x")


def test_secrets_are_redacted_before_they_reach_the_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)

    conversations.add_message(VALID, "user", f"mi token es {TOKEN}")

    raw = (folder(tmp_path) / f"{VALID}.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in raw and "vKjP" not in raw
    assert "mi token es ***" in raw


def test_huge_texts_are_cut(tmp_path):
    conversations.add_message(VALID, "bot", "x" * 100_000)

    text = lines(tmp_path, VALID)[0]["text"]
    assert len(text) < 21_000 and text.endswith("(recortado)")


# --- Leer, resolviendo qué botones se usaron ---------------------------------


def test_messages_of_a_missing_conversation_is_none():
    assert conversations.messages(VALID) is None


def test_messages_come_back_in_order_with_their_fields():
    conversations.add_message(VALID, "user", "hola")
    conversations.add_message(VALID, "bot", "No pude", error=True)

    result = conversations.messages(VALID)

    assert [(m["role"], m["text"], m["error"]) for m in result] == [("user", "hola", False), ("bot", "No pude", True)]
    assert all("ts" in m for m in result)


def test_a_pressed_button_marks_its_message_as_used():
    actions = [{"id": "guardar", "label": "Guardar"}, {"id": "cancelar", "label": "Cancelar"}]
    conversations.add_message(VALID, "bot", "1 fila", actions=actions)
    assert conversations.messages(VALID)[0]["used"] is False

    conversations.add_action(VALID, "guardar")
    message = conversations.messages(VALID)[0]

    assert message["used"] is True and message["chosen"] == "guardar"


def test_actions_are_not_returned_as_messages():
    conversations.add_message(VALID, "bot", "x", actions=[{"id": "nada:1", "label": "Nada más"}])
    conversations.add_action(VALID, "nada:1")

    assert len(conversations.messages(VALID)) == 1


def test_a_repeated_button_id_resolves_the_latest_unused_message():
    # Tras reiniciar, el contador de archivos vuelve a empezar y los ids se repiten.
    pair = [{"id": "resumir:1", "label": "Resumir"}, {"id": "nada:1", "label": "Nada más"}]
    conversations.add_message(VALID, "bot", "A", actions=pair)
    conversations.add_action(VALID, "resumir:1")
    conversations.add_message(VALID, "bot", "B", actions=pair)  # mismos ids
    assert [m["used"] for m in conversations.messages(VALID)] == [True, False]

    conversations.add_action(VALID, "nada:1")

    result = conversations.messages(VALID)
    assert [(m["used"], m["chosen"]) for m in result] == [(True, "resumir:1"), (True, "nada:1")]


def test_with_two_pending_messages_sharing_a_button_the_newest_is_resolved():
    # El archivo solo anota QUÉ botón se tocó, no en qué mensaje. Lo razonable es
    # que se tocó el más nuevo: el de más abajo, el que se está mirando.
    pair = [{"id": "resumir:1", "label": "Resumir"}, {"id": "nada:1", "label": "Nada más"}]
    conversations.add_message(VALID, "bot", "viejo", actions=pair)
    conversations.add_message(VALID, "bot", "nuevo", actions=pair)

    conversations.add_action(VALID, "resumir:1")

    result = conversations.messages(VALID)
    assert [(m["text"], m["used"]) for m in result] == [("viejo", False), ("nuevo", True)]


def test_an_action_with_no_matching_message_is_ignored():
    conversations.add_message(VALID, "bot", "sin botones")
    conversations.add_action(VALID, "guardar")

    assert conversations.messages(VALID)[0]["used"] is False


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    folder(tmp_path).mkdir()
    path = folder(tmp_path) / f"{VALID}.jsonl"
    path.write_text(
        '{"type": "message", "role": "user", "text": "bien", "ts": "x"}\n'
        "esto no es json\n"
        '{"type": "desconocido"}\n'
        '{"type": "message", "role": "bot", "text": "también bien", "ts": "x"}\n',
        encoding="utf-8",
    )

    assert [m["text"] for m in conversations.messages(VALID)] == ["bien", "también bien"]


# --- Listar ------------------------------------------------------------------


def make(tmp_path, conversation_id, first_user_text, mtime):
    conversations.add_message(conversation_id, "user", first_user_text)
    os.utime(folder(tmp_path) / f"{conversation_id}.jsonl", (mtime, mtime))


def test_list_is_newest_first(tmp_path):
    make(tmp_path, "20261019-100000-aaaa", "la más vieja", 1_000)
    make(tmp_path, "20261019-110000-bbbb", "la del medio", 2_000)
    make(tmp_path, "20261019-120000-cccc", "la más nueva", 3_000)

    assert [c["title"] for c in conversations.list_all()] == ["la más nueva", "la del medio", "la más vieja"]


def test_list_entries_carry_the_id_and_a_date(tmp_path):
    make(tmp_path, VALID, "hola", 1_700_000_000)

    entry = conversations.list_all()[0]

    assert entry["id"] == VALID
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}.*", entry["updated"])


def test_title_is_the_first_line_of_the_first_user_message_cut_to_40(tmp_path):
    conversations.add_message(VALID, "bot", "un saludo del bot que no cuenta")
    conversations.add_message(VALID, "user", "+ comprar filamento PETG negro para la impresora de la oficina\nsegunda línea")

    title = conversations.list_all()[0]["title"]

    assert title == "+ comprar filamento PETG negro para la i"
    assert len(title) == 40


def test_conversation_without_user_messages_gets_a_placeholder_title(tmp_path):
    conversations.add_message(VALID, "bot", "solo el bot")

    assert conversations.list_all()[0]["title"] == "Conversación sin título"


def test_new_ids_without_messages_are_not_listed(tmp_path):
    conversations.new_id()

    assert conversations.list_all() == []


def test_list_ignores_foreign_files_in_the_folder(tmp_path):
    make(tmp_path, VALID, "hola", 1_000)
    (folder(tmp_path) / "notas.txt").write_text("x")
    (folder(tmp_path) / "nombre raro.jsonl").write_text("x")
    (folder(tmp_path) / "20261019-153000-ZZZZ.jsonl").write_text("x")

    assert [c["id"] for c in conversations.list_all()] == [VALID]


def test_list_has_a_limit(tmp_path):
    for i in range(5):
        make(tmp_path, f"20261019-10000{i}-aaaa", f"c{i}", 1_000 + i)

    assert len(conversations.list_all(limit=3)) == 3
    assert conversations.list_all(limit=3)[0]["title"] == "c4"


def test_list_with_no_folder_is_empty():
    assert conversations.list_all() == []


def test_list_survives_a_broken_file(tmp_path):
    make(tmp_path, "20261019-100000-aaaa", "bien", 1_000)
    (folder(tmp_path) / "20261019-110000-bbbb.jsonl").write_bytes(b"\xff\xfe\x00 no es texto")

    titles = {c["id"]: c["title"] for c in conversations.list_all()}

    assert titles["20261019-100000-aaaa"] == "bien"
    assert titles["20261019-110000-bbbb"] == "Conversación sin título"
