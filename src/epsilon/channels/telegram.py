import asyncio
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from datetime import datetime
import html
import itertools
from pathlib import Path
import re
import tempfile
import threading
import time
import urllib.parse

from telegram import Bot, BotCommand, BotCommandScopeChat
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, InvalidToken, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from epsilon import core, state, tools
from epsilon.ai import chat, llm
from epsilon.channels import telegram_format
from epsilon.data import conversations, library
from epsilon.routines import notify
from epsilon.settings import config
from epsilon.voice import tts, transcribe

# Este archivo solo TRADUCE entre Telegram y el núcleo (core.py): convierte lo
# que llega en un core.Message y dibuja el core.Reply. No decide nada.


# --- Límites propios de Telegram ---------------------------------------------

# Telegram no acepta mensajes de más de 4096 caracteres; dejamos margen.
MAX_CHARS = 4000

# Los bots de Telegram solo pueden descargar archivos de hasta 20 MB.
MAX_DOWNLOAD = 20 * 1024 * 1024

# Lista de comandos que se muestran en el menú de Telegram.
# Sale de tools.TOOLS. Es solo el MENÚ: cada comando tiene que existir en core.COMMANDS;
# si falta uno, aparece en la lista pero no hace nada (hay un test que lo vigila).
# Reglas de Telegram: nombres en minúscula, sin "/", hasta 32 caracteres
# (letras, números y guion bajo); descripción de 1 a 256 caracteres.
COMMANDS = [BotCommand(tool.name, tool.menu) for tool in tools.TOOLS if tool.menu]


# --- Configuración -----------------------------------------------------------


def read_config(env) -> tuple[str, int]:
    """Devuelve (token, id autorizado) o lanza ValueError diciendo qué falta.

    Se llama al arrancar, NO al importar: así, si Telegram no está configurado,
    el resto de Epsilon puede arrancar igual."""
    token = env.get("TELEGRAM_TOKEN")
    allowed = env.get("ALLOWED_USER_ID")

    # Preferimos no arrancar antes que arrancar sin saber a quién obedecer.
    if not token:
        raise ValueError("Falta TELEGRAM_TOKEN en el .env")
    if not allowed or not allowed.strip().isdigit():
        raise ValueError("Falta ALLOWED_USER_ID en el .env (tiene que ser un número)")

    return token, int(allowed)


def is_configured(env) -> bool:
    """¿Está completa la configuración de Telegram? Así main decide si lo arranca."""
    try:
        read_config(env)
    except ValueError:
        return False
    return True


def explain(error: BaseException) -> str | None:
    """Mensaje claro para un error conocido (None si no lo conocemos).

    Importante: el texto original de InvalidToken trae el token entero, así que
    nunca se muestra; acá lo reemplazamos por uno propio."""
    if isinstance(error, InvalidToken):
        return "Telegram rechazó el token. Revisá TELEGRAM_TOKEN en la configuración (o en el .env)."
    if isinstance(error, NetworkError):  # incluye TimedOut
        return "No pude conectar con Telegram. Revisá tu conexión a internet."
    return None


async def _bot_name(token: str) -> str:
    async with Bot(token) as bot:
        return (await bot.get_me()).username


def check(token: str) -> str:
    """Comprueba el token con getMe (gratis, no usa el modelo).

    Devuelve un texto, o lanza ValueError con un mensaje claro (sin el token)."""
    if not token:
        raise ValueError("Falta el token de Telegram. Cargalo en la configuración.")
    try:
        username = asyncio.run(_bot_name(token))
    except InvalidToken:
        # "from None": que el error original (con el token adentro) no viaje.
        raise ValueError(
            "Telegram rechazó el token. Revisá que esté bien copiado."
        ) from None
    except NetworkError:
        raise ValueError(
            "No pude conectar con Telegram. Revisá tu conexión a internet."
        ) from None
    return f"Conectado como @{username}."


# --- Traducción hacia Telegram -----------------------------------------------


def trim(text: str) -> str:
    """Recorta al largo que acepta Telegram (y nunca devuelve texto vacío)."""
    return text[:MAX_CHARS] or "(vacío)"


