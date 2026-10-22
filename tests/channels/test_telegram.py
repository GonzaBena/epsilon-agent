import pytest

from epsilon import core, tools
from epsilon.data import conversations
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

    # (Los que atiende el propio canal, como /nuevo, no pasan por el núcleo.)
    own = {t.name for t in tools.TOOLS if t.channel == "telegram"}
    assert menu <= set(core.COMMANDS) | own


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

from epsilon.voice import transcribe


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
        return SimpleNamespace(message_id=1000 + len(self.replies))


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
    monkeypatch.setenv("VOICE_ENABLED", "si")
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
    from epsilon.data import notes

    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)

    hear(FakeUpdate(voice=FakeMedia()))

    assert not any(p.is_relative_to(tmp_path) for p in heard.paths)
    # Solo la conversación de Telegram (texto); del audio no queda ningún archivo.
    assert [p.name for p in tmp_path.iterdir()] == ["conversations"]
    assert not any(p.suffix in (".ogg", ".webm", ".mp3") for p in tmp_path.rglob("*"))


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
    monkeypatch.setenv("VOICE_ENABLED", "no")
    media = FakeMedia()
    update = FakeUpdate(voice=media)

    hear(update)

    assert update.replies == [transcribe.VOICE_OFF]
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


# --- La conversación de Telegram: lo que se habla queda anotado para la web --------


def telegram_conversation():
    """(id, mensajes) de la conversación abierta de Telegram."""
    conversation_id = conversations.current("telegram")
    return conversation_id, conversations.messages(conversation_id) if conversation_id else []


def say_text(text, chat_id=7):
    update = FakeUpdate(chat_id=chat_id)
    update.message.text = text
    asyncio.run(telegram.on_text(update, FakeContext()))
    return update


@pytest.fixture
def core_says(monkeypatch):
    replies = []
    monkeypatch.setattr(
        core,
        "handle",
        lambda msg: replies.pop(0) if replies else core.Reply("ok"),
    )
    telegram.LOGGED.clear()
    return replies


def test_a_text_turn_is_saved_with_what_the_reply_saved(core_says):
    core_says.append(core.Reply("Guardado en inbox.md", artifacts=[core.Artifact("note", "inbox.md")]))

    say_text("+ comprar pan")

    conversation_id, found = telegram_conversation()
    assert [(m["role"], m["text"]) for m in found] == [("user", "+ comprar pan"), ("bot", "Guardado en inbox.md")]
    assert conversations.artifacts(conversation_id) == [{"kind": "note", "path": "inbox.md"}]
    assert conversations.channel(conversation_id) == "telegram"


def test_every_turn_goes_to_the_same_conversation(core_says):
    say_text("uno")
    say_text("dos")

    assert len(conversations.list_all()) == 1
    assert [m["text"] for m in telegram_conversation()[1] if m["role"] == "user"] == ["uno", "dos"]


def test_a_file_turn_saves_the_name_and_the_caption(core_says):
    update = FakeUpdate()
    update.message.document = SimpleNamespace(
        file_size=10, mime_type="application/pdf", file_name="factura.pdf", get_file=FakeMedia().get_file
    )
    update.message.caption = "gastos: fecha, total"

    asyncio.run(telegram.on_file(update, FakeContext()))

    assert telegram_conversation()[1][0]["text"] == "factura.pdf\ngastos: fecha, total"


def test_a_voice_note_is_saved_as_what_was_heard(heard):
    hear(FakeUpdate(voice=FakeMedia()))

    assert [m["text"] for m in telegram_conversation()[1]][0] == "🎙 + comprar pan"


def test_a_confirmed_voice_note_is_saved_only_when_it_runs(heard, monkeypatch):
    update = ask_to_confirm(heard, monkeypatch)
    assert telegram_conversation()[1] == []  # esperando: todavía no se ejecutó nada
    [[run, _]] = update.markups[0].inline_keyboard

    tap(run.callback_data)

    assert [m["text"] for m in telegram_conversation()[1]] == ["🎙 + comprar pan", "Guardado en inbox.md"]


def test_a_tapped_button_is_noted_on_the_message_that_had_it(core_says):
    core_says.append(core.Reply("¿Resumir?", actions=[core.Action("resumir:1", "Resumir"), core.Action("nada:1", "No")]))
    core_says.append(core.Reply("Listo"))
    update = say_text("hola")
    # El mensaje de Telegram que tenía los botones (el primero que mandó el bot).
    sent_id = 1001

    query = FakeQuery("resumir:1")
    query.message.message_id = sent_id
    asyncio.run(telegram.make_on_button(42)(SimpleNamespace(callback_query=query), FakeContext()))

    found = telegram_conversation()[1]
    assert found[1]["used"] is True and found[1]["chosen"] == "resumir:1"
    assert found[-1]["text"] == "Listo"
    assert update.replies[0] == "¿Resumir?"


