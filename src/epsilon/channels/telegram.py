import asyncio
from datetime import datetime
import os

from telegram import BotCommand, BotCommandScopeChat
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from epsilon import core

# Este archivo solo TRADUCE entre Telegram y el núcleo (core.py): convierte lo
# que llega en un core.Message y dibuja el core.Reply. No decide nada.


# --- Límites propios de Telegram ---------------------------------------------

# Telegram no acepta mensajes de más de 4096 caracteres; dejamos margen.
MAX_CHARS = 4000

# Los bots de Telegram solo pueden descargar archivos de hasta 20 MB.
MAX_DOWNLOAD = 20 * 1024 * 1024

# Lista de comandos que se muestran en el menú de Telegram.
# OJO: esto es solo el MENÚ. Cada comando tiene que existir en core.COMMANDS;
# si falta uno, aparece en la lista pero no hace nada (hay un test que lo vigila).
# Reglas de Telegram: nombres en minúscula, sin "/", hasta 32 caracteres
# (letras, números y guion bajo); descripción de 1 a 256 caracteres.
COMMANDS = [
    BotCommand("ver", "Ver una nota (por defecto, inbox)"),
    BotCommand("buscar", "Buscar texto en todas las notas"),
    BotCommand("archivos", "Listar lo guardado (opcional: filtrar por nombre)"),
    BotCommand("resumir", "Resumir una nota o archivo guardado"),
    BotCommand("ticket", "Guardar la próxima imagen como ticket"),
    BotCommand("cancelar", "Cancelar el /ticket en espera"),
    BotCommand("uso", "Tokens y precio gastados (hoy y total)"),
    BotCommand("ayuda", "Cómo se usa el bot"),
]


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


# --- Traducción hacia Telegram -----------------------------------------------


def trim(text: str) -> str:
    """Recorta al largo que acepta Telegram (y nunca devuelve texto vacío)."""
    return text[:MAX_CHARS] or "(vacío)"


def keyboard(actions: list[core.Action]) -> InlineKeyboardMarkup | None:
    """Convierte las acciones del núcleo en una fila de botones."""
    if not actions:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(a.label, callback_data=a.id) for a in actions]]
    )


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
    await update.message.reply_text(trim(reply.text), reply_markup=keyboard(reply.actions))


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
    await update.message.reply_text(trim(reply.text), reply_markup=keyboard(reply.actions))


def make_on_button(allowed_user_id: int):
    async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Botones: el id del botón viaja al núcleo como acción."""
        query = update.callback_query
        await query.answer()  # le avisa a Telegram que recibimos el toque

        # Los botones no pasan por el filtro ONLY_ME, así que validamos acá.
        if query.from_user.id != allowed_user_id:
            return

        chat_id = query.message.chat.id
        msg = core.Message(action=query.data, session_id=session_id(chat_id))
        reply = await ask_core(chat_id, context, msg)
        await query.edit_message_text(
            trim(reply.text), reply_markup=keyboard(reply.actions)
        )

    return on_button


# --- Arranque ----------------------------------------------------------------


def build_app(token: str, allowed_user_id: int) -> Application:
    async def post_init(app: Application):
        """Se ejecuta una sola vez, cuando el bot arranca."""
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
    app.add_handler(CallbackQueryHandler(make_on_button(allowed_user_id)))
    return app


def run():
    try:
        token, allowed_user_id = read_config(os.environ)
    except ValueError as e:
        raise SystemExit(str(e))

    app = build_app(token, allowed_user_id)
    print("Bot en marcha. Ctrl+C para cortar.")
    app.run_polling()


if __name__ == "__main__":
    run()