def keyboard(actions: list[core.Action], stacked: bool = False) -> InlineKeyboardMarkup | None:
    """Convierte las acciones del núcleo en botones: todos en una fila, o uno por fila (un menú)."""
    if not actions:
        return None
    if stacked:
        return InlineKeyboardMarkup([[InlineKeyboardButton(a.label, callback_data=a.id)] for a in actions])
    playback = [InlineKeyboardButton(a.label, callback_data=a.id) for a in actions if not a.id.startswith("volumen:")]
    vols = [InlineKeyboardButton(a.label, callback_data=a.id) for a in actions if a.id.startswith("volumen:")]
    rows = []
    if playback:
        rows.append(playback)
    if vols:
        rows.append(vols)
    return InlineKeyboardMarkup(rows or [[InlineKeyboardButton(a.label, callback_data=a.id)] for a in actions])


def _photo_data(art_url: str):
    """Devuelve bytes o URL si hay una imagen utilizable, o None."""
    if not art_url:
        return None
    if art_url.startswith("file://"):
        try:
            path_str = urllib.parse.unquote(urllib.parse.urlsplit(art_url).path)
            p = Path(path_str)
            if p.is_file():
                return p.read_bytes()
        except Exception:
            return None
    elif art_url.startswith(("http://", "https://")):
        return art_url
    return None


def _media_caption(media: dict) -> str:
    title = media.get("title") or "Sin título"
    artist = media.get("artist")
    player = media.get("player") or ""
    status = media.get("status")
    icon = "▶️" if status == "Playing" else ("⏸️" if status == "Paused" else "⏹️")
    lines = [f"{icon} <b>{html.escape(title)}</b>"]
    if artist:
        lines.append(f"👤 <i>{html.escape(artist)}</i>")
    if player:
        lines.append(f"📻 <i>{html.escape(player)}</i>")
    return "\n".join(lines)


async def send_one(send, chunk: str, markup):
    """Manda UN mensaje ya armado (HTML). Si Telegram rechaza el formato («can't parse entities») se reenvía el mismo
    mensaje en texto plano: una respuesta nunca se pierde por un error de formato. Cualquier otro error sigue de largo."""
    try:
        return await send(chunk, parse_mode=ParseMode.HTML, reply_markup=markup)
    except BadRequest as e:
        if "parse" not in str(e).lower() and "entities" not in str(e).lower():
            raise
        print(f"Telegram no aceptó el formato; lo mando como texto plano: {config.redact(str(e))}")
        return await send(trim(telegram_format.plain_of(chunk)), reply_markup=markup)


async def deliver(text: str, first, rest, markup):
    """Muestra la respuesta del núcleo (markdown) como uno o más mensajes: tablas, negritas y listas bien dibujadas, y lo
    largo repartido sin cortar nada. El primero sale por `first` (responder o editar), los demás por `rest`; los botones
    van en el último, que es el que se devuelve (su id sirve para anotar el siguiente toque)."""
    chunks = telegram_format.to_html(text)
    sent = None
    for index, chunk in enumerate(chunks):
        sent = await send_one(first if index == 0 else rest, chunk, markup if index == len(chunks) - 1 else None)
    return sent


# Mientras el núcleo trabaja: Telegram apaga el «escribiendo…» a los 5 segundos, así que se renueva; y si la
# espera se alarga aparece un mensaje de progreso (editado en el lugar, borrado al terminar). Segundos.
# Cuánto se espera antes de mostrar el mensaje de progreso (TELEGRAM_PROGRESS_AFTER) y cada cuánto se renueva
# «escribiendo…» (TELEGRAM_TYPING_EVERY) son ajustes: se leen al usarlos.
EDIT_EVERY = 1.0  # Telegram limita las ediciones: como mucho una por segundo en un chat
TICK = 0.5  # cada cuánto el vigilante mira si hay algo para mostrar


def progress_text(event: dict) -> str | None:
    """Lo que dice el mensaje de progreso para un paso del turno (None: no cambia). Mismos eventos que la web."""
    if event.get("type") == "model":
        step = event.get("step", 1)
        return "🤔 Pensando…" if step <= 1 else f"🤔 Consultando a la IA (paso {step})…"
    if event.get("type") == "tool":
        name, arg = event.get("name", ""), event.get("arg", "")
        if event.get("state") == "running":
            return f"🔧 Usando {name}{': ' + arg if arg else ''}…"
        if event.get("state") == "blocked":
            return f"🚫 {name}: no disponible"
    return None