def test_a_disk_problem_does_not_break_the_bot(core_says, monkeypatch, capsys):
    def broken(name):
        raise OSError("disco lleno")

    monkeypatch.setattr(conversations, "for_channel", broken)

    update = say_text("hola")

    assert update.replies == ["ok"]  # igual contestó
    assert "No pude anotar la conversación" in capsys.readouterr().out


def test_nuevo_closes_the_conversation_and_the_next_message_starts_another(core_says):
    say_text("viejo")
    first, _ = telegram_conversation()
    update = FakeUpdate()

    asyncio.run(telegram.on_new(update, FakeContext()))
    assert "empieza de cero" in update.replies[0] and conversations.current("telegram") is None
    say_text("nuevo")

    second, _ = telegram_conversation()
    assert second != first
    assert [c["id"] for c in conversations.list_all()] == [second]
    assert conversations.messages(first)[0]["text"] == "viejo"


def test_nuevo_is_wired_before_the_generic_text_handler_and_only_for_me():
    app = telegram.build_app("123:abc", 42)
    handlers = [h for h in app.handlers[0]]
    names = [type(h).__name__ for h in handlers]

    assert names.index("CommandHandler") < names.index("MessageHandler")
    command = next(h for h in handlers if type(h).__name__ == "CommandHandler")
    assert command.commands == {"nuevo"}


def test_the_telegram_conversation_never_stores_the_bot_token(core_says, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "123456789:AAH-ejemplo_de_token_de_telegram_vKjP")

    say_text("mi token: 123456789:AAH-ejemplo_de_token_de_telegram_vKjP")

    assert "AAH-ejemplo" not in " ".join(m["text"] for m in telegram_conversation()[1])


# --- Charla con IA en Telegram (/chat) ----------------------------------------------------------
# Con una conversación de modo IA abierta, todo lo que Telegram le manda al núcleo lleva su id; sin ella, no.


@pytest.fixture
def seen_by_core(monkeypatch):
    """Los mensajes que llegan al núcleo (el núcleo de mentira contesta «ok»)."""
    messages = []

    def fake_handle(msg):
        messages.append(msg)
        return core.Reply("ok")

    monkeypatch.setattr(core, "handle", fake_handle)
    telegram.LOGGED.clear()
    return messages


def open_ai_chat():
    update = FakeUpdate()
    asyncio.run(telegram.on_chat(update, FakeContext()))
    return update


def test_chat_opens_an_ai_conversation_and_says_how_it_works(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "gemini:modelo-a")
    monkeypatch.setenv("CHAT_LIMIT_USD", "0.75")
    monkeypatch.setenv("CHAT_MAX_TURNS_TELEGRAM", "4")

    update = open_ai_chat()

    cid = conversations.current("telegram")
    assert conversations.mode(cid) == "ai" and conversations.limit(cid) == 0.75
    text = update.replies[0]
    assert "gemini:modelo-a" in text and "0.75" in text and "4" in text
    assert "/nuevo" in text


def test_chat_twice_does_not_open_a_second_conversation():
    open_ai_chat()
    first = conversations.current("telegram")

    update = open_ai_chat()

    assert conversations.current("telegram") == first
    assert "Ya estás" in update.replies[0]


def test_chat_leaves_the_previous_conversation_saved_and_closed(seen_by_core):
    say_text("viejo")
    old, _ = telegram_conversation()

    open_ai_chat()

    assert conversations.current("telegram") != old
    assert conversations.messages(old)[0]["text"] == "viejo"


def test_chat_warns_when_the_cost_cannot_be_measured(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "gemini:modelo-sin-precio")

    update = open_ai_chat()

    assert "precio" in update.replies[0]


def test_text_carries_the_ai_conversation_only_while_it_is_open(seen_by_core):
    say_text("hola")
    open_ai_chat()
    cid = conversations.current("telegram")
    say_text("seguimos")
    asyncio.run(telegram.on_new(FakeUpdate(), FakeContext()))
    say_text("de nuevo")

    assert [m.conversation_id for m in seen_by_core] == [None, cid, None]


def test_a_button_carries_the_ai_conversation(seen_by_core):
    open_ai_chat()
    cid = conversations.current("telegram")
    query = FakeQuery("perm:si")
    query.message.message_id = 1

    asyncio.run(telegram.make_on_button(42)(SimpleNamespace(callback_query=query), FakeContext()))

    assert seen_by_core[0].conversation_id == cid


def test_a_file_carries_the_ai_conversation(seen_by_core):
    open_ai_chat()
    cid = conversations.current("telegram")
    update = FakeUpdate()
    update.message.document = SimpleNamespace(
        file_size=10, mime_type="application/pdf", file_name="factura.pdf", get_file=FakeMedia().get_file
    )

    asyncio.run(telegram.on_file(update, FakeContext()))

    assert seen_by_core[0].conversation_id == cid


