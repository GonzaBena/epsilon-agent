import json
import os
import re

import pytest

from epsilon.data import conversations, notes

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


# --- Conversaciones de un canal (Telegram) -----------------------------------


def talk(channel_name="telegram", text="hola"):
    """Un mensaje en la conversación abierta del canal; devuelve su id."""
    conversation_id = conversations.for_channel(channel_name)
    conversations.add_message(conversation_id, "user", text)
    return conversation_id


def test_a_channel_conversation_starts_with_a_meta_line_and_keeps_the_same_id():
    first = talk()
    second = talk(text="otra cosa")

    assert first == second
    assert [e["type"] for e in lines(notes.DATA_DIR, first)] == ["meta", "message", "message"]


def test_the_channel_is_known_from_the_file_and_web_is_the_default():
    telegram_id = talk()
    web_id = conversations.new_id()
    conversations.add_message(web_id, "user", "desde la web")

    assert conversations.channel(telegram_id) == "telegram"
    assert conversations.channel(web_id) == "web"
    assert conversations.channel("20261019-153000-ffff") is None


def test_a_conversation_with_an_badly_named_channel_in_its_meta_line_counts_as_web(tmp_path):
    path = folder(tmp_path) / f"{VALID}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type": "meta", "channel": "Otro"}\n{"type": "message", "role": "user", "text": "x"}\n')

    assert conversations.channel(VALID) == "web"


def test_the_telegram_conversation_has_a_fixed_title_unless_renamed():
    conversation_id = talk(text="mi primer mensaje")

    assert conversations.title(conversation_id) == "Telegram"
    conversations.set_title(conversation_id, "Compras")
    assert conversations.title(conversation_id) == "Compras"
    conversations.set_title(conversation_id, "")
    assert conversations.title(conversation_id) == "Telegram"


def test_closing_starts_a_new_conversation_and_hides_the_old_one_from_the_list():
    old = talk(text="viejo")
    assert conversations.close_channel("telegram") is True
    assert conversations.current("telegram") is None
    new = talk(text="nuevo")

    assert new != old
    listed = [c["id"] for c in conversations.list_all()]
    assert new in listed and old not in listed
    assert conversations.count() == 1
    assert conversations.messages(old)[0]["text"] == "viejo"  # sigue en disco, legible por id


def test_closing_when_nothing_is_open_does_nothing():
    assert conversations.close_channel("telegram") is False
    assert conversations.count() == 0


def test_the_list_shows_one_telegram_conversation_next_to_the_web_ones_with_their_channel():
    web_id = conversations.new_id()
    conversations.add_message(web_id, "user", "web")
    telegram_id = talk()
    for n in range(3):  # tres días de Telegram: siguen siendo una sola entrada
        conversations.close_channel("telegram")
        telegram_id = talk(text=f"día {n}")

    listed = {c["id"]: c["channel"] for c in conversations.list_all()}

    assert listed == {web_id: "web", telegram_id: "telegram"}


def test_a_conversation_that_grows_past_the_limit_is_closed_and_the_next_message_starts_another(monkeypatch):
    first = talk()
    monkeypatch.setattr(conversations, "MAX_BYTES", 10)

    assert conversations.current("telegram") is None  # se cerró sola
    second = talk(text="sigue")

    assert second != first and conversations.count() == 1
    assert [e["type"] for e in lines(notes.DATA_DIR, first)][-1] == "closed"


def test_closing_is_not_activity_so_it_does_not_reorder_recent():
    web_id = conversations.new_id()
    conversations.add_message(web_id, "user", "web")
    telegram_id = talk()
    path = folder(notes.DATA_DIR) / f"{telegram_id}.jsonl"
    before = path.stat().st_mtime_ns

    conversations.close_channel("telegram")

    assert path.stat().st_mtime_ns == before


def test_only_known_channels_other_than_web_can_be_opened():
    for bad in ("web", "Otro", "../x", ""):
        with pytest.raises(ValueError):
            conversations.for_channel(bad)
        with pytest.raises(ValueError):
            conversations.current(bad)
        with pytest.raises(ValueError):
            conversations.close_channel(bad)


