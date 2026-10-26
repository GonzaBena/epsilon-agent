from datetime import date, datetime

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


def test_removed_commands_are_unknown(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    for command in ("/usage", "/fecha", "/hora", "/dolar", "/clima", "/ventanas", "/sonando", "/contexto", "/todo x", "/cancelar", "/olvidar 1", "/editar 1 x"):
        assert say(command).text == core.HELP, command


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

    assert "14:32" in core.AI_RUNNERS["fecha"]("").text


def test_dolar_answers_without_the_model(monkeypatch):
    monkeypatch.setattr(core.dolar, "text", lambda: "Dólar hoy: Blue $1.500")

    assert core.AI_RUNNERS["dolar"]("").text == "Dólar hoy: Blue $1.500"


def test_dolar_failure_is_a_plain_message(monkeypatch):
    def fail():
        raise ValueError("No pude consultar las cotizaciones del dólar.")

    monkeypatch.setattr(core.dolar, "text", fail)

    assert "No pude consultar" in core.AI_RUNNERS["dolar"]("").text


def test_clima_uses_the_given_city_or_the_configured_one(monkeypatch):
    seen = []
    monkeypatch.setattr(core.clima, "text", lambda city: seen.append(city) or f"clima de {city}")
    monkeypatch.setenv("WEATHER_CITY", "Rosario")

    assert core.AI_RUNNERS["clima"]("").text == "clima de Rosario"
    assert core.AI_RUNNERS["clima"]("Salta").text == "clima de Salta"
    assert seen == ["Rosario", "Salta"]


def test_clima_without_any_city_explains_what_to_do(monkeypatch):
    monkeypatch.delenv("WEATHER_CITY", raising=False)

    assert "Configuración" in core.AI_RUNNERS["clima"]("").text


def test_clima_failure_is_a_plain_message(monkeypatch):
    def fail(city):
        raise ValueError("No encontré el clima de «Zzz».")

    monkeypatch.setattr(core.clima, "text", fail)

    assert core.AI_RUNNERS["clima"]("Zzz").text == "No encontré el clima de «Zzz»."


def test_sistema_answers_from_fixed_options_only(monkeypatch):
    seen = []
    monkeypatch.setattr(core.sistema, "report", lambda item, data_dir: seen.append(item) or "tabla")

    assert say("/sistema").text == "tabla" and say("/sistema ram").text == "tabla"
    assert seen == ["", "ram"]


def test_volume_and_music_commands_pass_their_argument_and_answer_as_text(monkeypatch):
    seen = []
    monkeypatch.setattr(core.multimedia, "volume", lambda arg: seen.append(("volumen", arg)) or "Volumen: 40 %")
    monkeypatch.setattr(core.multimedia, "now_playing", lambda: "Sonando: A — B (chromium)")
    monkeypatch.setattr(core.multimedia, "music", lambda arg: seen.append(("musica", arg)) or "Pausé. (chromium)")

    assert say("/volumen 40").text == "Volumen: 40 %"
    sonando_reply = say("/musica")
    assert sonando_reply.text == "Sonando: A — B (chromium)"
    assert [a.id for a in sonando_reply.actions] == [
        "musica:anterior", "musica:alternar", "musica:siguiente", "volumen:-5", "volumen:+5"
    ]
    assert say("/musica pausa").text == "Pausé. (chromium)"
    assert seen == [("volumen", "40"), ("musica", "pausa")]


def test_multimedia_actions_dispatch_and_return_updated_playing_card(monkeypatch):
    seen = []
    monkeypatch.setattr(core.multimedia, "music", lambda arg: seen.append(("musica", arg)) or "ok")
    monkeypatch.setattr(core.multimedia, "volume", lambda arg: seen.append(("volumen", arg)) or "ok")
    monkeypatch.setattr(core.multimedia, "now_playing", lambda: "Sonando: Track 2 (spotify)")
    monkeypatch.setattr(core.multimedia, "now_playing_info", lambda: {
        "title": "Track 2", "artist": "Artist", "status": "Playing", "player": "spotify", "art_url": ""
    })

    reply = core.handle(core.Message(action="musica:siguiente"))
    assert reply.text == "Sonando: Track 2 (spotify)"
    assert ("musica", "siguiente") in seen
    assert any(a.id == "musica:alternar" and "Pausa" in a.label for a in reply.actions)

    vol_reply = core.handle(core.Message(action="volumen:+5"))
    assert ("volumen", "+5") in seen
    assert vol_reply.text == "Sonando: Track 2 (spotify)"



def test_volume_and_music_errors_come_back_as_text_not_exceptions():
    assert "Escribí un número" in say("/volumen mucho; rm -rf /").text
    assert "Opciones: pausa" in say("/musica apagar").text


def test_the_ai_can_run_the_pc_tools_with_a_copied_command_prefix(monkeypatch):
    seen = []
    monkeypatch.setattr(core.multimedia, "volume", lambda arg: seen.append(arg) or "ok")

    assert core.AI_RUNNERS["volumen"]("/volumen +5").text == "ok" and seen == ["+5"]


def test_windows_command_passes_its_argument_and_errors_come_back_as_text(monkeypatch):
    seen = []
    monkeypatch.setattr(core.ventanas, "report", lambda arg: seen.append(arg) or "tabla")

    assert core.AI_RUNNERS["ventanas"]("activa").text == "tabla" and seen == ["activa"]
    monkeypatch.undo()
    assert "Opciones: activa" in core.AI_RUNNERS["ventanas"]("cerrar todo").text


def routines_clock(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    monkeypatch.setattr(core.clock, "now", lambda: datetime(2026, 10, 22, 14, 30).astimezone())  # jueves


def test_rutina_creates_and_rutinas_lists(tmp_path, monkeypatch):
    routines_clock(tmp_path, monkeypatch)

    assert say("/rutinas").text.startswith("No tenés rutinas")
    created = say("/rutina 23:00 | volumen 20").text
    say("/rutina en 30m | avisar sacar la pizza")
    listing = say("/rutinas").text

    assert created == "Listo, rutina 1: todos los días a las 23:00 → volumen 20. Próxima: jue 22/10 23:00."
    assert "| 1 | todos los días a las 23:00 → volumen 20 | jue 22/10 23:00 |" in listing
    assert "| 2 | una vez, 22/10 a las 15:00 → avisar: sacar la pizza | jue 22/10 15:00 |" in listing


def test_rutina_with_a_condition_is_watched_not_scheduled(tmp_path, monkeypatch):
    routines_clock(tmp_path, monkeypatch)

    created = say("/rutina disco>90 | avisar poco espacio").text
    listing = say("/rutinas").text

    assert created == "Listo, rutina 1: cuando el disco pase del 90 % → avisar: poco espacio. La vigilo: avisa al pasar el umbral y no más de una vez por hora."
    assert "| 1 | cuando el disco pase del 90 % → avisar: poco espacio | vigilando |" in listing
    assert say("/rutina ram>85 | avisar x").text.startswith("Listo, rutina 2: cuando la memoria pase del 85 %")
    assert say("/rutina disco>100 | avisar x").text == "El umbral va de 1 a 99 %."
    say("/rutina pausar 1"), say("/rutina seguir 1")
    assert "vigilando" in say("/rutinas").text


def test_rutina_manages_by_number(tmp_path, monkeypatch):
    routines_clock(tmp_path, monkeypatch)
    say("/rutina 23:00 | volumen 20")

    assert say("/rutina pausar 1").text == "Pausé la rutina 1."
    assert "en pausa" in say("/rutinas").text
    assert say("/rutina seguir 1").text == "Reanudé la rutina 1."
    assert say("/rutina borrar 1").text == "Borré la rutina 1."
    assert say("/rutina borrar 1").text == "No hay una rutina 1."
    assert "Decime el número" in say("/rutina borrar").text and "Decime el número" in say("/rutina borrar todas").text


def test_rutina_explains_itself_and_refuses_what_it_does_not_understand(tmp_path, monkeypatch):
    routines_clock(tmp_path, monkeypatch)

    assert "cuándo | qué" in say("/rutina").text
    for bad in ("23:00 | apagar", "23:00 | volumen 40; reboot", "mañana | volumen 20", "volumen 20", "23:00 | hackear todo"):
        reply = say("/rutina " + bad).text
        assert reply and "Listo" not in reply, bad
    assert say("/rutinas").text.startswith("No tenés rutinas")


def test_rutinas_warns_when_the_switch_is_off(tmp_path, monkeypatch):
    routines_clock(tmp_path, monkeypatch)
    say("/rutina 23:00 | volumen 20")
    monkeypatch.setenv("ROUTINES_ENABLED", "no")

    assert say("/rutinas").text.startswith("⚠ Las rutinas están apagadas")


def test_sistema_refuses_anything_else_without_running_it():
    text = say("/sistema ls -la; rm -rf /").text

    assert "Opciones: disco, ram, uptime, servicio" in text
