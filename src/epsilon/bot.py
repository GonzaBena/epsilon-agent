import asyncio
from datetime import date, datetime
import os

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram import BotCommand, BotCommandScopeChat
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from epsilon import extract, notes, summarize

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

# Los bots de Telegram solo pueden descargar archivos de hasta 20 MB.
MAX_DOWNLOADS = 20 * 1024 * 1024
# Para el modelo somos más conservadores (el límite exacto de Gemini,
# chequealo en su documentación porque puede cambiar).
MAX_PER_MODEL = 15 * 1024 * 1024


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


async def save_and_confirm(update, context, name, data):
    """Guarda el archivo recibido y pregunta si se quiere resumir."""
    path = notes.guardar_archivo(name, data)
    relative = path.relative_to(notes.DATA_DIR)  # para mostrar y recordar

    # El callback_data de un botón tiene máximo 64 bytes, así que no podemos
    # meter la ruta ahí. Guardamos la ruta en memoria con un número (el id de
    # tu mensaje) y el botón lleva solo ese número.
    id_msg = update.message.message_id
    context.user_data.setdefault("archivos", {})[id_msg] = str(relative)

    botones = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Resumir", callback_data=f"resumir:{id_msg}"),
                InlineKeyboardButton("Nada más", callback_data=f"nada:{id_msg}"),
            ]
        ]
    )
    await update.message.reply_text(f"Guardado: {relative}", reply_markup=botones)


async def summarize_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botones Resumir / Nada más."""
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ALLOWED_USER_ID:
        return

    # "resumir:123" -> accion="resumir", id_txt="123"
    action, _, id_txt = query.data.partition(":")
    archives = context.user_data.get("archivos", {})
    relative = archives.get(int(id_txt))

    # Si reiniciaste el bot, esa memoria se perdió (el archivo sigue guardado).
    if relative is None:
        await query.edit_message_text(
            "Ya no recuerdo ese archivo; sigue guardado en la carpeta."
        )
        return

    if action == "nada":
        archives.pop(int(id_txt), None)
        await query.edit_message_text(f"Listo. Queda guardado: {relative}")
        return

    ruta = notes.ruta_segura(relative)
    if ruta.stat().st_size > MAX_PER_MODEL:
        await query.edit_message_text("Es demasiado grande para resumirlo.")
        return

    await query.edit_message_text("Resumiendo…")
    try:
        # De nuevo to_thread: la llamada al modelo no debe trabar el bot.
        texto = await asyncio.to_thread(summarize.summarize, ruta)
    except Exception as e:
        print(f"Error al resumir: {e!r}")  # el detalle, a la terminal
        await query.edit_message_text("No pude resumirlo.")
        return

    archives.pop(int(id_txt), None)  # ya está resuelto; sacamos el pendiente
    summarize_name = f"resumenes/{ruta.stem}"
    notes.guardar_nota(summarize_name, texto)
    await query.edit_message_text(
        f"Resumen guardado en {summarize_name}.md\n\n{texto[: MAX_CARACTERES - 100]}"
    )


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
    """Recibe una foto o archivo. Con 'nombre: campos' extrae un CSV; sin eso, lo guarda."""
    request_data = parse_request(update.message.caption)

    if update.message.photo:
        archive = await update.message.photo[-1].get_file()
        mime = "image/jpeg"
        # Las fotos no traen nombre, así que le armamos uno con la hora.
        name = f"foto_{datetime.now():%H%M%S}.jpg"
    else:
        doc = update.message.document
        # Si pesa más de lo que Telegram deja bajar, avisamos antes de intentar.
        if doc.file_size and doc.file_size > MAX_DOWNLOADS:
            await update.message.reply_text(
                "Ese archivo pesa más de 20 MB; no puedo bajarlo."
            )
            return
        archive = await doc.get_file()
        mime = doc.mime_type or "image/jpeg"
        name = doc.file_name or "archivo"

    data = bytes(await archive.download_as_bytearray())

    # Sin pedido de extracción: solo guardamos y preguntamos (esto NO usa el modelo).
    if not request_data:
        await save_and_confirm(update, context, name, data)
        return

    name_csv, fields = request_data

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
        "name": name_csv,
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
        f"{len(rows)} fila(s) para {name_csv}.csv:\n\n{preview[:MAX_CARACTERES]}",
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

# Lista de comandos que se muestran en el menú de Telegram.
# OJO: esto es solo el MENÚ. Cada comando necesita igualmente su
# CommandHandler en main(); si falta uno, aparece en la lista pero no hace nada.
# Reglas de Telegram: nombres en minúscula, sin "/", hasta 32 caracteres
# (letras, números y guion bajo); descripción de 1 a 256 caracteres.
COMANDOS = [
    BotCommand("ver", "Ver una nota (por defecto, inbox)"),
    BotCommand("buscar", "Buscar texto en todas las notas"),
]


async def post_init(app: Application):
    """Se ejecuta una sola vez, cuando el bot arranca."""
    try:
        await app.bot.set_my_commands(
            COMANDOS,
            # scope = a quién se le muestra el menú. Acá solo a tu chat privado
            # (en un chat privado, el chat_id es igual a tu user id).
            # A cualquier otra persona no le aparece nada, coherente con la whitelist.
            scope=BotCommandScopeChat(chat_id=ALLOWED_USER_ID),
        )
    except Exception as e:
        # Si falla (por ejemplo, no hay ningún chat abierto con el bot todavía),
        # el bot sigue funcionando igual; solo no tendrás el menú.
        print(f"No pude configurar el menú de comandos: {e!r}")


def main():
    # Application.builder() arma el bot con tu token.
    app = Application.builder().token(TOKEN).post_init(post_init).build()

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

    # Imágenes y PDFs (como foto o como archivo), solo de tu usuario.
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.IMAGE | filters.Document.PDF) & ONLY_ME,
            image,
        )
    )
    # Cada grupo de botones atiende solo sus propios callback_data (pattern).
    app.add_handler(CallbackQueryHandler(confirm, pattern=r"^(guardar|cancelar)$"))
    app.add_handler(CallbackQueryHandler(summarize_cb, pattern=r"^(resumir|nada):"))

    print("Bot en marcha. Ctrl+C para cortar.")
    # run_polling: el bot le pregunta a Telegram cada tanto si hay mensajes
    # nuevos. No necesitás abrir puertos ni tener servidor público.
    app.run_polling()


if __name__ == "__main__":
    main()