async def watch(bot, chat_id: int, events: "asyncio.Queue[dict]") -> None:
    """Corre junto al núcleo hasta que lo cancelen: renueva «escribiendo…» y, pasado TELEGRAM_PROGRESS_AFTER, muestra un
    mensaje con el paso en curso. Dibujar nunca puede romper la respuesta: lo que falle se avisa y se sigue."""
    clock = time.monotonic
    start = last_typing = last_edit = clock()
    wanted, shown, message_id = "⏳ Trabajando…", None, None
    typing_every, progress_after = config.number("TELEGRAM_TYPING_EVERY"), config.number("TELEGRAM_PROGRESS_AFTER")
    try:
        while True:
            try:
                if (text := progress_text(await asyncio.wait_for(events.get(), TICK))) is not None:
                    wanted = text
            except asyncio.TimeoutError:
                pass
            now = clock()
            try:
                if now - last_typing >= typing_every:
                    last_typing = now
                    await bot.send_chat_action(chat_id, ChatAction.TYPING)
                if now - start >= progress_after and wanted != shown and now - last_edit >= EDIT_EVERY:
                    last_edit, shown = now, wanted
                    if message_id is None:
                        # Si la respuesta llega mientras viaja, el envío termina igual y hay que borrarlo.
                        sending = asyncio.ensure_future(bot.send_message(chat_id, trim(config.redact(wanted))))
                        try:
                            message_id = (await asyncio.shield(sending)).message_id
                        except asyncio.CancelledError:
                            message_id = (await asyncio.gather(sending, return_exceptions=True))[0]
                            message_id = getattr(message_id, "message_id", None)
                            raise
                    else:
                        await bot.edit_message_text(trim(config.redact(wanted)), chat_id=chat_id, message_id=message_id)
            except Exception as e:
                print(f"No pude mostrar el progreso en Telegram: {config.redact(repr(e))}")
    finally:
        if message_id is not None:
            try:
                await bot.delete_message(chat_id, message_id)
            except Exception as e:
                print(f"No pude borrar el mensaje de progreso: {config.redact(repr(e))}")


async def ask_core(chat_id: int, context: ContextTypes.DEFAULT_TYPE, msg: core.Message):
    """Le pasa el mensaje al núcleo sin trabar el bot mientras espera al modelo, mostrando que sigue vivo."""
    # "escribiendo…" mientras tarda (reemplaza los viejos avisos «Analizando…»).
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()
    # handle() es síncrona (puede llamar a Gemini): la corremos en otro hilo, que avisa sus pasos a este bucle.
    work = asyncio.to_thread(core.handle, msg, lambda event: loop.call_soon_threadsafe(events.put_nowait, event))
    watcher = asyncio.create_task(watch(context.bot, chat_id, events))
    try:
        return await work
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)  # espera a que borre el mensaje de progreso


# --- Handlers ----------------------------------------------------------------
# Cada uno hace lo mismo: armar un core.Message, pedir la respuesta y mostrarla.


def session_id(chat_id: int) -> str:
    return f"telegram:{chat_id}"


def ai_conversation() -> str | None:
    """La conversación de Telegram abierta si es de modo IA (/chat), o None. Es la que el núcleo necesita para
    hablar con la IA: sin ella, un texto que no es comando muestra la ayuda. Si el disco falla, el mensaje
    sigue como uno normal en vez de romper el bot."""
    try:
        found = conversations.current("telegram")
        return found if found and conversations.mode(found) == "ai" else None
    except Exception as e:
        print(f"No pude buscar la charla con IA de Telegram: {config.redact(repr(e))}")
        return None


def new_message(chat_id: int, **fields) -> core.Message:
    """Todo lo que Telegram le manda al núcleo sale de acá, así la charla con IA abierta viaja siempre
    (texto, archivos, voz y botones: una pregunta de permiso sin ella queda «vencida»)."""
    return core.Message(session_id=session_id(chat_id), conversation_id=ai_conversation(), **fields)


# --- Lo que se habla por acá queda en la conversación de Telegram (la ve la web) ----------------
# Una sola conversación abierta (conversations.for_channel); /nuevo la cierra y arranca otra.
# Anotarla nunca puede romper el bot: si el disco falla, se avisa en la terminal y sigue.