def test_what_a_channel_conversation_saves_is_still_redacted(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", TOKEN)
    conversation_id = talk(text=f"mi token es {TOKEN}")

    assert TOKEN not in (folder(notes.DATA_DIR) / f"{conversation_id}.jsonl").read_text(encoding="utf-8")


# --- Eliminar ----------------------------------------------------------------


def test_delete_removes_the_file_for_real(tmp_path):
    conversations.add_message(VALID, "user", "hola")

    assert conversations.delete(VALID) is True

    assert not (folder(tmp_path) / f"{VALID}.jsonl").exists()
    assert conversations.messages(VALID) is None
    assert VALID not in [c["id"] for c in conversations.list_all()]


def test_delete_says_false_when_it_does_not_exist():
    assert conversations.delete(VALID) is False


@pytest.mark.parametrize("bad", ["../../etc/passwd", "../x", "", "abc", f"{VALID}\n", f"{VALID}/../x"])
def test_delete_refuses_ids_that_are_not_ours(bad, tmp_path):
    outside = tmp_path / "x.jsonl"
    outside.write_text("no tocar")

    with pytest.raises(ValueError):
        conversations.delete(bad)
    assert outside.read_text() == "no tocar"


def test_delete_only_touches_that_conversation(tmp_path):
    other = "20261019-153001-cd34"
    conversations.add_message(VALID, "user", "una")
    conversations.add_message(other, "user", "otra")

    conversations.delete(VALID)

    assert conversations.messages(other) is not None


def test_delete_does_not_touch_what_the_conversation_saved(tmp_path):
    note = notes.safe_path("inbox.md")
    note.write_text("comprar pan\n")
    conversations.add_message(VALID, "bot", "Guardado", artifacts=[{"kind": "note", "path": "inbox.md"}])

    conversations.delete(VALID)

    assert note.read_text() == "comprar pan\n"


def test_delete_of_a_symlink_removes_only_the_link(tmp_path):
    real = tmp_path / "real.jsonl"
    real.write_text('{"type": "message", "id": "a", "role": "user", "text": "hola"}\n')
    folder(tmp_path).mkdir()
    (folder(tmp_path) / f"{VALID}.jsonl").symlink_to(real)

    assert conversations.delete(VALID) is True

    assert real.exists()  # el archivo al que apuntaba no se tocó
    assert not (folder(tmp_path) / f"{VALID}.jsonl").is_symlink()


def test_the_open_telegram_conversation_can_be_deleted_and_the_next_message_starts_another():
    first = conversations.for_channel("telegram")
    conversations.add_message(first, "user", "hola")

    conversations.delete(first)

    assert conversations.current("telegram") is None
    second = conversations.for_channel("telegram")
    assert second != first and conversations.channel(second) == "telegram"


def test_an_old_button_does_not_bring_a_deleted_conversation_back(tmp_path):
    conversations.add_message(VALID, "bot", "¿Guardo?", actions=[{"id": "guardar", "label": "Guardar"}])
    conversations.delete(VALID)

    conversations.add_action(VALID, "abc12345", "guardar")  # un toque tardío (por ejemplo, desde Telegram)

    assert not (folder(tmp_path) / f"{VALID}.jsonl").exists()


# --- Canales de plugins ------------------------------------------------------


def test_a_plugin_channel_keeps_its_own_conversation():
    conversation_id = conversations.for_channel("discord")
    conversations.add_message(conversation_id, "user", "hola")

    assert conversations.channel(conversation_id) == "discord"
    assert conversations.current("discord") == conversation_id
    assert conversations.for_channel("discord") == conversation_id  # la misma, no otra


def test_a_plugin_channel_conversation_is_listed_with_a_fixed_title():
    conversation_id = conversations.for_channel("discord")
    conversations.add_message(conversation_id, "user", "hola")

    entry = conversations.list_all()[0]
    assert entry["title"] == "Discord" and entry["channel"] == "discord"


def test_closing_a_plugin_channel_conversation_starts_a_new_one_next_time():
    first = conversations.for_channel("discord")
    conversations.add_message(first, "user", "hola")

    assert conversations.close_channel("discord") is True
    assert conversations.current("discord") is None
    assert conversations.for_channel("discord") != first


@pytest.mark.parametrize("name", ["web", "Web", "../x", "", "a", "x" * 21, "con espacio"])
def test_invalid_channel_names_are_rejected(name):
    for function in (conversations.for_channel, conversations.current, conversations.close_channel):
        with pytest.raises(ValueError):
            function(name)


def test_a_file_with_an_invalid_channel_in_its_meta_reads_as_web(tmp_path):
    path = folder(tmp_path)
    path.mkdir()
    (path / f"{VALID}.jsonl").write_text('{"type": "meta", "channel": "../x"}\n')

    assert conversations.channel(VALID) == "web"


# --- Modo IA: modo y límite de costo -----------------------------------------------


def test_start_ai_writes_meta_and_limit_once(data_dir):
    conversations.start(VALID, "ai", 0.5)
    conversations.start(VALID, "ai", 9)  # ya existe: no hace nada

    assert lines(data_dir, VALID) == [
        {"type": "meta", "channel": "web", "mode": "ai", "ts": lines(data_dir, VALID)[0]["ts"]},
        {"type": "limit", "usd": 0.5, "ts": lines(data_dir, VALID)[1]["ts"]},
    ]


def test_start_normal_creates_nothing(data_dir):
    conversations.start(VALID, "normal", 0.5)

    assert not (folder(data_dir) / f"{VALID}.jsonl").exists()


def test_start_does_not_touch_existing(data_dir):
    conversations.add_message(VALID, "user", "hola")
    conversations.start(VALID, "ai", 0.5)

    assert conversations.mode(VALID) == "normal"
    assert len(lines(data_dir, VALID)) == 1


def test_start_rejects_unknown_mode():
    with pytest.raises(ValueError):
        conversations.start(VALID, "magico", 0.5)


def test_mode_defaults_to_normal_for_old_telegram_and_missing():
    other = "20261019-153001-cd34"
    conversations.add_message(other, "user", "viejo")  # sin línea meta
    telegram = conversations.for_channel("telegram")
    conversations.add_message(telegram, "user", "hola")

    assert conversations.mode(other) == "normal"
    assert conversations.mode(telegram) == "normal"
    assert conversations.mode(VALID) == "normal"  # no existe


def test_mode_ai_only_for_web_and_telegram(data_dir):
    path = folder(data_dir) / f"{VALID}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type": "meta", "channel": "telegram", "mode": "ai"}\n', encoding="utf-8")
    assert conversations.mode(VALID) == "ai"

    path.write_text('{"type": "meta", "channel": "matrix", "mode": "ai"}\n', encoding="utf-8")
    assert conversations.mode(VALID) == "normal"  # un plugin no puede ser de modo IA


