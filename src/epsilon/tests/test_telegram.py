import pytest

from epsilon import core
from epsilon.channels import telegram


# --- Configuración: faltar algo no rompe al importar, solo al arrancar -------


def test_read_config_returns_token_and_numeric_id():
    env = {"TELEGRAM_TOKEN": "abc", "ALLOWED_USER_ID": "123"}

    assert telegram.read_config(env) == ("abc", 123)


def test_read_config_without_token_explains_what_is_missing():
    with pytest.raises(ValueError, match="TELEGRAM_TOKEN"):
        telegram.read_config({"ALLOWED_USER_ID": "123"})


def test_read_config_without_allowed_user_refuses_to_start():
    # Preferimos no arrancar antes que arrancar sin saber a quién obedecer.
    with pytest.raises(ValueError, match="ALLOWED_USER_ID"):
        telegram.read_config({"TELEGRAM_TOKEN": "abc"})


def test_read_config_rejects_non_numeric_id():
    with pytest.raises(ValueError, match="ALLOWED_USER_ID"):
        telegram.read_config({"TELEGRAM_TOKEN": "abc", "ALLOWED_USER_ID": "yo"})


# --- Límites de Telegram -----------------------------------------------------


def test_trim_leaves_short_text_alone():
    assert telegram.trim("hola") == "hola"


def test_trim_cuts_long_text_under_telegram_limit():
    assert len(telegram.trim("x" * 10_000)) == telegram.MAX_CHARS


def test_trim_never_returns_empty_text():
    # Telegram rechaza mensajes vacíos.
    assert telegram.trim("") == "(vacío)"


# --- Botones -----------------------------------------------------------------


def test_no_actions_means_no_keyboard():
    assert telegram.keyboard([]) is None


def test_actions_become_one_row_of_buttons():
    markup = telegram.keyboard([core.Action("guardar", "Guardar"), core.Action("nada:1", "No")])

    row = markup.inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in row] == [
        ("Guardar", "guardar"),
        ("No", "nada:1"),
    ]


# --- Menú de comandos --------------------------------------------------------


def test_every_menu_command_exists_in_core():
    # Si el menú muestra un comando que el núcleo no conoce, no haría nada.
    menu = {c.command for c in telegram.COMMANDS}

    assert menu <= set(core.COMMANDS)


# --- Opciones de arranque según el hilo --------------------------------------


def test_in_main_thread_telegram_handles_stop_signals():
    options = telegram.polling_options(in_main_thread=True)

    assert "stop_signals" not in options  # usa los de la librería (Ctrl+C, systemd)


def test_in_a_thread_telegram_does_not_touch_signals():
    # Solo el hilo principal puede manejar señales; en otro hilo daría error.
    assert telegram.polling_options(in_main_thread=False)["stop_signals"] is None


def test_telegram_retries_forever_if_the_network_is_down_at_start():
    for in_main in (True, False):
        assert telegram.polling_options(in_main)["bootstrap_retries"] == -1


# --- Mensajes de error claros ------------------------------------------------

from types import SimpleNamespace

from telegram.error import InvalidToken, NetworkError, TimedOut

from epsilon import state

LEAKY = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"


def test_explain_rejected_token_says_what_to_check_and_hides_the_token():
    text = telegram.explain(InvalidToken(f"The token `{LEAKY}` was rejected by the server."))

    assert "TELEGRAM_TOKEN" in text and "rechazó" in text
    assert LEAKY not in text


def test_explain_network_problems():
    for error in (NetworkError("x"), TimedOut("x")):
        assert "conectar" in telegram.explain(error)


def test_explain_unknown_errors_returns_none_so_the_caller_falls_back():
    assert telegram.explain(RuntimeError("otra cosa")) is None


# --- check: probar el token con getMe (gratis) -------------------------------


def fake_bot(monkeypatch, get_me):
    class FakeBot:
        def __init__(self, token):
            self.token = token

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_me(self):
            return await get_me(self.token)

    monkeypatch.setattr(telegram, "Bot", FakeBot)