# Mensaje del bot en Telegram -> (conversación, id del mensaje anotado), para saber en qué
# mensaje se tocó un botón. En memoria y acotado: tras un reinicio, el toque no se anota.
MAX_LOGGED = 200
LOGGED: OrderedDict[tuple[int, int], tuple[str, str]] = OrderedDict()


def log_turn(chat_id: int, said: str | None, reply: core.Reply, sent_message_id: int | None) -> None:
    """Anota lo que dijiste (si hay) y lo que respondió el bot."""
    try:
        conversation_id = conversations.for_channel("telegram")
        if said is not None:
            conversations.add_message(conversation_id, "user", said)
        message_id = conversations.add_message(
            conversation_id,
            "bot",
            reply.text,
            actions=[asdict(a) for a in reply.actions],
            artifacts=[asdict(a) for a in reply.artifacts],
            ai=reply.ai,
        )
        if sent_message_id is not None:
            LOGGED[(chat_id, sent_message_id)] = (conversation_id, message_id)
            while len(LOGGED) > MAX_LOGGED:
                LOGGED.popitem(last=False)
    except Exception as e:
        print(f"No pude anotar la conversación de Telegram: {config.redact(repr(e))}")


def log_action(chat_id: int, sent_message_id: int, action_id: str) -> None:
    """Anota qué botón se tocó, en el mensaje que lo tenía."""
    found = LOGGED.get((chat_id, sent_message_id))
    if not found:
        return
    try:
        conversations.add_action(found[0], found[1], action_id)
    except Exception as e:
        print(f"No pude anotar la conversación de Telegram: {config.redact(repr(e))}")


async def send_files(context: ContextTypes.DEFAULT_TYPE, chat_id: int, files: list[str]) -> None:
    """Manda como documento (el original, sin comprimir) cada archivo que el núcleo pidió enviar.
    Las rutas se vuelven a validar con la biblioteca: este canal no confía en una ruta solo porque venga del núcleo."""
    for rel in files:
        try:
            path = library.resolve(rel)
            await context.bot.send_document(chat_id, path.read_bytes(), filename=path.name)
        except Exception as e:
            print(f"No pude enviar un archivo por Telegram: {config.redact(repr(e))}")
            await context.bot.send_message(chat_id, f"No pude enviar {rel}.")


def should_speak(said: str | None) -> bool:
    """¿Corresponde responder con una nota de voz sintetizada?"""
    if not config.enabled("TTS_ENABLED"):
        return False
    mode = config.value("TTS_MODE").strip().lower()
    if mode == "solo_voz":
        return bool(said and "🎙" in said)
    return True


async def send_voice_reply(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str) -> None:
    """Sintetiza la respuesta a audio y la manda como nota de voz."""
    send_fn = getattr(context.bot, "send_voice", None)
    if not send_fn:
        return
    try:
        audio_path = await asyncio.to_thread(tts.synthesize, text)
        try:
            with open(audio_path, "rb") as f:
                await send_fn(chat_id=chat_id, voice=f)
        finally:
            audio_path.unlink(missing_ok=True)
    except Exception as e:
        print(f"No pude enviar la nota de voz en Telegram: {config.redact(repr(e))}")


async def respond(message, context, chat_id: int, msg: core.Message, said: str | None) -> None:
    """Le pasa el mensaje al núcleo, muestra la respuesta y la anota (con lo que dijiste)."""
    reply = await ask_core(chat_id, context, msg)
    if should_speak(said) and reply.text:
        await send_voice_reply(context, chat_id, reply.text)
    markup = keyboard(reply.actions, reply.stacked)
    sent = None
    if reply.media and isinstance(reply.media, dict):
        photo = _photo_data(reply.media.get("art_url") or "")
        bot = getattr(context, "bot", None)
        if photo is not None and bot is not None and hasattr(bot, "send_photo"):
            try:
                caption = _media_caption(reply.media)
                sent = await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
                )
            except Exception as e:
                print(f"No pude enviar la carátula en Telegram: {config.redact(repr(e))}")
                sent = None
    if sent is None:
        sent = await deliver(reply.text, message.reply_text, message.reply_text, markup)
    log_turn(chat_id, said, reply, getattr(sent, "message_id", None))
    await send_files(context, chat_id, reply.files)