def test_a_voice_note_carries_the_ai_conversation(heard):
    open_ai_chat()
    cid = conversations.current("telegram")

    hear(FakeUpdate(voice=FakeMedia()))

    assert heard.messages[0].conversation_id == cid


def test_the_answer_of_the_ai_is_saved_marked_as_ai(monkeypatch):
    monkeypatch.setattr(core, "handle", lambda msg: core.Reply("Buenas", ai=True))
    open_ai_chat()

    say_text("hola")

    assert [m["ai"] for m in telegram_conversation()[1] if m["role"] == "bot"][-1] is True


def test_a_disk_problem_looking_for_the_chat_falls_back_to_a_normal_message(seen_by_core, monkeypatch):
    def broken(name):
        raise OSError("disco roto")

    monkeypatch.setattr(conversations, "current", broken)

    say_text("hola")

    assert seen_by_core[0].conversation_id is None


@pytest.fixture
def ai_model(monkeypatch):
    """Un modelo de mentira con precio, para ver qué le llega a la IA de verdad (sin red)."""
    from epsilon.ai import llm, usage

    monkeypatch.setenv("AI_MODEL", "gemini:modelo-a")
    monkeypatch.setenv("CHAT_LIMIT_USD", "")
    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (1.0, 4.0)})
    calls = []

    def fake_chat(history, system, tools, max_tokens=1500, allow_calls=True):
        calls.append([(turn.role, turn.parts[0]) for turn in history])
        usage.record("modelo-a", 10, 10)
        return llm.Step("la IA contesta", [], llm.text_turn("model", "la IA contesta"))

    monkeypatch.setattr(llm, "chat_tools", fake_chat)
    return calls


def test_free_text_in_the_chat_goes_to_the_ai_with_the_talk_so_far(ai_model):
    open_ai_chat()

    update = say_text("hola")
    say_text("corregí lo anterior")

    assert update.replies == ["la IA contesta"]
    assert ai_model[1] == [
        ("user", "hola"), ("model", "la IA contesta"), ("user", "corregí lo anterior"),
    ]


def test_commands_in_the_chat_never_call_the_model(ai_model):
    open_ai_chat()

    update = say_text("+ comprar pan")

    assert ai_model == []  # y conftest.py bloquearía cualquier llamada que se escape
    assert "inbox" in update.replies[0]


def test_telegram_context_is_cut_to_the_telegram_limit(ai_model, monkeypatch):
    monkeypatch.setenv("CHAT_MAX_TURNS_TELEGRAM", "2")
    open_ai_chat()
    for n in range(4):
        say_text(f"pregunta {n}")

    assert [text for role, text in ai_model[-1] if role == "user"] == ["pregunta 1", "pregunta 2", "pregunta 3"]


def test_chat_is_wired_before_the_generic_text_handler_and_only_for_me():
    app = telegram.build_app("123:abc", 42)
    handlers = list(app.handlers[0])
    commands = [h for h in handlers if type(h).__name__ == "CommandHandler"]

    assert {"chat"} in [h.commands for h in commands]
    last_command = max(handlers.index(h) for h in commands)
    first_text = min(i for i, h in enumerate(handlers) if type(h).__name__ == "MessageHandler")
    assert last_command < first_text


def test_chat_is_in_the_telegram_menu_and_is_not_a_core_command():
    assert "chat" in [c.command for c in telegram.COMMANDS]
    assert "chat" not in core.COMMANDS and "chat" not in tools.command_names()


# --- Apagar el bot desde la página Canales -----------------------------------


class FakeLoop:
    def __init__(self):
        self.scheduled = []

    def call_soon_threadsafe(self, callback):
        self.scheduled.append(callback)


def test_stop_asks_the_bots_own_loop_to_stop_the_app(monkeypatch):
    loop, app = FakeLoop(), SimpleNamespace(stop_running=lambda: None)
    monkeypatch.setitem(telegram._runtime, "loop", loop)
    monkeypatch.setitem(telegram._runtime, "app", app)

    telegram.stop()

    # No lo corta desde este hilo: lo agenda en el bucle del bot (stop_running lo exige).
    assert loop.scheduled == [app.stop_running]


def test_stop_before_the_bot_finished_connecting_is_remembered(monkeypatch):
    monkeypatch.setitem(telegram._runtime, "loop", None)
    monkeypatch.setitem(telegram._runtime, "app", None)
    monkeypatch.setitem(telegram._runtime, "stop_requested", False)

    telegram.stop()

    assert telegram._runtime["stop_requested"] is True  # post_init lo cumple al arrancar


