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
    # new_id, el id del mensaje (que también usa token_hex), new_id que choca, y new_id que no.
    suffixes = iter(["aaaa", "01020304", "aaaa", "bbbb"])
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


# --- Ids de mensaje ----------------------------------------------------------


def test_add_message_returns_an_id_that_is_saved_with_it(tmp_path):
    message_id = conversations.add_message(VALID, "bot", "hola")

    assert re.fullmatch(r"[0-9a-f]{8}", message_id)
    assert lines(tmp_path, VALID)[0]["id"] == message_id


def test_message_ids_are_unique_within_a_conversation():
    ids = {conversations.add_message(VALID, "user", str(i)) for i in range(300)}

    assert len(ids) == 300


# --- Leer, resolviendo qué botones se usaron ---------------------------------


def test_messages_of_a_missing_conversation_is_none():
    assert conversations.messages(VALID) is None


def test_messages_come_back_in_order_with_their_fields():
    first = conversations.add_message(VALID, "user", "hola")
    second = conversations.add_message(VALID, "bot", "No pude", error=True)

    result = conversations.messages(VALID)

    assert [(m["id"], m["role"], m["text"], m["error"]) for m in result] == [
        (first, "user", "hola", False),
        (second, "bot", "No pude", True),
    ]
    assert all("ts" in m for m in result)


def test_a_message_without_buttons_says_so():
    conversations.add_message(VALID, "bot", "sin botones")

    message = conversations.messages(VALID)[0]

    assert message["actions"] == [] and message["used"] is False and message["chosen"] is None


def test_a_message_with_buttons_lists_them_until_one_is_pressed():
    actions = [{"id": "guardar", "label": "Guardar"}, {"id": "cancelar", "label": "Cancelar"}]
    conversations.add_message(VALID, "bot", "1 fila", actions=actions)

    message = conversations.messages(VALID)[0]

    assert message["actions"] == actions
    assert message["used"] is False and message["chosen"] is None


def test_a_pressed_button_marks_its_message_with_the_chosen_one():
    actions = [{"id": "guardar", "label": "Guardar"}, {"id": "cancelar", "label": "Cancelar"}]
    message_id = conversations.add_message(VALID, "bot", "1 fila", actions=actions)

    conversations.add_action(VALID, message_id, "cancelar")
    message = conversations.messages(VALID)[0]

    assert message["used"] is True and message["chosen"] == "cancelar"
    assert message["actions"] == actions  # los botones siguen ahí: se muestran apagados


def test_action_lines_are_not_returned_as_messages():
    message_id = conversations.add_message(VALID, "bot", "x", actions=[{"id": "nada:1", "label": "Nada más"}])
    conversations.add_action(VALID, message_id, "nada:1")

    assert len(conversations.messages(VALID)) == 1


def test_the_action_resolves_the_exact_message_even_when_ids_repeat():
    # Tras reiniciar, el contador de archivos vuelve a empezar y los ids de botón se repiten:
    # por eso la pulsación apunta al mensaje, no solo al botón.
    pair = [{"id": "resumir:1", "label": "Resumir"}, {"id": "nada:1", "label": "Nada más"}]
    old = conversations.add_message(VALID, "bot", "viejo", actions=pair)
    new = conversations.add_message(VALID, "bot", "nuevo", actions=pair)

    conversations.add_action(VALID, old, "nada:1")

    result = {m["id"]: m for m in conversations.messages(VALID)}
    assert (result[old]["used"], result[old]["chosen"]) == (True, "nada:1")
    assert (result[new]["used"], result[new]["chosen"]) == (False, None)

    conversations.add_action(VALID, new, "resumir:1")

    result = {m["id"]: m for m in conversations.messages(VALID)}
    assert (result[new]["used"], result[new]["chosen"]) == (True, "resumir:1")
    assert result[old]["chosen"] == "nada:1"  # el primero no cambió


def test_the_first_press_wins():
    actions = [{"id": "guardar", "label": "Guardar"}, {"id": "cancelar", "label": "Cancelar"}]
    message_id = conversations.add_message(VALID, "bot", "1 fila", actions=actions)

    conversations.add_action(VALID, message_id, "guardar")
    conversations.add_action(VALID, message_id, "cancelar")

    assert conversations.messages(VALID)[0]["chosen"] == "guardar"


def test_a_button_the_message_did_not_offer_is_ignored():
    message_id = conversations.add_message(VALID, "bot", "x", actions=[{"id": "guardar", "label": "Guardar"}])

    conversations.add_action(VALID, message_id, "inventado")

    assert conversations.messages(VALID)[0]["used"] is False


def test_an_action_for_an_unknown_message_is_ignored():
    conversations.add_message(VALID, "bot", "x", actions=[{"id": "guardar", "label": "Guardar"}])

    conversations.add_action(VALID, "no-existe", "guardar")

    assert conversations.messages(VALID)[0]["used"] is False