async def on_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/nuevo: la próxima vez que hables, empieza otra conversación de Telegram. Telegram no avisa
    cuando borrás el chat, así que este es el modo de empezar de cero. Lo anterior queda guardado."""
    conversations.close_channel("telegram")
    await update.message.reply_text("Listo: la conversación de Telegram empieza de cero. Lo anterior queda guardado.")


async def on_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/chat: abre una charla con la IA. Lo que escribas sin «/» va a la IA con lo que hablaron; los comandos
    siguen sin usar modelo. /nuevo la cierra. Lo anterior queda guardado."""
    model = llm.model_for("chat")
    if cid := ai_conversation():
        text = f"Ya estás en una charla con IA (modelo {model}). /nuevo para empezar de cero."
    else:
        cid = conversations.start_channel("telegram", "ai", chat.default_limit())
        status = chat.status(cid)
        text = (
            f"🤖 Charla con IA abierta.\nModelo: {model}\n"
            f"Límite: US$ {status['limit']:.2f} · la IA ve los últimos {chat.max_turns(cid)} intercambios.\n"
            "Lo que escribas sin «/» va a la IA; los comandos siguen sin usarla. /nuevo para terminar."
        )
        if status["reason"]:
            text += f"\n\n⚠️ {status['reason']}"
        try:
            conversations.add_message(cid, "bot", text)  # así la charla no queda vacía en la web
        except Exception as e:
            print(f"No pude anotar la conversación de Telegram: {config.redact(repr(e))}")
    await update.message.reply_text(trim(text))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Texto y comandos: el núcleo decide qué son."""
    chat_id = update.effective_chat.id
    msg = new_message(chat_id, text=update.message.text)
    await respond(update.message, context, chat_id, msg, said=update.message.text)


async def download_attachment(update: Update, number: int = 0) -> core.Attachment | None:
    """Baja la foto o el documento del mensaje. None si pesa de más (ya se avisó). `number` distingue
    el nombre de las fotos de un álbum, que llegan en el mismo segundo."""
    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        mime = "image/jpeg"
        # Las fotos no traen nombre, así que le armamos uno con la hora.
        name = f"foto_{datetime.now():%H%M%S}{f'_{number}' if number else ''}.jpg"
    else:
        doc = update.message.document
        # Si pesa más de lo que Telegram deja bajar, avisamos antes de intentar.
        if doc.file_size and doc.file_size > MAX_DOWNLOAD:
            await update.message.reply_text(
                "Ese archivo pesa más de 20 MB; no puedo bajarlo."
            )
            return None
        file = await doc.get_file()
        mime = doc.mime_type or "image/jpeg"
        name = doc.file_name or "archivo"

    data = bytes(await file.download_as_bytearray())
    return core.Attachment(data=data, name=name, mime=mime)


# Un álbum de Telegram llega como mensajes sueltos con el mismo `media_group_id` (hasta 10 por álbum, y el pie
# solo en uno). Los juntamos en memoria y, cuando pasa ALBUM_WAIT s sin que llegue otro, van al núcleo como UN
# mensaje con todos los archivos (así «gastos: fecha» sobre 10 fotos da un solo CSV).
ALBUM_WAIT = 1.5
ALBUMS: dict[tuple[int, str], "Album"] = {}


@dataclass
class Album:
    message: object  # el primer mensaje: en él se contesta
    context: object
    attachments: list = field(default_factory=list)
    caption: str = ""
    task: asyncio.Task | None = None


def add_to_album(update: Update, context, chat_id: int, attachment: core.Attachment) -> None:
    key = (chat_id, str(update.message.media_group_id))
    album = ALBUMS.get(key)
    if album is None:
        album = ALBUMS[key] = Album(update.message, context)
    if len(album.attachments) < core.MAX_CSV_IMAGES:
        album.attachments.append(attachment)
    album.caption = album.caption or (update.message.caption or "")
    if album.task:
        album.task.cancel()  # llegó otra pieza: el reloj empieza de nuevo
    album.task = asyncio.get_running_loop().create_task(flush_album(key))


async def flush_album(key: tuple[int, str]) -> None:
    await asyncio.sleep(ALBUM_WAIT)
    album = ALBUMS.pop(key, None)
    if album is None:
        return
    chat_id = key[0]
    names = "\n".join(a.name for a in album.attachments)
    msg = new_message(chat_id, text=album.caption, attachments=album.attachments)
    try:
        await respond(album.message, album.context, chat_id, msg, said=f"{names}\n{album.caption}" if album.caption.strip() else names)
    except Exception as e:  # una tarea suelta no tiene quién vea su error
        print(f"Error con el álbum de Telegram: {config.redact(repr(e))}")


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foto, imagen o PDF. El texto que lo acompaña (caption) viaja como texto. Los de un álbum se juntan."""
    chat_id = update.effective_chat.id
    group = getattr(update.message, "media_group_id", None)
    number = 0
    if group:
        album = ALBUMS.get((chat_id, str(group)))
        number = (len(album.attachments) if album else 0) + 1
    attachment = await download_attachment(update, number)
    if attachment is None:
        return
    if group:
        add_to_album(update, context, chat_id, attachment)
        return

    caption = update.message.caption or ""
    msg = new_message(chat_id, text=caption, attachment=attachment)
    await respond(update.message, context, chat_id, msg, said=f"{attachment.name}\n{caption}" if caption.strip() else attachment.name)