def test_check_reports_the_bot_name(monkeypatch):
    async def get_me(token):
        return SimpleNamespace(username="mi_bot")

    fake_bot(monkeypatch, get_me)

    assert telegram.check(LEAKY) == "Conectado como @mi_bot."


def test_check_without_a_token_says_so(monkeypatch):
    with pytest.raises(ValueError, match="token de Telegram"):
        telegram.check("")


def test_check_explains_a_rejected_token_without_leaking_it(monkeypatch):
    async def get_me(token):
        raise InvalidToken(f"The token `{token}` was rejected by the server.")

    fake_bot(monkeypatch, get_me)

    with pytest.raises(ValueError) as error:
        telegram.check(LEAKY)

    assert "rechazó" in str(error.value) and LEAKY not in str(error.value)
    assert error.value.__cause__ is None  # tampoco por la cadena de excepciones


def test_check_explains_connection_problems(monkeypatch):
    async def get_me(token):
        raise NetworkError("sin red")

    fake_bot(monkeypatch, get_me)

    with pytest.raises(ValueError, match="conectar"):
        telegram.check(LEAKY)


# --- Estado: conectando hasta que Telegram responde --------------------------


def test_telegram_is_marked_running_only_after_it_connects(monkeypatch):
    import asyncio

    app = telegram.build_app("123456789:AAH-ejemplo_de_token_de_telegram_vKjP", 1)

    async def no_menu(self, *args, **kwargs):
        return None

    # Los objetos de PTB no admiten parches en la instancia: va en la clase.
    monkeypatch.setattr(type(app.bot), "set_my_commands", no_menu)
    assert "telegram" not in state.snapshot()

    asyncio.run(app.post_init(app))  # PTB lo llama recién tras un getMe exitoso

    assert state.snapshot()["telegram"]["state"] == "running"


def test_contexto_is_in_the_telegram_menu():
    assert "contexto" in [c.command for c in telegram.COMMANDS]


# --- Notas de voz y audios ----------------------------------------------------
# Se transcriben en local (transcribe.py) y el texto entra al núcleo como cualquier mensaje:
# se muestra «🎙 «…»» y el pedido se ejecuta directo, sin pedir confirmación.

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

from epsilon import transcribe


class FakeFile:
    def __init__(self, data=b"OggS-audio"):
        self.data = data

    async def download_as_bytearray(self):
        return bytearray(self.data)


class FakeMedia:
    def __init__(self, size=1000, name=None, mime="audio/ogg"):
        self.file_size, self.mime_type = size, mime
        # Como en Telegram: una nota de voz (Voice) NO tiene file_name; un audio o documento, sí.
        if name is not None:
            self.file_name = name
        self.downloaded = False

    async def get_file(self):
        self.downloaded = True
        return FakeFile()


class FakeUpdate:
    def __init__(self, voice=None, audio=None, document=None, chat_id=7):
        self.voice, self.audio, self.document = voice, audio, document
        self.replies = []
        self.markups = []
        self.message = SimpleNamespace(
            voice=voice, audio=audio, document=document, photo=None, caption=None, reply_text=self.reply_text
        )
        self.effective_chat = SimpleNamespace(id=chat_id)

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        self.markups.append(reply_markup)


class FakeContext:
    def __init__(self):
        self.actions = []
        self.bot = SimpleNamespace(send_chat_action=self.send_chat_action)

    async def send_chat_action(self, chat_id, action):
        self.actions.append(action)


@pytest.fixture
def heard(monkeypatch):
    """Lo que «oye» Whisper (de mentira) y qué le llegó al núcleo."""
    seen = SimpleNamespace(text="+ comprar pan", paths=[], threads=[], messages=[], error=None)

    def fake_audio_to_text(path):
        seen.paths.append(Path(path))
        seen.threads.append(threading.current_thread())
        assert Path(path).exists()  # mientras se transcribe, el archivo está
        if seen.error:
            raise seen.error
        return seen.text

    def fake_handle(msg):
        seen.messages.append(msg)
        return core.Reply("Guardado en inbox.md")

    monkeypatch.setattr(transcribe, "audio_to_text", fake_audio_to_text)
    monkeypatch.setattr(core, "handle", fake_handle)
    # Los ajustes de voz de la PC real no cuentan: cada test parte de los valores por defecto.
    monkeypatch.setenv("TELEGRAM_VOICE", "si")
    monkeypatch.setenv("VOICE_CONFIRM", "no")
    telegram.PENDING_VOICE.clear()
    return seen