def test_an_action_cannot_resolve_a_message_that_has_no_buttons():
    message_id = conversations.add_message(VALID, "user", "hola")

    conversations.add_action(VALID, message_id, "guardar")

    assert conversations.messages(VALID)[0]["used"] is False


def test_malformed_lines_are_skipped_not_fatal(tmp_path):
    folder(tmp_path).mkdir()
    path = folder(tmp_path) / f"{VALID}.jsonl"
    path.write_text(
        '{"type": "message", "id": "aaaaaaaa", "role": "user", "text": "bien", "ts": "x"}\n'
        "esto no es json\n"
        '{"type": "desconocido"}\n'
        '{"type": "message", "id": "bbbbbbbb", "role": "bot", "text": "también bien", "ts": "x"}\n',
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


# --- Título: se puede cambiar (agregando una línea; gana la última) ----------


def test_set_title_appends_a_line_and_keeps_everything_else(tmp_path):
    conversations.add_message(VALID, "user", "mensaje original")
    before = (folder(tmp_path) / f"{VALID}.jsonl").read_text(encoding="utf-8")

    conversations.set_title(VALID, "Compras de la oficina")

    after = (folder(tmp_path) / f"{VALID}.jsonl").read_text(encoding="utf-8")
    assert after.startswith(before)  # nada de lo anterior se tocó
    last = lines(tmp_path, VALID)[-1]
    assert last["type"] == "title" and last["text"] == "Compras de la oficina" and "ts" in last


def test_a_custom_title_replaces_the_automatic_one_in_the_list():
    conversations.add_message(VALID, "user", "+ comprar filamento")

    conversations.set_title(VALID, "Filamento")

    assert conversations.list_all()[0]["title"] == "Filamento"
    assert conversations.title(VALID) == "Filamento"


def test_the_last_title_wins():
    conversations.add_message(VALID, "user", "hola")

    conversations.set_title(VALID, "Uno")
    conversations.set_title(VALID, "Dos")

    assert conversations.title(VALID) == "Dos"


def test_an_empty_title_goes_back_to_the_automatic_one():
    conversations.add_message(VALID, "user", "mi primer mensaje")
    conversations.set_title(VALID, "Nombre propio")

    conversations.set_title(VALID, "   ")

    assert conversations.title(VALID) == "mi primer mensaje"


def test_set_title_returns_the_title_now_in_effect():
    conversations.add_message(VALID, "user", "mi primer mensaje")

    assert conversations.set_title(VALID, "  Nombre   propio ") == "Nombre propio"
    assert conversations.set_title(VALID, "") == "mi primer mensaje"


def test_a_title_is_a_single_clean_line_of_at_most_80_characters():
    conversations.add_message(VALID, "user", "hola")

    title = conversations.set_title(VALID, "línea uno\nlínea dos\t\ttabulada   " + "x" * 200)

    assert "\n" not in title and "\t" not in title and "  " not in title
    assert len(title) == 80


def test_titles_are_redacted_too(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)
    conversations.add_message(VALID, "user", "hola")

    conversations.set_title(VALID, f"bot {TOKEN}")

    raw = (folder(tmp_path) / f"{VALID}.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in raw and "vKjP" not in raw


def test_set_title_on_a_missing_conversation_fails_and_creates_nothing(tmp_path):
    with pytest.raises(FileNotFoundError):
        conversations.set_title(VALID, "x")

    assert not folder(tmp_path).exists()


def test_set_title_checks_the_id():
    with pytest.raises(ValueError):
        conversations.set_title("../escape", "x")


def test_title_of_a_missing_conversation_is_none():
    assert conversations.title(VALID) is None


def test_title_lines_are_not_messages():
    conversations.add_message(VALID, "user", "hola")
    conversations.set_title(VALID, "Otro")

    assert len(conversations.messages(VALID)) == 1


def test_renaming_does_not_reorder_the_list(tmp_path):
    make(tmp_path, "20261019-100000-aaaa", "la vieja", 1_000)
    make(tmp_path, "20261019-110000-bbbb", "la nueva", 2_000)

    conversations.set_title("20261019-100000-aaaa", "Renombrada")

    assert [c["title"] for c in conversations.list_all()] == ["la nueva", "Renombrada"]
    # y la fecha que se muestra sigue siendo la de la última actividad, no la del renombrado
    assert os.stat(folder(tmp_path) / "20261019-100000-aaaa.jsonl").st_mtime == pytest.approx(1_000, abs=1)


def test_a_new_message_does_move_it_to_the_top(tmp_path):
    make(tmp_path, "20261019-100000-aaaa", "la vieja", 1_000)
    make(tmp_path, "20261019-110000-bbbb", "la nueva", 2_000)

    conversations.add_message("20261019-100000-aaaa", "user", "sigo acá")

    assert conversations.list_all()[0]["id"] == "20261019-100000-aaaa"


# --- artifacts: lo que una respuesta guardó ----------------------------------


def test_a_bot_message_keeps_what_it_saved():
    conversations.add_message(VALID, "bot", "Guardado", artifacts=[{"kind": "note", "path": "inbox.md"}])

    [message] = conversations.messages(VALID)
    assert message["artifacts"] == [{"kind": "note", "path": "inbox.md"}]


def test_a_message_without_artifacts_has_an_empty_list(tmp_path):
    conversations.add_message(VALID, "bot", "hola")

    assert conversations.messages(VALID)[0]["artifacts"] == []
    assert "artifacts" not in lines(tmp_path, VALID)[0]  # no se escribe lo que está vacío


def test_artifacts_are_written_on_the_message_line_and_nothing_is_edited(tmp_path):
    conversations.add_message(VALID, "bot", "a", artifacts=[{"kind": "csv", "path": "gastos.csv"}])
    conversations.add_message(VALID, "bot", "b", artifacts=[{"kind": "note", "path": "inbox.md"}])

    written = lines(tmp_path, VALID)
    assert [l["artifacts"][0]["path"] for l in written] == ["gastos.csv", "inbox.md"]


def test_unknown_kinds_and_odd_paths_are_not_stored():
    conversations.add_message(
        VALID,
        "bot",
        "x",
        artifacts=[
            {"kind": "virus", "path": "a.exe"},  # tipo que no existe
            {"kind": "note", "path": "../../etc/passwd"},  # se sale de la carpeta
            {"kind": "note", "path": "/etc/passwd"},  # absoluta
            {"kind": "note", "path": "nota\ncon salto.md"},  # salto de línea
            {"kind": "note", "path": ""},
            {"kind": "note", "path": "a\\b.md"},  # barra invertida
            {"kind": "note", "path": "x" * 301},  # demasiado larga
            {"kind": "csv", "path": "bien.csv"},
        ],
    )

    assert conversations.messages(VALID)[0]["artifacts"] == [{"kind": "csv", "path": "bien.csv"}]


def test_a_hand_edited_line_cannot_smuggle_odd_artifacts(tmp_path):
    conversations.add_message(VALID, "user", "hola")
    path = folder(tmp_path) / f"{VALID}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "message", "id": "aaaa1111", "role": "bot", "text": "x",
                            "artifacts": [{"kind": "note", "path": "../fuera.md"}, "no soy un dict", {"kind": "csv", "path": "ok.csv"}]}) + "\n")
        f.write(json.dumps({"type": "message", "id": "bbbb2222", "role": "bot", "text": "y", "artifacts": "texto"}) + "\n")

    messages = conversations.messages(VALID)

    assert messages[1]["artifacts"] == [{"kind": "csv", "path": "ok.csv"}]
    assert messages[2]["artifacts"] == []