def _audio_suffix(media) -> str:
    """Extensión del archivo temporal: la del nombre si es corta y simple, si no .ogg (las
    notas de voz de Telegram). Nunca sale texto raro de acá: solo letras y números."""
    # getattr: una nota de voz (Voice) no tiene file_name; un audio o un documento, sí.
    suffix = Path(getattr(media, "file_name", None) or "").suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else ".ogg"


# Notas de voz esperando «Ejecutar» o «Cancelar»: token -> (chat, texto). Viven en memoria (se
# pierden al reiniciar); el texto no viaja en el botón porque Telegram deja
# apenas 64 bytes. Hasta MAX_PENDING_VOICE en total: lo más viejo se descarta.
VOICE_PREFIX = "voz:"
MAX_PENDING_VOICE = 20
PENDING_VOICE: dict[str, tuple[int, str]] = {}
_voice_counter = itertools.count(1)


def remember_voice(chat_id: int, text: str) -> str:
    token = str(next(_voice_counter))
    PENDING_VOICE[token] = (chat_id, text)
    while len(PENDING_VOICE) > MAX_PENDING_VOICE:
        PENDING_VOICE.pop(next(iter(PENDING_VOICE)))
    return token


async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nota de voz o audio: se transcribe en local y el texto entra al núcleo como si lo
    hubieras escrito. Primero se muestra lo entendido y el pedido se ejecuta directo, sin
    pedir confirmación (sería lo mismo que escribir)."""
    chat_id = update.effective_chat.id
    media = update.message.voice or update.message.audio or update.message.document

    # Apagada desde /menu o /config (vale para todos los canales): ni se descarga el audio.
    if not config.enabled("VOICE_ENABLED"):
        await update.message.reply_text(transcribe.VOICE_OFF)
        return

    # Si pesa más de lo que Telegram deja bajar, avisamos antes de intentar.
    if media.file_size and media.file_size > MAX_DOWNLOAD:
        await update.message.reply_text("Ese audio pesa más de 20 MB; no puedo bajarlo.")
        return

    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)  # «escribiendo…» mientras tarda
    path = None
    try:
        file = await media.get_file()
        data = bytes(await file.download_as_bytearray())
        # Un archivo temporal FUERA de DATA_DIR (el audio no se guarda) que se borra siempre.
        with tempfile.NamedTemporaryFile(suffix=_audio_suffix(media), delete=False) as temp:
            temp.write(data)
            path = Path(temp.name)
        # Transcribir tarda segundos: en otro hilo, para no trabar al bot.
        text = await asyncio.to_thread(transcribe.audio_to_text, path)
    except transcribe.VoiceError as e:
        await update.message.reply_text(str(e))  # ya viene redactado para el usuario
        return
    except Exception as e:
        print(f"Error con un audio: {config.redact(repr(e))}")  # el detalle, a la terminal
        await update.message.reply_text("No pude procesar el audio.")
        return
    finally:
        if path:
            path.unlink(missing_ok=True)

    heard = f"🎙 «{text[: MAX_CHARS - 40]}»"
    # Con la confirmación prendida (/menu o /config), no se ejecuta hasta que toques Ejecutar.
    if config.enabled("VOICE_CONFIRM"):
        token = remember_voice(chat_id, text)
        await update.message.reply_text(
            f"{heard}\n¿Lo ejecuto?",
            reply_markup=InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("Ejecutar", callback_data=f"{VOICE_PREFIX}si:{token}"),
                    InlineKeyboardButton("Cancelar", callback_data=f"{VOICE_PREFIX}no:{token}"),
                ]]
            ),
        )
        return

    # Lo entendido va en un mensaje aparte, para ver qué escuchó; después, la respuesta al pedido.
    await update.message.reply_text(heard)
    await run_voice_text(update.message, context, chat_id, text)


async def run_voice_text(message, context, chat_id: int, text: str):
    """El texto de una nota de voz entra al núcleo como si lo hubieras escrito."""
    msg = new_message(chat_id, text=text)
    await respond(message, context, chat_id, msg, said=f"🎙 {text}")


async def on_voice_answer(query, context, chat_id: int):
    """«Ejecutar» o «Cancelar» sobre una nota de voz que esperaba confirmación."""
    parts = query.data.split(":")  # voz:si:<token>
    if len(parts) != 3 or parts[1] not in ("si", "no"):
        return
    answer, token = parts[1], parts[2]
    pending = PENDING_VOICE.get(token)
    if not pending or pending[0] != chat_id:
        # Pasó mucho tiempo, o el bot se reinició: el texto ya no está.
        await query.edit_message_text("Ese audio ya no está; mandalo de nuevo.")
        return
    del PENDING_VOICE[token]
    heard = f"🎙 «{pending[1][: MAX_CHARS - 40]}»"
    if answer == "no":
        await query.edit_message_text(f"{heard}\nCancelado.")
        return
    await query.edit_message_text(heard)  # queda lo entendido, sin botones
    await run_voice_text(query.message, context, chat_id, pending[1])


def make_on_button(allowed_user_id: int):
    async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Botones: el id del botón viaja al núcleo como acción."""
        query = update.callback_query
        await query.answer()  # le avisa a Telegram que recibimos el toque

        # Los botones no pasan por el filtro ONLY_ME, así que validamos acá.
        if query.from_user.id != allowed_user_id:
            return

        chat_id = query.message.chat.id
        if query.data.startswith(VOICE_PREFIX):
            await on_voice_answer(query, context, chat_id)
            return
        msg = new_message(chat_id, action=query.data)
        reply = await ask_core(chat_id, context, msg)
        log_action(chat_id, query.message.message_id, query.data)
        markup = keyboard(reply.actions, reply.stacked)

        is_photo_msg = bool(getattr(query.message, "photo", None))
        if is_photo_msg and reply.media and hasattr(query, "edit_message_caption"):
            try:
                caption = _media_caption(reply.media)
                sent = await query.edit_message_caption(caption=caption, parse_mode=ParseMode.HTML, reply_markup=markup)
                log_turn(chat_id, None, reply, getattr(sent, "message_id", None) or query.message.message_id)
                await send_files(context, chat_id, reply.files)
                return
            except BadRequest as e:
                if "not modified" in str(e).lower():
                    return

        # El primer trozo edita el mensaje del botón; si la respuesta es larga, el resto va como mensajes nuevos.
        try:
            sent = await deliver(reply.text, query.edit_message_text, query.message.reply_text, markup)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
            sent = query.message
        # La respuesta nueva queda anotada, en el mensaje que lleva los botones, para el siguiente toque.
        log_turn(chat_id, None, reply, getattr(sent, "message_id", None) or query.message.message_id)
        await send_files(context, chat_id, reply.files)

    return on_button


