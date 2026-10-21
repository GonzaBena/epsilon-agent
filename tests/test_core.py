from datetime import date

from epsilon import core
from epsilon.ai import usage
from epsilon.data import notes


def use_tmp_data(tmp_path, monkeypatch):
    # Todo va a una carpeta temporal, nunca a tus datos reales.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)


def say(text: str) -> core.Reply:
    return core.handle(core.Message(text=text))


def test_plus_saves_dated_line_in_inbox(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    reply = say("+ comprar filamento")

    assert reply.text == "Guardado en inbox.md"
    saved = (tmp_path / "inbox.md").read_text(encoding="utf-8")
    assert saved == f"- {date.today().isoformat()} comprar filamento\n"


def test_plus_keeps_a_multiline_or_markdown_note_out_of_a_list_item(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    say("+ # Título\nlínea dos")

    saved = (tmp_path / "inbox.md").read_text(encoding="utf-8")
    assert saved == f"## {date.today().isoformat()}\n\n# Título\nlínea dos\n\n"
    assert not saved.startswith("- ")


def test_plus_without_text_asks_for_it(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    reply = say("+")

    assert "Escribí algo" in reply.text
    assert not (tmp_path / "inbox.md").exists()


def test_ver_reads_inbox_by_default(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    say("+ hola")

    assert "hola" in say("/ver").text


def test_ver_reads_named_note(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    notes.save_note("casa", "pintar pared")

    assert "pintar pared" in say("/ver casa").text


def test_ver_rejects_path_outside_data_dir(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    reply = say("/ver ../../etc/passwd")

    assert reply.text == "Nombre de nota no válido"


def test_ver_empty_note_says_so(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    (tmp_path / "vacia.md").write_text("", encoding="utf-8")

    assert say("/ver vacia").text == "(vacía)"


def test_buscar_finds_lines(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    notes.save_note("casa", "comprar PETG negro")

    assert "casa:1: comprar PETG negro" in say("/buscar petg").text


def test_buscar_without_word_shows_usage(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    assert say("/buscar").text == "Uso: /buscar palabra"


def test_archivos_lists_saved_files(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    notes.save_note("casa", "algo")

    assert "- casa.md" in say("/archivos").text


def test_archivos_without_matches(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    assert say("/archivos").text == "No encontré archivos."


def test_archivos_mentions_how_many_more(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    for i in range(12):
        notes.save_note(f"nota{i}", "x")

    text = say("/archivos").text

    assert "(y 2 más; filtrá con /archivos texto)" in text


def test_uso_shows_today_and_total(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    monkeypatch.setattr(usage, "PRICES", {"m": (1.0, 1.0)})
    usage.record("m", 10, 5)

    text = say("/uso").text

    assert "Hoy: 1 llamada(s)" in text and "Total" in text


def test_usage_alias_works(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    assert "Hoy" in say("/usage").text


def test_help_aliases_return_help():
    for command in ("/ayuda", "/start"):
        assert say(command).text == core.HELP


def test_unknown_text_returns_help():
    assert say("hola, qué tal").text == core.HELP


def test_commands_ignore_case_in_command_name(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    assert say("/AYUDA").text == core.HELP


def test_command_with_bot_suffix_still_works(tmp_path, monkeypatch):
    # Telegram en grupos manda "/ver@mi_bot"; el núcleo no debería romperse.
    use_tmp_data(tmp_path, monkeypatch)

    assert say("/ayuda@epsilon_bot").text == core.HELP


def test_fecha_and_its_alias_answer_the_time_without_the_model(monkeypatch):
    monkeypatch.setattr(core.clock, "now_text", lambda moment=None: "miércoles 21 de octubre de 2026, 14:32 (UTC-03:00)")

    assert "14:32" in send("/fecha").text
    assert "14:32" in send("/hora").text
