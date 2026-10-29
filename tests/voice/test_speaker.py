import pytest

from epsilon.voice import speaker


@pytest.fixture
def splitter():
    return speaker.SentenceSplitter()


# --- Cortar el texto que va llegando en oraciones --------------------------------------------------------------------


def test_a_finished_sentence_is_handed_out_and_the_unfinished_tail_waits(splitter):
    assert splitter.feed("Hola, ¿cómo estás hoy? Bien") == ["Hola, ¿cómo estás hoy?"]
    assert splitter.flush("Hola, ¿cómo estás hoy? Bien") == ["Bien"]


def test_text_without_an_ending_waits_for_the_flush(splitter):
    assert splitter.feed("todavía no terminé de decir lo que quería") == []
    assert splitter.flush("todavía no terminé de decir lo que quería") == ["todavía no terminé de decir lo que quería"]


def test_flushing_twice_never_repeats_anything(splitter):
    splitter.flush("Hola mundo")

    assert splitter.flush("Hola mundo") == []


def test_a_terminator_at_the_very_end_is_not_enough_because_more_may_follow(splitter):
    # «3.» podría ser el comienzo de «3.5»: hace falta ver lo que viene después.
    assert splitter.feed("Hola, ¿cómo estás hoy?") == []
    assert splitter.flush("Hola, ¿cómo estás hoy?") == ["Hola, ¿cómo estás hoy?"]


def test_very_short_sentences_are_joined_with_the_next_so_it_does_not_sound_like_a_machine_gun(splitter):
    assert splitter.feed("Sí. Claro. Te cuento todo lo que pasó ayer. Después") == ["Sí. Claro. Te cuento todo lo que pasó ayer."]


def test_a_decimal_number_does_not_end_a_sentence(splitter):
    assert splitter.feed("El valor es 3.5 dólares por unidad. Listo") == ["El valor es 3.5 dólares por unidad."]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Habló con el Sr. Pérez sobre el tema pendiente. Luego", "Habló con el Sr. Pérez sobre el tema pendiente."),
        ("Pasá por la av. Corrientes y doblá a la derecha. Luego", "Pasá por la av. Corrientes y doblá a la derecha."),
        ("Sirve para varias cosas, p. ej. esto funciona bien y es útil. Fin", "Sirve para varias cosas, p. ej. esto funciona bien y es útil."),
    ],
)
def test_an_abbreviation_does_not_end_a_sentence(splitter, text, expected):
    assert splitter.feed(text) == [expected]


def test_quotes_and_brackets_after_the_period_stay_with_the_sentence(splitter):
    assert splitter.feed('Dijo "vamos a casa ahora mismo." Después') == ['Dijo "vamos a casa ahora mismo."']


def test_spanish_opening_marks_belong_to_the_next_sentence(splitter):
    assert splitter.feed("¡Genial, lo logramos juntos! ¿Y ahora") == ["¡Genial, lo logramos juntos!"]


def test_each_line_of_a_list_is_its_own_chunk(splitter):
    text = "Primera tarea pendiente de hoy\nSegunda tarea pendiente de mañana\nTer"

    assert splitter.feed(text) == ["Primera tarea pendiente de hoy", "Segunda tarea pendiente de mañana"]


def test_a_long_run_without_periods_is_cut_at_a_comma_so_speech_can_start(splitter):
    text = "uno dos tres cuatro, " * 30 + "fin"

    chunks = splitter.feed(text) + splitter.flush(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= speaker.MAX_CHARS for chunk in chunks)
    assert " ".join(chunks).split() == text.split()  # no se pierde ni se repite nada


