import asyncio
from datetime import date
import os

from dotenv import load_dotenv
from telegram import Update, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import epsilon.extract as extract
import epsilon.notes as notes

# IMPORTANTE: cargamos el .env ANTES de importar notas.
# notes.py lee DATA_DIR apenas se importa; si el .env no estuviera
# cargado todavía, usaría la carpeta por defecto en vez de la tuya.
load_dotenv()


# --- Configuración -----------------------------------------------------------

TOKEN = os.environ.get("TELEGRAM_TOKEN")
ALLOWED = os.environ.get("ALLOWED_USER_ID")

# Si falta alguno, cortamos con un mensaje claro. Preferimos que el bot
# NO arranque antes que arrancar sin saber a quién obedecer.
if not TOKEN or not ALLOWED:
    raise SystemExit("Faltan TELEGRAM_TOKEN o ALLOWED_USER_ID en el .env")

# El ID viene como texto; lo pasamos a número para poder compararlo.
ALLOWED_USER_ID = int(ALLOWED)

# Filtro que deja pasar SOLO los mensajes de tu usuario.
# Los mensajes de cualquier otra persona se ignoran sin responder nada.
ONLY_ME = filters.User(user_id=ALLOWED_USER_ID)

# Telegram no acepta mensajes de más de 4096 caracteres; dejamos margen.
MAX_CARACTERES = 4000


# --- Handlers (qué hacer ante cada mensaje) ----------------------------------
# Son "async" porque python-telegram-bot trabaja de forma asíncrona:
# "await" significa "esperá a que Telegram termine de enviar esto".


async def save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mensajes que empiezan con '+': se guardan en la nota 'inbox'."""
    # update.message.text es el texto completo; sacamos el "+" y los espacios.
    text = update.message.text.lstrip("+").strip()

    if not text:
        await update.message.reply_text("Escribí algo después del +")
        return

    # Anteponemos la fecha para saber cuándo anotaste cada cosa.
    line = f"- {date.today().isoformat()} {text}"
    answer = notes.save_note("inbox", line)
    await update.message.reply_text(answer)


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ver [nota]: muestra una nota (por defecto 'inbox')."""
    # context.args es la lista de palabras que van después del comando.
    name = context.args[0] if context.args else "inbox"

    try:
        contenido = notes.read_note(name)
    except ValueError:
        # ruta_segura lanza ValueError si el nombre intenta salirse de la carpeta.
        await update.message.reply_text("Nombre de nota no válido")
        return

    # Recortamos por si la nota es más larga de lo que Telegram permite.
    await update.message.reply_text(contenido[:MAX_CARACTERES] or "(vacía)")


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/buscar texto: busca en todas las notas."""
    text = " ".join(context.args)  # unimos las palabras en un solo texto

    if not text:
        await update.message.reply_text("Uso: /buscar palabra")
        return

    results = notes.search_note(text)
    await update.message.reply_text("\n".join(results)[:MAX_CARACTERES])


async def other(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cualquier otro texto: recordamos cómo se usa el bot."""
    await update.message.reply_text(
        "Usá:\n+ texto → guardar\n/ver [nota] → leer\n/buscar palabra → buscar"
    )


def parse_request(caption: str | None):
    """Convierte 'gastos: fecha, total' en ('gastos', ['fecha', 'total'])."""
    # Sin texto o sin ":" no podemos saber qué hay que extraer.
    if not caption or ":" not in caption:
        return None

    # split(":", 1) corta solo en el PRIMER ":", así los campos pueden
    # tener ":" adentro sin romper nada.
    name, txt_fields = caption.split(":", 1)
    name = name.strip()
    fields = [c.strip() for c in txt_fields.split(",") if c.strip()]

    if not name or not fields:
        return None
    return name, fields