# --- Arranque ----------------------------------------------------------------


def build_app(token: str, allowed_user_id: int) -> Application:
    async def post_init(app: Application):
        """Se ejecuta una sola vez, cuando el bot arranca."""
        # PTB llama a esto recién después de conectarse con Telegram (getMe),
        # así que acá sí podemos decir que está activo.
        state.set("telegram", "running", "Conectado.")
        _runtime["app"], _runtime["loop"] = app, asyncio.get_running_loop()
        notify.set_sink("telegram", make_notifier(allowed_user_id))  # los avisos de las rutinas también llegan acá
        if _runtime["stop_requested"]:  # lo apagaron mientras conectaba
            app.stop_running()
            return
        try:
            await app.bot.set_my_commands(
                COMMANDS,
                # scope = a quién se le muestra el menú. Acá solo a tu chat privado
                # (en un chat privado, el chat_id es igual a tu user id).
                scope=BotCommandScopeChat(chat_id=allowed_user_id),
            )
        except Exception as e:
            # Si falla (por ejemplo, no hay ningún chat abierto con el bot todavía),
            # el bot sigue funcionando igual; solo no tendrás el menú.
            print(f"No pude configurar el menú de comandos: {e!r}")

    app = Application.builder().token(token).post_init(post_init).build()

    # Filtro que deja pasar SOLO los mensajes de tu usuario.
    # Los de cualquier otra persona se ignoran sin responder nada.
    only_me = filters.User(user_id=allowed_user_id)

    app.add_handler(CommandHandler("nuevo", on_new, filters=only_me))  # antes que el texto en general
    app.add_handler(CommandHandler("chat", on_chat, filters=only_me))
    app.add_handler(MessageHandler(filters.TEXT & only_me, on_text))
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF) & only_me,
            on_file,
        )
    )
    app.add_handler(
        MessageHandler(
            (filters.VOICE | filters.AUDIO | filters.Document.Category("audio/")) & only_me,
            on_voice,
        )
    )
    app.add_handler(CallbackQueryHandler(make_on_button(allowed_user_id)))
    return app