def test_start_channel_opens_an_ai_conversation_for_telegram():
    cid = conversations.start_channel("telegram", "ai", 0.5)

    assert conversations.mode(cid) == "ai"
    assert conversations.channel(cid) == "telegram"
    assert conversations.limit(cid) == 0.5
    assert conversations.current("telegram") == cid


def test_start_channel_closes_the_one_that_was_open():
    old = conversations.for_channel("telegram")
    conversations.add_message(old, "user", "hola")

    new = conversations.start_channel("telegram", "ai")

    assert new != old
    assert conversations.current("telegram") == new
    assert conversations.mode(old) == "normal"  # lo anterior queda guardado, intacto
    assert conversations.messages(old)[0]["text"] == "hola"


def test_start_channel_without_limit_writes_no_limit_line():
    cid = conversations.start_channel("telegram", "ai")

    assert conversations.limit(cid) is None


def test_start_channel_normal_mode_is_a_plain_channel_conversation():
    cid = conversations.start_channel("telegram", "normal")

    assert conversations.mode(cid) == "normal"
    assert conversations.current("telegram") == cid


def test_start_channel_rejects_web_unknown_names_modes_and_plugin_ai():
    with pytest.raises(ValueError):
        conversations.start_channel("web", "ai")
    with pytest.raises(ValueError):
        conversations.start_channel("Mal Nombre", "ai")
    with pytest.raises(ValueError):
        conversations.start_channel("telegram", "magico")
    with pytest.raises(ValueError):
        conversations.start_channel("matrix", "ai")  # solo Telegram y la web


def test_start_channel_rejects_a_bad_limit_before_closing_anything():
    old = conversations.for_channel("telegram")
    conversations.add_message(old, "user", "hola")

    with pytest.raises(ValueError):
        conversations.start_channel("telegram", "ai", 0)

    assert conversations.current("telegram") == old