async def image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe una foto (o imagen enviada como archivo) y arma el preview."""
    request_msg = parse_request(update.message.caption)
    if not request_msg:
        await update.message.reply_text(
            "Mandá la imagen con un texto así:\ngastos: fecha, proveedor, total"
        )
        return
    name, fields = request_msg

    # Telegram entrega las imágenes de dos formas distintas:
    if update.message.photo:
        # Como "foto": viene comprimida, en varios tamaños; [-1] es el más grande.
        archive = await update.message.photo[-1].get_file()
        mime = "image/jpeg"
    else:
        # Como "archivo": llega sin comprimir (mejor para letra chica).
        archive = await update.message.document.get_file()
        mime = update.message.document.mime_type or "image/jpeg"

    # Descargamos los bytes de la imagen a memoria (no se guarda en disco).
    data = bytes(await archive.download_as_bytearray())

    await update.message.reply_text("Analizando…")

    try:
        # La llamada a Gemini es "bloqueante" (el programa espera). Con
        # to_thread la corremos en otro hilo para que el bot siga vivo
        # mientras tanto.
        rows = await asyncio.to_thread(extract.img_to_rows, data, mime, fields)
    except Exception as e:
        # El detalle va a la terminal; a Telegram solo un mensaje corto.
        print(f"Error al extraer: {e!r}")
        await update.message.reply_text("No pude procesar la imagen.")
        return

    if not rows:
        await update.message.reply_text("No encontré datos en la imagen.")
        return

    # Guardamos el resultado "en espera" hasta que confirmes. Si mandás otra
    # imagen antes de confirmar, la nueva reemplaza a esta. Y si reiniciás
    # el bot, se pierde (está en memoria).
    context.user_data["pendiente"] = {
        "name": name,
        "fields": fields,
        "rows": rows,
    }

    # Preview: las primeras 5 filas, con los valores separados por " | ".
    preview = "\n".join(" | ".join(f.values()) for f in rows[:5])
    buttons = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Guardar", callback_data="guardar"),
                InlineKeyboardButton("Cancelar", callback_data="cancelar"),
            ]
        ]
    )
    await update.message.reply_text(
        f"{len(rows)} fila(s) para {name}.csv:\n\n{preview[:MAX_CARACTERES]}",
        reply_markup=buttons,
    )


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones Guardar / Cancelar."""
    query = update.callback_query
    await query.answer()  # le avisa a Telegram que recibimos el toque

    # Los botones no pasan por el filtro SOLO_YO, así que validamos acá.
    if query.from_user.id != ALLOWED_USER_ID:
        return

    # pop saca el pendiente y lo borra: no se puede guardar dos veces.
    pending = context.user_data.pop("pendiente", None)
    if not pending:
        await query.edit_message_text("No hay nada pendiente.")
        return

    if query.data == "cancelar":
        await query.edit_message_text("Cancelado.")
        return

    try:
        result = notes.save_csv(pending["name"], pending["fields"], pending["rows"])
    except ValueError as e:
        # Pasa si el nombre intenta salirse de la carpeta, o si el CSV
        # ya existe con otras columnas.
        result = f"No se pudo guardar: {e}"

    await query.edit_message_text(result)


# --- Arranque ----------------------------------------------------------------


def main():
    # Application.builder() arma el bot con tu token.
    app = Application.builder().token(TOKEN).build()

    # Registramos los handlers. Cada uno lleva el filtro SOLO_YO.
    # El orden importa: se usa el primero que coincida.
    app.add_handler(
        MessageHandler(filters.TEXT & filters.Regex(r"^\+") & ONLY_ME, save)
    )
    app.add_handler(CommandHandler("ver", watch, filters=ONLY_ME))
    app.add_handler(CommandHandler("buscar", search, filters=ONLY_ME))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ONLY_ME, other))
    # Imágenes: como foto o como archivo, solo de tu usuario.
    app.add_handler(
        MessageHandler((filters.PHOTO | filters.Document.IMAGE) & ONLY_ME, image)
    )
    # Botones de confirmación (los valida la propia función).
    app.add_handler(CallbackQueryHandler(confirm))

    print("Bot en marcha. Ctrl+C para cortar.")
    # run_polling: el bot le pregunta a Telegram cada tanto si hay mensajes
    # nuevos. No necesitás abrir puertos ni tener servidor público.
    app.run_polling()


if __name__ == "__main__":
    main()