def hear(update):
    context = FakeContext()
    asyncio.run(telegram.on_voice(update, context))
    return context


def test_a_voice_note_shows_what_was_understood_and_then_answers_it(heard):
    update = FakeUpdate(voice=FakeMedia())

    hear(update)

    assert update.replies == ["🎙 «+ comprar pan»", "Guardado en inbox.md"]
    [msg] = heard.messages
    assert msg.text == "+ comprar pan" and msg.attachment is None and msg.action is None
    assert msg.session_id == "telegram:7"  # la misma sesión que si lo hubiera escrito


def test_no_confirmation_is_asked_the_request_runs_right_away(heard):
    update = FakeUpdate(voice=FakeMedia())

    hear(update)

    assert len(heard.messages) == 1  # ya se ejecutó
    assert not any("¿" in r for r in update.replies)


def test_music_style_audio_and_audio_documents_work_too(heard):
    for update in (
        FakeUpdate(audio=FakeMedia(name="nota.mp3", mime="audio/mpeg")),
        FakeUpdate(document=FakeMedia(name="grabacion.m4a", mime="audio/mp4")),
    ):
        hear(update)
        assert update.replies[0] == "🎙 «+ comprar pan»"


def test_voice_problems_are_shown_as_they_are_and_nothing_reaches_the_core(heard):
    for error in (
        transcribe.VoiceError("No entendí nada en el audio."),
        transcribe.VoiceNotInstalled("La voz no está instalada. Instalala con: uv sync --extra voz"),
        transcribe.VoiceDownloading("Estoy descargando el modelo de voz (unos 1,6 GB, una sola vez). Mandá el audio de nuevo en unos minutos."),
    ):
        heard.error = error
        update = FakeUpdate(voice=FakeMedia())

        hear(update)

        assert update.replies == [str(error)]
    assert heard.messages == []


def test_an_unexpected_failure_gives_a_short_message(heard):
    heard.error = RuntimeError("detalle interno /ruta/secreta")
    update = FakeUpdate(voice=FakeMedia())

    hear(update)

    assert update.replies == ["No pude procesar el audio."]
    assert heard.messages == []


def test_the_temporary_audio_is_always_deleted(heard):
    hear(FakeUpdate(voice=FakeMedia()))
    heard.error = transcribe.VoiceError("x")
    hear(FakeUpdate(voice=FakeMedia()))
    heard.error = RuntimeError("boom")
    hear(FakeUpdate(voice=FakeMedia()))

    assert len(heard.paths) == 3
    assert not any(p.exists() for p in heard.paths)


def test_the_audio_is_not_saved_in_the_data_folder(heard, tmp_path, monkeypatch):
    from epsilon import notes

    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)

    hear(FakeUpdate(voice=FakeMedia()))

    assert not any(p.is_relative_to(tmp_path) for p in heard.paths)
    assert list(tmp_path.iterdir()) == []


def test_transcribing_does_not_block_the_bot(heard):
    # Transcribir tarda segundos: corre en otro hilo, no en el que atiende a Telegram.
    seen_loop = []

    async def run():
        seen_loop.append(threading.current_thread())
        await telegram.on_voice(FakeUpdate(voice=FakeMedia()), FakeContext())

    asyncio.run(run())

    assert heard.threads[0] is not seen_loop[0]


def test_a_too_big_audio_is_refused_before_downloading_it(heard):
    media = FakeMedia(size=telegram.MAX_DOWNLOAD + 1)
    update = FakeUpdate(voice=media)

    hear(update)

    assert "20 MB" in update.replies[0]
    assert not media.downloaded and heard.messages == []


def test_a_long_transcription_is_trimmed_for_telegram(heard):
    heard.text = "palabra " * 2000

    update = FakeUpdate(voice=FakeMedia())
    hear(update)

    assert len(update.replies[0]) <= telegram.MAX_CHARS + 10


