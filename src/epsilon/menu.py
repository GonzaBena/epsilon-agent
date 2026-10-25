from dataclasses import dataclass
import hashlib
import os
import re

from epsilon.ai import favorites, llm
from epsilon.settings import config

# El /menu: ajustes que se cambian desde el propio chat, con botones. Cada pantalla es una
# entrada de SCREENS; para sumar un ajuste hay que agregarlo en una pantalla (y que exista en
# config.py como ajuste de sí/no). No conoce ningún canal ni el tipo Reply del núcleo: solo
# devuelve el texto y los botones (id, rótulo), que core.py convierte en una respuesta.
#
# Seguridad: lo único que el menú puede cambiar son los ajustes listados acá (y el modelo, solo
# entre los favoritos). Un id de botón con otra clave se rechaza, aunque el texto llegue armado a mano.


@dataclass(frozen=True)
class Toggle:
    key: str  # ajuste de sí/no de config.py
    label: str


@dataclass(frozen=True)
class Models:
    """Los botones de esta pantalla son los modelos favoritos (los que marcaste en la web): cambian solos."""


@dataclass(frozen=True)
class Screen:
    label: str  # cómo se ve el botón que lleva a esta pantalla
    title: str  # el encabezado del mensaje
    items: tuple  # Toggle, o el nombre de otra pantalla
    back: str | None = None  # a qué pantalla vuelve «‹ Volver»; None = es la principal
    hint: str = "Tocá una opción para cambiarla."


MAIN = "main"

SCREENS: dict[str, Screen] = {
    MAIN: Screen("", "⚙️ Menú", ("voz", "modelo"), hint="Elegí qué configurar."),
    "voz": Screen(
        "🎙 Voz",
        "🎙 Voz",
        (
            Toggle("VOICE_ENABLED", "Voz (web y Telegram)"),
            Toggle("VOICE_CONFIRM", "Confirmar antes de ejecutar"),
            Toggle("TTS_ENABLED", "Responder con audio"),
        ),
        back=MAIN,
    ),
    "modelo": Screen(
        "🤖 Modelo",
        "🤖 Modelo de IA",
        (Models(),),
        back=MAIN,
        hint="Tocá un favorito para usarlo. Si hay una charla con IA abierta, el próximo mensaje le reenvía su contexto al modelo nuevo.",
    ),
}

# Ids de los botones. Cortos: Telegram acepta hasta 64 bytes.
PREFIX = "menu:"
CLOSE = PREFIX + "close"


def _toggles() -> dict[str, tuple[str, Toggle]]:
    """clave del ajuste -> (pantalla donde está, el Toggle)."""
    return {
        item.key: (name, item)
        for name, screen in SCREENS.items()
        for item in screen.items
        if isinstance(item, Toggle)
    }


def _model_key(model: str) -> str:
    """Un nombre corto y fijo para el botón de un modelo: Telegram deja 64 bytes por id y un modelo puede
    llamarse con 100 caracteres. Se vuelve a buscar entre los favoritos de ese momento al tocarlo."""
    return hashlib.sha1(model.encode()).hexdigest()[:8]


def _model_buttons() -> list[tuple[str, str]]:
    current = llm.model_for("chat")  # el que de verdad usa el chat (puede ser AI_MODEL_CHAT)
    return [
        (f"{PREFIX}m:{_model_key(model)}", f"✅ {model}" if model == current else model)
        for model in favorites.load()
    ]


def render(name: str = MAIN, note: str = "") -> tuple[str, list[tuple[str, str]]]:
    """El texto y los botones (id, rótulo) de una pantalla. `note` va arriba, para avisar qué pasó."""
    screen = SCREENS[name]
    hint = screen.hint
    buttons = []
    for item in screen.items:
        if isinstance(item, Toggle):
            state = "sí ✅" if config.enabled(item.key) else "no ⬜"
            buttons.append((f"{PREFIX}t:{item.key}", f"{item.label}: {state}"))
        elif isinstance(item, Models):
            buttons += _model_buttons()
            if not buttons:
                hint = "No tenés modelos favoritos. Marcalos en la web: en el selector de modelo, con la estrella o Ctrl+F."
            elif os.environ.get("AI_MODEL_CHAT"):
                hint += "\n⚠️ AI_MODEL_CHAT manda en el chat: tocar un favorito cambia AI_MODEL, no ese."
        else:
            buttons.append((f"{PREFIX}{item}", SCREENS[item].label))
    buttons.append((CLOSE, "✖ Cerrar") if screen.back is None else (f"{PREFIX}{screen.back}", "‹ Volver"))
    head = f"{note}\n\n" if note else ""
    return f"{head}{screen.title}\n{hint}", buttons


def press(rest: str) -> tuple[str, list[tuple[str, str]]] | None:
    """Atiende un botón (lo que viene después de «menu:»). Devuelve la pantalla a mostrar,
    o None si el id no es del menú. Lanza config.ConfigError u OSError si no se pudo guardar."""
    if rest == "close":
        return "Menú cerrado.", []
    if rest in SCREENS:
        return render(rest)
    kind, _, key = rest.partition(":")
    if kind == "m":
        return _choose_model(key)
    found = _toggles().get(key) if kind == "t" else None
    if not found:
        return None
    screen, _ = found
    config.update({key: "no" if config.enabled(key) else "si"}, [])
    return render(screen)


def _choose_model(key: str) -> tuple[str, list[tuple[str, str]]] | None:
    """Un toque en un favorito: lo deja como modelo (AI_MODEL, igual que el selector de la web). Solo vale
    un favorito de ESTE momento: si ya no está, no se cambia nada."""
    if not re.fullmatch(r"[0-9a-f]{8}", key):
        return None
    model = next((m for m in favorites.load() if _model_key(m) == key), None)
    if model is None:
        return render("modelo", "Ese favorito ya no está.")
    config.update({"AI_MODEL": model}, [])
    return render("modelo", f"Ahora usás {model}.")
