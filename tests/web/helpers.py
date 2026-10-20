from pathlib import Path
import re

from epsilon import conversations, notes, state
from epsilon.channels import web

CFG_SECRET = "AIzaSyD-ejemplo_de_clave_larga_QzXw"
CFG_TOKEN = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"
PAGES = ["index.html", "config.html", "canales.html", "uso.html"]
BRAND = "Ε"  # Ε  GREEK CAPITAL LETTER EPSILON
LOWERCASE = "ε"  # ε  (la minúscula: la letra correcta es la mayúscula)


def static_path(name):
    """La ruta de un archivo de la web: los .js están en js/, los .css en css/ y lo demás en la raíz."""
    folder = {".js": "js", ".css": "css"}.get(Path(name).suffix, "")
    return web.STATIC_DIR / folder / name


def post_message(client, **body):
    return client.post("/api/message", json=body)


def upload(client, name="foto.jpg", mime="image/jpeg", data=b"bytes", text="", conversation_id=""):
    return client.post(
        "/api/upload",
        files={"file": (name, data, mime)},
        data={"text": text, "conversation_id": conversation_id},
    )


def put_config(client, changes=None, clear=None):
    return client.put("/api/config", json={"changes": changes or {}, "clear": clear or []})


def setting(state, key):
    return next(s for s in state["settings"] if s["key"] == key)


def page(name):
    return static_path(name).read_text(encoding="utf-8")


def menu_items(html):
    """Los <a class="nav-item"> del menú: [(href, data-command, texto)]."""
    nav = re.search(r'<nav[^>]*aria-label="Secciones"[^>]*>(.*?)</nav>', html, re.S).group(1)
    items = re.findall(r'<a class="nav-item"([^>]*)>(.*?)</a>', nav, re.S)
    result = []
    for attrs, inner in items:
        href = re.search(r'href="([^"]*)"', attrs).group(1)
        command = re.search(r'data-command="([^"]*)"', attrs)
        label = re.search(r'<span class="label">(.*?)</span>', inner, re.S)
        result.append((href, command.group(1) if command else None, label.group(1).strip() if label else ""))
    return result


def chat_html():
    return page("index.html")


def attachment_html():
    # Desde el contenedor hasta el nombre, que es lo último que tiene adentro.
    return re.search(r'<div id="chip".*?id="chip-name"[^>]*></span>', chat_html(), re.S).group(0)


def new_conversation(client):
    return client.post("/api/conversations").json()["id"]


def chat(client, conversation_id, text="", **extra):
    return client.post(
        "/api/message", json={"text": text, "conversation_id": conversation_id, **extra}
    )


def saved_lines(conversation_id):
    path = notes.DATA_DIR / "conversations" / f"{conversation_id}.jsonl"
    import json as _json

    return [_json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def js(name):
    return static_path(name).read_text(encoding="utf-8")


def tool_rows(client):
    """Las filas del panel, como las manda el servidor: [(nombre, ¿tiene la etiqueta 'usa IA'?)]."""
    return [(row["label"], row["uses_ai"]) for row in client.get("/api/tools").json()["tools"]]


def right_panel():
    return re.search(r'<aside[^>]*id="right".*?</aside>', chat_html(), re.S).group(0)


def make_conversations(client, how_many):
    ids = []
    for n in range(how_many):
        cid = new_conversation(client)
        chat(client, cid, f"/ayuda {n}")
        ids.append(cid)
    return ids


def transcribe_audio(client, mime="audio/webm;codecs=opus", data=b"audio", **extra):
    return client.post("/api/transcribe", files={"file": ("voz.webm", data, mime)}, **extra)


def telegram_talk(text="desde Telegram"):
    conversation_id = conversations.for_channel("telegram")
    conversations.add_message(conversation_id, "user", text)
    conversations.add_message(conversation_id, "bot", "respuesta")
    return conversation_id


def switch_tags():
    return re.findall(r"<button[^>]*role=\"switch\"[^>]*>", page("config.html"))


def top_links(html):
    """Los enlaces de la barra de arriba: [(href, texto, ¿es la página actual?)]."""
    nav = re.search(r'<nav class="top-nav"[^>]*>(.*?)</nav>', html, re.S).group(1)
    return [
        (m.group(2), m.group(4).strip(), "aria-current" in m.group(1) + m.group(3))
        for m in re.finditer(r'<a class="top-link"([^>]*?)href="([^"]*)"([^>]*)>(.*?)</a>', nav, re.S)
    ]