def test_the_chat_shows_typing_while_it_transcribes(heard):
    # Con un error el núcleo no se llama: si hay «escribiendo…», vino de la transcripción.
    heard.error = transcribe.VoiceError("No entendí nada en el audio.")

    context = hear(FakeUpdate(voice=FakeMedia()))

    assert context.actions


@pytest.mark.parametrize(
    "name, expected",
    [
        ("nota.mp3", ".mp3"),
        ("Grabacion.M4A", ".m4a"),
        (None, ".ogg"),
        ("sin_extension", ".ogg"),
        ("raro.verylongext", ".ogg"),  # demasiado larga
        ("a.m 4a", ".ogg"),  # con espacio
        ("x.sh;rm", ".ogg"),  # con símbolos
        ("../../x.ogg", ".ogg"),  # solo cuenta el nombre, y esta extensión es simple
    ],
)
def test_the_temporary_file_extension_is_always_simple(name, expected):
    assert telegram._audio_suffix(FakeMedia(name=name)) == expected


def test_a_real_telegram_voice_has_no_file_name_and_still_works():
    # El bug real: Voice no tiene file_name, y el bot respondía «No pude procesar el audio».
    from telegram import Audio, Document, Voice

    assert not hasattr(Voice("f", "u", 3), "file_name")
    assert telegram._audio_suffix(Voice("f", "u", 3)) == ".ogg"
    assert telegram._audio_suffix(Audio("f", "u", 3, file_name="a.mp3")) == ".mp3"
    assert telegram._audio_suffix(Document("f", "u", file_name="g.m4a")) == ".m4a"


def test_the_real_telegram_objects_have_everything_on_voice_reads():
    # Lo que on_voice toca de un audio, en los tres tipos reales (no en los de mentira).
    from telegram import Audio, Document, Voice

    for media in (Voice("f", "u", 3), Audio("f", "u", 3), Document("f", "u")):
        for attribute in ("file_size", "mime_type", "get_file"):
            assert hasattr(media, attribute), (type(media).__name__, attribute)


def test_voice_handlers_only_answer_the_authorized_user():
    from telegram import Chat, Message, Update, User, Voice
    from telegram.ext import MessageHandler
    from datetime import datetime

    app = telegram.build_app("123456789:AAH-ejemplo_de_token_de_telegram_vKjP", 42)
    handlers = [h for group in app.handlers.values() for h in group if isinstance(h, MessageHandler)]

    def update_from(user_id):
        message = Message(
            1, datetime.now(), Chat(user_id, "private"), from_user=User(user_id, "x", False),
            voice=Voice("fid", "uid", 3),
        )
        return Update(1, message=message)

    answering = lambda user_id: [h for h in handlers if h.check_update(update_from(user_id))]

    assert any(h.callback is telegram.on_voice for h in answering(42))
    assert answering(99) == []  # un desconocido no activa nada (ni la transcripción)


def test_each_kind_of_audio_reaches_the_voice_handler_and_other_files_do_not():
    from telegram import Audio, Chat, Document, Message, Update, User
    from telegram.ext import MessageHandler
    from datetime import datetime

    app = telegram.build_app("123456789:AAH-ejemplo_de_token_de_telegram_vKjP", 42)
    handlers = [h for group in app.handlers.values() for h in group if isinstance(h, MessageHandler)]

    def goes_to(**media):
        message = Message(1, datetime.now(), Chat(42, "private"), from_user=User(42, "x", False), **media)
        return {h.callback for h in handlers if h.check_update(Update(1, message=message))}

    assert telegram.on_voice in goes_to(audio=Audio("a", "u", 3))
    assert telegram.on_voice in goes_to(document=Document("d", "u", file_name="g.m4a", mime_type="audio/mp4"))
    assert telegram.on_voice not in goes_to(document=Document("d", "u", file_name="x.pdf", mime_type="application/pdf"))


# --- Voz: apagada desde /menu, y confirmación opcional ----------------------


