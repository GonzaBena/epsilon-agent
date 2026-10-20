import asyncio
from datetime import datetime
import itertools
import os
from pathlib import Path
import re
import tempfile
import threading

from telegram import Bot, BotCommand, BotCommandScopeChat
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.error import InvalidToken, NetworkError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from epsilon import config, core, state, tools, transcribe

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
    buttons = [InlineKeyboardButton(a.label, callback_data=a.id) for a in actions]
    return InlineKeyboardMarkup([[b] for b in buttons] if stacked else [buttons])


async def ask_core(chat_id: int, context: ContextTypes.DEFAULT_TYPE, msg: core.Message):
    """Le pasa el mensaje al núcleo sin trabar el bot mientras espera al modelo."""
    # "escribiendo…" mientras tarda (reemplaza los viejos avisos «Analizando…»).
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    # handle() es síncrona (puede llamar a Gemini): la corremos en otro hilo.
    return await asyncio.to_thread(core.handle, msg)


# --- Handlers ----------------------------------------------------------------
# Cada uno hace lo mismo: armar un core.Message, pedir la respuesta y mostrarla.


def session_id(chat_id: int) -> str:
    return f"telegram:{chat_id}"


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Texto y comandos: el núcleo decide qué son."""
    chat_id = update.effective_chat.id
    msg = core.Message(text=update.message.text, session_id=session_id(chat_id))
    reply = await ask_core(chat_id, context, msg)
    await update.message.reply_text(trim(reply.text), reply_markup=keyboard(reply.actions, reply.stacked))


async def on_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foto, imagen o PDF. El texto que lo acompaña (caption) viaja como texto."""
    chat_id = update.effective_chat.id

    if update.message.photo:
        file = await update.message.photo[-1].get_file()
        mime = "image/jpeg"
        # Las fotos no traen nombre, así que le armamos uno con la hora.
        name = f"foto_{datetime.now():%H%M%S}.jpg"
    else:
        doc = update.message.document
        # Si pesa más de lo que Telegram deja bajar, avisamos antes de intentar.
        if doc.file_size and doc.file_size > MAX_DOWNLOAD:
            await update.message.reply_text(
                "Ese archivo pesa más de 20 MB; no puedo bajarlo."
            )
            return
        file = await doc.get_file()
        mime = doc.mime_type or "image/jpeg"
        name = doc.file_name or "archivo"

    data = bytes(await file.download_as_bytearray())
    msg = core.Message(
        text=update.message.caption or "",
        attachment=core.Attachment(data=data, name=name, mime=mime),
        session_id=session_id(chat_id),
    )
    reply = await ask_core(chat_id, context, msg)
    await update.message.reply_text(trim(reply.text), reply_markup=keyboard(reply.actions, reply.stacked))


def _audio_suffix(media) -> str:
    """Extensión del archivo temporal: la del nombre si es corta y simple, si no .ogg (las
    notas de voz de Telegram). Nunca sale texto raro de acá: solo letras y números."""
    # getattr: una nota de voz (Voice) no tiene file_name; un audio o un documento, sí.
    suffix = Path(getattr(media, "file_name", None) or "").suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,5}", suffix) else ".ogg"


# Notas de voz esperando «Ejecutar» o «Cancelar»: token -> (chat, texto). Viven en memoria (se
# pierden al reiniciar, como /contexto); el texto no viaja en el botón porque Telegram deja
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
    msg = core.Message(text=text, session_id=session_id(chat_id))
    reply = await ask_core(chat_id, context, msg)
    await message.reply_text(trim(reply.text), reply_markup=keyboard(reply.actions, reply.stacked))


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
        msg = core.Message(action=query.data, session_id=session_id(chat_id))
        reply = await ask_core(chat_id, context, msg)
        await query.edit_message_text(
            trim(reply.text), reply_markup=keyboard(reply.actions, reply.stacked)
        )

    return on_button


# --- Arranque ----------------------------------------------------------------


def build_app(token: str, allowed_user_id: int) -> Application:
    async def post_init(app: Application):
        """Se ejecuta una sola vez, cuando el bot arranca."""
        # PTB llama a esto recién después de conectarse con Telegram (getMe),
        # así que acá sí podemos decir que está activo.
        state.set("telegram", "running", "Conectado.")
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
        token, allowed_user_id = read_config(os.environ)
    except ValueError as e:
        raise SystemExit(str(e))

    app = build_app(token, allowed_user_id)
    in_main = threading.current_thread() is threading.main_thread()
    print("Bot de Telegram en marcha.")
    app.run_polling(**polling_options(in_main))


if __name__ == "__main__":
    run()