def test_reset_forgets_an_old_stop_request(monkeypatch):
    monkeypatch.setitem(telegram._runtime, "stop_requested", True)

    telegram.reset()

    assert telegram._runtime["stop_requested"] is False


def test_run_clears_the_runtime_so_the_bot_can_be_started_again(monkeypatch):
    monkeypatch.setattr(telegram, "read_config", lambda env: ("123:abc", 42))
    app = SimpleNamespace(run_polling=lambda **kw: telegram._runtime.update(loop=object(), app=app))
    monkeypatch.setattr(telegram, "build_app", lambda token, uid: app)

    telegram.run()

    assert telegram._runtime["app"] is None and telegram._runtime["loop"] is None


@pytest.fixture
def offline_ptb(monkeypatch):
    """La Application real de python-telegram-bot, con la red de mentira: conecta, escucha y se apaga."""
    from telegram import User
    from telegram.ext import ExtBot

    async def initialize(self):
        self._initialized = True
        self._bot_user = User(id=1, first_name="x", is_bot=True, username="x_bot")

    async def nothing(self, *args, **kwargs):
        return True

    async def no_updates(self, *args, **kwargs):
        await asyncio.sleep(0.05)
        return ()

    for name, fake in (
        ("initialize", initialize),
        ("shutdown", nothing),
        ("delete_webhook", nothing),
        ("set_my_commands", nothing),
        ("get_updates", no_updates),
    ):
        monkeypatch.setattr(ExtBot, name, fake)
    monkeypatch.setattr(telegram, "read_config", lambda env: ("123:abc", 42))
    real_build = telegram.build_app
    monkeypatch.setattr(telegram, "build_app", lambda token, uid: real_build(token, uid))


def run_bot_in_a_thread():
    import threading as threads

    finished = threads.Event()

    def target():
        telegram.run()
        finished.set()

    threads.Thread(target=target, daemon=True).start()
    return finished


def wait_until_connected():
    import time

    for _ in range(200):
        if telegram._runtime["loop"] is not None:
            return
        time.sleep(0.02)
    raise AssertionError("el bot no llegó a conectar")


def test_the_real_bot_stops_from_another_thread_and_can_be_started_again(offline_ptb):
    for _ in range(2):  # apagar y volver a prender, en el mismo proceso
        telegram.reset()  # lo que hace main justo antes de prenderlo
        finished = run_bot_in_a_thread()
        wait_until_connected()

        telegram.stop()

        assert finished.wait(timeout=8), "el bot no se apagó"
        assert telegram._runtime["app"] is None and telegram._runtime["loop"] is None


def test_the_real_bot_stops_even_if_it_is_asked_while_still_connecting(offline_ptb):
    telegram.reset()
    finished = run_bot_in_a_thread()

    telegram.stop()  # sin esperar a que conecte

    assert finished.wait(timeout=8)


# --- Enviar archivos guardados ---------------------------------------------------


class SendingContext(FakeContext):
    def __init__(self):
        super().__init__()
        self.documents, self.messages = [], []
        self.bot.send_document = self.send_document
        self.bot.send_message = self.send_message

    async def send_document(self, chat_id, document, filename=None):
        self.documents.append((chat_id, document, filename))

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


@pytest.fixture
def stored(tmp_path, monkeypatch):
    from epsilon.data import notes

    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    (tmp_path / "tickets").mkdir()
    (tmp_path / "tickets" / "super.jpg").write_bytes(b"JPGDATA")
    return tmp_path


def test_enviar_sends_the_original_file_as_a_document(stored):
    update = FakeUpdate(chat_id=7)
    update.message.text = "/enviar super"
    context = SendingContext()

    asyncio.run(telegram.on_text(update, context))

    assert update.replies == ["Te mando tickets/super.jpg."]
    assert context.documents == [(7, b"JPGDATA", "super.jpg")]


def test_a_failure_sending_is_reported_without_details(stored, monkeypatch):
    context = SendingContext()

    async def boom(*args, **kwargs):
        raise RuntimeError("detalle interno con https://api.telegram.org/botTOKEN")

    context.bot.send_document = boom

    asyncio.run(telegram.send_files(context, 7, ["tickets/super.jpg"]))

    assert context.messages == [(7, "No pude enviar tickets/super.jpg.")]


def test_send_files_does_not_trust_a_path_that_is_not_in_the_library(stored):
    (stored / "secreto.env").write_text("TOKEN=x\n", encoding="utf-8")
    context = SendingContext()

    asyncio.run(telegram.send_files(context, 7, ["secreto.env", "../fuera.md"]))

    assert context.documents == []
    assert len(context.messages) == 2


def test_enviar_is_in_the_telegram_menu():
    assert "enviar" in {c.command for c in telegram.COMMANDS}