def test_artifacts_are_listed_newest_first_without_repeats():
    conversations.add_message(VALID, "bot", "1", artifacts=[{"kind": "note", "path": "inbox.md"}])
    conversations.add_message(VALID, "bot", "2", artifacts=[{"kind": "csv", "path": "gastos.csv"}])
    conversations.add_message(VALID, "bot", "3", artifacts=[{"kind": "note", "path": "inbox.md"}])  # otra vez

    assert conversations.artifacts(VALID) == [
        {"kind": "note", "path": "inbox.md"},
        {"kind": "csv", "path": "gastos.csv"},
    ]


def test_artifacts_of_a_missing_conversation_is_none():
    assert conversations.artifacts(VALID) is None
    with pytest.raises(ValueError):
        conversations.artifacts("../../x")


def test_inside_one_message_the_last_artifact_is_the_newest():
    conversations.add_message(
        VALID, "bot", "x", artifacts=[{"kind": "file", "path": "a.pdf"}, {"kind": "note", "path": "b.md"}]
    )

    assert [a["path"] for a in conversations.artifacts(VALID)] == ["b.md", "a.pdf"]


# --- contar (para «Ver más») -------------------------------------------------


def test_count_is_the_total_even_when_the_list_is_cut(tmp_path):
    for i in range(5):
        make(tmp_path, f"20261019-10000{i}-aaaa", f"c{i}", 1_000 + i)

    assert conversations.count() == 5
    assert len(conversations.list_all(limit=2)) == 2


def test_count_ignores_foreign_files_and_empty_folder(tmp_path):
    assert conversations.count() == 0
    make(tmp_path, VALID, "hola", 1_000)
    (folder(tmp_path) / "notas.txt").write_text("x")
    (folder(tmp_path) / "no-es-un-id.jsonl").write_text("x")

    assert conversations.count() == 1