def test_with_voice_turned_off_the_audio_is_not_even_downloaded(heard, monkeypatch):
    monkeypatch.setenv("TELEGRAM_VOICE", "no")
    media = FakeMedia()
    update = FakeUpdate(voice=media)

    hear(update)

    assert update.replies == ["La voz está apagada. Prendela con /menu."]
    assert not media.downloaded and heard.paths == [] and heard.messages == []


class FakeQuery:
    """Un toque de botón: guarda cómo quedó el mensaje que lo tenía."""

    def __init__(self, data, chat_id=7, user_id=42):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.edits = []
        self.replies = []
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id), reply_text=self.reply_text)

    async def answer(self):
        pass

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)


def tap(data, **kwargs):
    query = FakeQuery(data, **kwargs)
    asyncio.run(telegram.make_on_button(42)(SimpleNamespace(callback_query=query), FakeContext()))
    return query


def ask_to_confirm(heard, monkeypatch):
    monkeypatch.setenv("VOICE_CONFIRM", "si")
    update = FakeUpdate(voice=FakeMedia())
    hear(update)
    return update


def test_with_confirmation_on_the_note_waits_for_a_tap_and_runs_nothing(heard, monkeypatch):
    update = ask_to_confirm(heard, monkeypatch)

    assert update.replies == ["🎙 «+ comprar pan»\n¿Lo ejecuto?"]
    [[run, cancel]] = update.markups[0].inline_keyboard
    assert (run.text, cancel.text) == ("Ejecutar", "Cancelar")
    assert run.callback_data.startswith("voz:si:") and cancel.callback_data.startswith("voz:no:")
    assert len(run.callback_data.encode()) <= 64
    assert heard.messages == []  # todavía no se ejecutó


def test_run_executes_the_note_once_and_removes_the_buttons(heard, monkeypatch):
    update = ask_to_confirm(heard, monkeypatch)
    [[run, _]] = update.markups[0].inline_keyboard

    query = tap(run.callback_data)

    assert query.edits == [("🎙 «+ comprar pan»", None)]
    assert query.replies == ["Guardado en inbox.md"]
    [msg] = heard.messages
    assert msg.text == "+ comprar pan" and msg.session_id == "telegram:7"
    # Tocarlo otra vez no lo repite.
    again = tap(run.callback_data)
    assert again.edits == [("Ese audio ya no está; mandalo de nuevo.", None)]
    assert len(heard.messages) == 1


def test_cancel_discards_the_note(heard, monkeypatch):
    update = ask_to_confirm(heard, monkeypatch)
    [[_, cancel]] = update.markups[0].inline_keyboard

    query = tap(cancel.callback_data)

    assert query.edits == [("🎙 «+ comprar pan»\nCancelado.", None)]
    assert heard.messages == [] and telegram.PENDING_VOICE == {}


def test_a_note_from_before_a_restart_says_it_is_gone(heard):
    query = tap("voz:si:999")

    assert query.edits == [("Ese audio ya no está; mandalo de nuevo.", None)]
    assert heard.messages == []


def test_another_chat_cannot_run_a_pending_note(heard, monkeypatch):
    update = ask_to_confirm(heard, monkeypatch)
    [[run, _]] = update.markups[0].inline_keyboard

    tap(run.callback_data, chat_id=8)

    assert heard.messages == [] and len(telegram.PENDING_VOICE) == 1


def test_only_the_authorized_user_can_answer(heard, monkeypatch):
    update = ask_to_confirm(heard, monkeypatch)
    [[run, _]] = update.markups[0].inline_keyboard

    query = tap(run.callback_data, user_id=99)

    assert query.edits == [] and heard.messages == []


@pytest.mark.parametrize("data", ["voz:", "voz:si", "voz:quizas:1", "voz:si:1:2"])
def test_malformed_voice_buttons_do_nothing(heard, data):
    query = tap(data)

    assert query.edits == [] and heard.messages == []


def test_pending_notes_are_capped(heard):
    for n in range(telegram.MAX_PENDING_VOICE + 5):
        telegram.remember_voice(7, f"nota {n}")

    assert len(telegram.PENDING_VOICE) == telegram.MAX_PENDING_VOICE
    assert "nota 0" not in {text for _, text in telegram.PENDING_VOICE.values()}