def test_a_long_run_without_any_punctuation_is_cut_at_a_space(splitter):
    text = " ".join(["palabra"] * 120)

    chunks = splitter.feed(text) + splitter.flush(text)

    assert len(chunks) > 1 and all(len(chunk) <= speaker.MAX_CHARS for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


def test_the_text_grows_between_calls_and_only_new_sentences_come_out(splitter):
    assert splitter.feed("Hola, ¿cómo estás hoy? Bi") == ["Hola, ¿cómo estás hoy?"]
    assert splitter.feed("Hola, ¿cómo estás hoy? Bien, gracias por preguntar. Y") == ["Bien, gracias por preguntar."]
    assert splitter.feed("Hola, ¿cómo estás hoy? Bien, gracias por preguntar. Y vos?") == []


def test_a_new_round_starts_from_zero_when_the_text_is_replaced_by_a_shorter_one(splitter):
    splitter.feed("Hola, ¿cómo estás hoy? Bien, gracias por preguntar. Y")

    assert splitter.feed("Dejame mirar eso un momento. Ya") == ["Dejame mirar eso un momento."]


def test_reset_forgets_what_was_already_spoken(splitter):
    splitter.feed("Hola, ¿cómo estás hoy? Bien")
    splitter.reset()

    assert splitter.feed("Hola, ¿cómo estás hoy? Bien") == ["Hola, ¿cómo estás hoy?"]


# --- El Speaker: eventos de la charla -> voz ---------------------------------------------------------------------------

import threading  # noqa: E402


class FakePlayer:
    """Hace de aplay: anota lo que se le escribió y si lo cerraron bien o lo mataron."""

    def __init__(self, rate):
        self.rate = rate
        self.written = []
        self.finished = False
        self.killed = False
        self.hold = None  # un Event: si está, finish() espera a que lo suelten (para probar interrumpir)

    def write(self, data):
        if self.killed:
            raise BrokenPipeError
        self.written.append(data)

    def finish(self):
        if self.hold is not None:
            self.hold.wait(3)
        self.finished = True

    def kill(self):
        self.killed = True
        if self.hold is not None:
            self.hold.set()


class Rig:
    """Un Speaker con sintetizador y reproductor de mentira, y una forma de esperar a que termine de procesar."""

    def __init__(self, monkeypatch):
        self.said = []
        self.players = []
        self.synthesized = []
        self.fail_synth = None
        self.fail_player = None
        monkeypatch.setenv("VOICE_SPEAK", "si")
        self.speaker = speaker.Speaker(self.said.append, synth=self.synth, player_factory=self.player)

    def synth(self, text, voice, speed):
        if self.fail_synth:
            raise self.fail_synth
        self.synthesized.append((text, voice, speed))
        return text.encode(), 24000  # el «audio» es el propio texto: así se ve qué sonó y en qué orden

    def player(self, rate):
        if self.fail_player:
            raise self.fail_player
        player = FakePlayer(rate)
        self.players.append(player)
        return player

    def send(self, *events, turn=1):
        for event in events:
            self.speaker.on_event({"turn": turn, **event})

    def settle(self):
        assert self.speaker.drain(3), "el Speaker no terminó de procesar"

    def spoken(self):
        return [chunk.decode() for player in self.players for chunk in player.written]


@pytest.fixture
def rig(monkeypatch):
    rig = Rig(monkeypatch)
    yield rig
    rig.speaker.close()


def start(rig, turn=1):
    rig.speaker.on_event({"type": "turn", "state": "start", "turn": turn})


def test_a_sentence_is_spoken_as_soon_as_it_is_complete_not_when_the_answer_ends(rig):
    start(rig)

    rig.send({"type": "text", "text": "Hola, ¿cómo estás hoy? Bi"})
    rig.settle()

    assert rig.spoken() == ["Hola, ¿cómo estás hoy?"]
    assert not rig.players[0].finished  # la respuesta sigue: el reproductor queda abierto


def test_the_whole_answer_is_spoken_in_order_however_long_it_is(rig):
    start(rig)
    sentences = [f"Esta es la oración número {n} de una respuesta bastante larga." for n in range(1, 21)]
    text = ""
    for sentence in sentences:
        text += sentence + " "
        rig.send({"type": "text", "text": text + "Y"})
    rig.send({"type": "text", "text": text.strip()}, {"type": "reply", "text": text.strip()})
    rig.settle()

    assert rig.spoken() == sentences  # ninguna se pierde, ninguna se repite y no se corta a las pocas
    assert rig.players[0].finished and len(rig.players) == 1  # un solo reproductor: suenan seguidas, sin cortes


def test_the_reply_alone_is_spoken_when_the_model_did_not_stream_text(rig):
    start(rig)

    rig.send({"type": "reply", "text": "Son las diez de la noche."})
    rig.settle()

    assert rig.spoken() == ["Son las diez de la noche."]
    assert rig.players[0].finished


def test_what_was_already_spoken_is_not_said_again_with_the_reply(rig):
    start(rig)

    rig.send({"type": "text", "text": "Son las diez de la noche. Hoy"}, {"type": "text", "text": "Son las diez de la noche. Hoy es jueves."})
    rig.send({"type": "reply", "text": "Son las diez de la noche. Hoy es jueves."})
    rig.settle()

    assert rig.spoken() == ["Son las diez de la noche.", "Hoy es jueves."]


def test_what_the_model_says_before_using_a_tool_is_spoken_before_the_tool_runs(rig):
    start(rig)

    rig.send({"type": "model", "step": 1}, {"type": "text", "text": "Dejame mirar eso un momento"})
    rig.send({"type": "tool", "name": "fecha", "state": "running"})
    rig.settle()

    assert rig.spoken() == ["Dejame mirar eso un momento"]


def test_each_round_of_the_model_starts_its_text_from_zero(rig):
    start(rig)

    rig.send({"type": "model", "step": 1}, {"type": "text", "text": "Dejame mirar eso un momento"}, {"type": "tool", "name": "fecha", "state": "running"})
    rig.send({"type": "model", "step": 2}, {"type": "text", "text": "Son las diez de la noche."}, {"type": "reply", "text": "Son las diez de la noche."})
    rig.settle()

    assert rig.spoken() == ["Dejame mirar eso un momento", "Son las diez de la noche."]


def test_the_voice_and_speed_come_from_the_settings(rig, monkeypatch):
    monkeypatch.setenv("VOICE_TTS_VOICE", "em_alex")
    monkeypatch.setenv("VOICE_TTS_SPEED", "1.2")
    start(rig)

    rig.send({"type": "reply", "text": "Hola, ¿cómo estás hoy?"})
    rig.settle()

    assert rig.synthesized == [("Hola, ¿cómo estás hoy?", "em_alex", 1.2)]


def test_the_default_voice_is_dora(rig):
    start(rig)

    rig.send({"type": "reply", "text": "Hola, ¿cómo estás hoy?"})
    rig.settle()

    assert rig.synthesized[0][1] == "ef_dora"


def test_markdown_never_reaches_the_voice(rig):
    start(rig)

    rig.send({"type": "reply", "text": "**Listo**, lo anoté en https://ejemplo.com/muy/largo"})
    rig.settle()

    assert rig.synthesized[0][0] == "Listo, lo anoté en enlace"


def test_nothing_is_spoken_when_the_voice_is_off(rig, monkeypatch):
    monkeypatch.setenv("VOICE_SPEAK", "no")
    start(rig)

    rig.send({"type": "text", "text": "Hola, ¿cómo estás hoy? Bi"}, {"type": "reply", "text": "Hola, ¿cómo estás hoy? Bien."})
    rig.settle()

    assert rig.synthesized == [] and rig.players == []


def test_events_of_another_turn_or_without_a_turn_are_ignored(rig):
    start(rig, turn=2)

    rig.send({"type": "reply", "text": "Esto es de un turno viejo."}, turn=1)
    rig.speaker.on_event({"type": "reply", "text": "Esto no trae turno."})
    rig.speaker.on_event({"type": "status", "text": "Escuchando…"})
    rig.settle()

    assert rig.synthesized == []


def test_an_error_message_is_spoken_too(rig):
    start(rig)

    rig.send({"type": "error", "text": "Algo falló al hablar con la IA."})
    rig.settle()

    assert rig.spoken() == ["Algo falló al hablar con la IA."]


def test_stopping_kills_the_audio_drops_what_was_waiting_and_ignores_the_rest_of_that_turn(rig):
    start(rig)
    rig.send({"type": "text", "text": "Hola, ¿cómo estás hoy? Bi"})
    rig.settle()
    player = rig.players[0]

    rig.speaker.stop()
    rig.send({"type": "text", "text": "Hola, ¿cómo estás hoy? Bien, gracias por preguntar. Y"}, {"type": "reply", "text": "Hola, ¿cómo estás hoy? Bien, gracias por preguntar."})
    rig.settle()

    assert player.killed
    assert rig.spoken() == ["Hola, ¿cómo estás hoy?"]  # nada de lo que llegó después de interrumpir


def test_after_stopping_the_next_turn_speaks_normally_with_a_new_player(rig):
    start(rig)
    rig.send({"type": "text", "text": "Hola, ¿cómo estás hoy? Bi"})
    rig.settle()
    rig.speaker.stop()

    start(rig, turn=2)
    rig.send({"type": "reply", "text": "Son las diez de la noche."}, turn=2)
    rig.settle()

    assert len(rig.players) == 2 and rig.players[0].killed and not rig.players[1].killed
    assert rig.spoken()[-1] == "Son las diez de la noche."


def test_a_sentence_still_being_synthesized_when_you_interrupt_never_plays(monkeypatch):
    release = threading.Event()
    played = []

    def slow_synth(text, voice, speed):
        release.wait(3)
        return text.encode(), 24000

    monkeypatch.setenv("VOICE_SPEAK", "si")
    instance = speaker.Speaker(lambda text: None, synth=slow_synth, player_factory=lambda rate: played.append(rate) or FakePlayer(rate))
    try:
        instance.on_event({"type": "turn", "state": "start", "turn": 1})
        instance.on_event({"type": "reply", "text": "Una oración que tarda en salir.", "turn": 1})
        instance.stop()
        release.set()
        assert instance.drain(3)
    finally:
        instance.close()

    assert played == []  # el reproductor ni se llegó a abrir


def test_a_synthesis_failure_is_said_once_and_the_rest_of_the_turn_is_skipped(rig):
    rig.fail_synth = RuntimeError("detalle interno")
    start(rig)

    rig.send({"type": "text", "text": "Primera oración bastante larga. Segunda oración bastante larga. Ter"})
    rig.send({"type": "reply", "text": "Primera oración bastante larga. Segunda oración bastante larga. Tercera."})
    rig.settle()

    assert rig.said == ["No pude generar la voz."]
    assert "detalle" not in str(rig.said)


def test_a_missing_voice_package_says_how_to_install_it(rig):
    rig.fail_synth = ImportError("kokoro_onnx")
    start(rig)

    rig.send({"type": "reply", "text": "Hola, ¿cómo estás hoy?"})
    rig.settle()

    assert rig.said == ["Para que hable instalá la voz: uv sync --extra voz"]


def test_a_missing_aplay_says_what_to_install(rig):
    rig.fail_player = FileNotFoundError("aplay")
    start(rig)

    rig.send({"type": "reply", "text": "Hola, ¿cómo estás hoy?"})
    rig.settle()

    assert rig.said == ["Falta el programa «aplay» (alsa-utils) para reproducir la voz."]


def test_the_warning_is_not_repeated_in_the_next_turns(rig):
    rig.fail_synth = RuntimeError("x")
    for turn in (1, 2):
        start(rig, turn)
        rig.send({"type": "reply", "text": "Hola, ¿cómo estás hoy?"}, turn=turn)
        rig.settle()

    assert rig.said == ["No pude generar la voz."]


def test_a_turn_with_nothing_pronounceable_says_nothing_and_opens_no_player(rig):
    start(rig)

    rig.send({"type": "reply", "text": "🙂 🙂"})
    rig.settle()

    assert rig.synthesized == [] and rig.players == []


# --- Avisos hablados: un fallo del micrófono o de la transcripción también se dice (no solo se escribe) -------------------


def test_a_notice_is_spoken_even_though_no_turn_is_going_on(rig):
    rig.speaker.on_event({"type": "say", "text": "No te escuché: probá de nuevo."})
    rig.settle()

    assert rig.spoken() == ["No te escuché: probá de nuevo."]
    assert rig.players[0].finished


def test_only_the_first_sentence_of_a_long_technical_notice_is_spoken(rig):
    text = "No pude grabar: PipeWire no tiene un micrófono predeterminado disponible. Conectá uno y elegilo como entrada en los ajustes de sonido (con «wpctl status» ves los que hay)."

    rig.speaker.on_event({"type": "say", "text": text})
    rig.settle()

    assert rig.spoken() == ["No pude grabar: PipeWire no tiene un micrófono predeterminado disponible."]


def test_a_notice_without_any_full_stop_is_cut_at_a_word_when_it_is_too_long(rig):
    rig.speaker.on_event({"type": "say", "text": "palabra " * 60})
    rig.settle()

    (sentence,) = rig.spoken()
    assert len(sentence) <= speaker.MAX_NOTICE and not sentence.endswith(" ")


def test_notices_are_silent_when_the_voice_is_off(rig, monkeypatch):
    monkeypatch.setenv("VOICE_SPEAK", "no")

    rig.speaker.on_event({"type": "say", "text": "No te escuché: probá de nuevo."})
    rig.settle()

    assert rig.synthesized == []


def test_a_notice_after_an_interruption_is_spoken_normally(rig):
    start(rig)
    rig.send({"type": "text", "text": "Hola, ¿cómo estás hoy? Bi"})
    rig.settle()
    rig.speaker.stop()

    rig.speaker.on_event({"type": "say", "text": "No te escuché: probá de nuevo."})
    rig.settle()

    assert rig.spoken()[-1] == "No te escuché: probá de nuevo."


def test_a_blank_notice_says_nothing(rig):
    rig.speaker.on_event({"type": "say", "text": "   "})
    rig.settle()

    assert rig.synthesized == [] and rig.players == []