def test_limit_last_wins_and_none_without_line():
    conversations.add_message(VALID, "user", "hola")
    assert conversations.limit(VALID) is None

    conversations.start("20261019-153002-ef56", "ai", 0.5)
    conversations.set_limit("20261019-153002-ef56", 2)

    assert conversations.limit("20261019-153002-ef56") == 2


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), 1001, "abc", True])
def test_set_limit_rejects_bad_values(bad):
    conversations.start(VALID, "ai", 0.5)

    with pytest.raises(ValueError):
        conversations.set_limit(VALID, bad)


def test_set_limit_missing_conversation():
    with pytest.raises(FileNotFoundError):
        conversations.set_limit(VALID, 1)


def test_set_limit_keeps_modification_time(data_dir):
    conversations.start(VALID, "ai", 0.5)
    path = folder(data_dir) / f"{VALID}.jsonl"
    os.utime(path, (1_000_000, 1_000_000))

    conversations.set_limit(VALID, 1)

    assert path.stat().st_mtime == 1_000_000


def test_add_message_ai_flag_roundtrip():
    conversations.add_message(VALID, "user", "hola")
    conversations.add_message(VALID, "bot", "buenas", ai=True)
    conversations.add_message(VALID, "bot", "otra")

    assert [m["ai"] for m in conversations.messages(VALID)] == [False, True, False]


def test_list_all_has_mode():
    conversations.start(VALID, "ai", 0.5)
    conversations.add_message(VALID, "user", "hola")
    conversations.add_message("20261019-153003-0a0a", "user", "normal")

    modes = {c["id"]: c["mode"] for c in conversations.list_all()}

    assert modes == {VALID: "ai", "20261019-153003-0a0a": "normal"}


def test_ai_conversation_title_is_first_user_message():
    conversations.start(VALID, "ai", 0.5)
    conversations.add_message(VALID, "user", "explicame esto")

    assert conversations.title(VALID) == "explicame esto"


def test_set_limit_rejects_a_number_too_big_for_a_float():
    conversations.start(VALID, "ai", 0.5)

    with pytest.raises(ValueError):
        conversations.set_limit(VALID, 10**400)


def test_simultaneous_starts_write_one_meta_and_one_limit(data_dir, monkeypatch):
    import threading
    import time

    real_append = conversations._append

    def slow_append(*args, **kwargs):
        time.sleep(0.05)  # deja que los otros hilos lleguen al chequeo «¿ya existe?»
        return real_append(*args, **kwargs)

    monkeypatch.setattr(conversations, "_append", slow_append)
    threads = [threading.Thread(target=conversations.start, args=(VALID, "ai", 0.5)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    written = [l["type"] for l in lines(data_dir, VALID)]
    assert written == ["meta", "limit"]


# --- Anclar -------------------------------------------------------------------


def test_pinning_is_the_last_pin_line_and_does_not_reorder_the_list(tmp_path):
    make(tmp_path, "20261019-100000-aaaa", "vieja", 1_000)
    make(tmp_path, "20261019-110000-bbbb", "nueva", 2_000)

    assert conversations.set_pinned("20261019-100000-aaaa", True) is True
    listed = {c["title"]: c["pinned"] for c in conversations.list_all()}
    assert listed == {"vieja": True, "nueva": False}
    assert [c["title"] for c in conversations.list_all()] == ["nueva", "vieja"]  # anclar no la hace «reciente»

    assert conversations.set_pinned("20261019-100000-aaaa", False) is False
    assert not any(c["pinned"] for c in conversations.list_all())


def test_the_limit_cuts_only_the_recents_and_never_a_pinned_one(tmp_path):
    for i in range(5):
        make(tmp_path, f"20261019-10000{i}-aaaa", f"c{i}", 1_000 + i)
    conversations.set_pinned("20261019-100000-aaaa", True)  # la más vieja

    titles = [c["title"] for c in conversations.list_all(limit=2)]

    assert titles == ["c4", "c3", "c0"]
    assert conversations.count() == 5


def test_pinning_a_missing_or_channel_conversation_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        conversations.set_pinned(VALID, True)
    cid = conversations.for_channel("telegram")
    conversations.add_message(cid, "user", "hola")
    with pytest.raises(ValueError):
        conversations.set_pinned(cid, True)