def make_notifier(chat_id: int):
    """Lo que las rutinas usan para avisarte por Telegram: se llama desde otro hilo, así que el mensaje se manda
    dentro del bucle del bot y se espera el resultado (si falla, `notify.send` lo anota y sigue con los demás medios)."""
    def deliver(text: str) -> None:
        loop, app = _runtime["loop"], _runtime["app"]
        if loop is None or app is None:
            raise RuntimeError("Telegram no está conectado")
        asyncio.run_coroutine_threadsafe(app.bot.send_message(chat_id, trim(config.redact(text))), loop).result(timeout=15)

    return deliver


# Para poder apagar el bot sin cortar el proceso (la página Canales): el hilo que corre el bot
# deja acá su bucle de eventos y su aplicación; stop() le pide cortar desde otro hilo.
_runtime: dict = {"app": None, "loop": None, "stop_requested": False}


def reset() -> None:
    """Borra un pedido de apagado viejo. main lo llama justo antes de prender el canal (no al
    empezar run(): un stop() que llegue mientras el hilo arranca se perdería)."""
    _runtime["stop_requested"] = False


def stop() -> None:
    """Pide que el bot deje de escuchar (run() vuelve enseguida). No espera: main espera al hilo.
    Si todavía está conectando, el pedido se cumple apenas arranca."""
    _runtime["stop_requested"] = True
    loop, app = _runtime["loop"], _runtime["app"]
    if loop is not None and app is not None:
        # stop_running tiene que correr dentro del bucle del bot, no en este hilo.
        loop.call_soon_threadsafe(app.stop_running)


def polling_options(in_main_thread: bool) -> dict:
    """Opciones de run_polling según el hilo desde el que corre."""
    # Si no hay red al arrancar, reintenta sin límite (-1) en vez de rendirse:
    # como la web sigue viva, nadie reiniciaría el servicio por nosotros.
    options = {"bootstrap_retries": -1}
    if not in_main_thread:
        # Solo el hilo principal puede manejar señales (Ctrl+C, systemd). En
        # otro hilo habría un error, y de las señales se ocupa la web.
        options["stop_signals"] = None
    return options


def run():
    try:
        token, allowed_user_id = read_config(config.environ())
    except ValueError as e:
        raise SystemExit(str(e))

    app = build_app(token, allowed_user_id)
    in_main = threading.current_thread() is threading.main_thread()
    print("Bot de Telegram en marcha.")
    try:
        app.run_polling(**polling_options(in_main))
    finally:
        _runtime.update(app=None, loop=None)  # listo para volver a prenderlo
        notify.remove_sink("telegram")


if __name__ == "__main__":
    run()
