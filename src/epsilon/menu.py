from dataclasses import dataclass

from epsilon import config

# El /menu: ajustes que se cambian desde el propio chat, con botones. Cada pantalla es una
# entrada de SCREENS; para sumar un ajuste hay que agregarlo en una pantalla (y que exista en
# config.py como ajuste de sí/no). No conoce ningún canal ni el tipo Reply del núcleo: solo
# devuelve el texto y los botones (id, rótulo), que core.py convierte en una respuesta.
#
# Seguridad: lo único que el menú puede cambiar son los ajustes listados acá. Un id de botón
# con otra clave se rechaza, aunque el texto llegue armado a mano.


@dataclass(frozen=True)
class Toggle:
    key: str  # ajuste de sí/no de config.py
    label: str


@dataclass(frozen=True)
class Screen:
    label: str  # cómo se ve el botón que lleva a esta pantalla
    title: str  # el encabezado del mensaje
    items: tuple  # Toggle, o el nombre de otra pantalla
    back: str | None = None  # a qué pantalla vuelve «‹ Volver»; None = es la principal
    hint: str = "Tocá una opción para cambiarla."


MAIN = "main"

SCREENS: dict[str, Screen] = {
    MAIN: Screen("", "⚙️ Menú", ("voz",), hint="Elegí qué configurar."),
    "voz": Screen(
        "🎙 Voz",
        "🎙 Voz",
        (
            Toggle("VOICE_ENABLED", "Voz (web y Telegram)"),
            Toggle("VOICE_CONFIRM", "Confirmar antes de ejecutar"),
        ),
        back=MAIN,
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


def render(name: str = MAIN) -> tuple[str, list[tuple[str, str]]]:
    """El texto y los botones (id, rótulo) de una pantalla."""
    screen = SCREENS[name]
    buttons = []
    for item in screen.items:
        if isinstance(item, Toggle):
            state = "sí ✅" if config.enabled(item.key) else "no ⬜"
            buttons.append((f"{PREFIX}t:{item.key}", f"{item.label}: {state}"))
        else:
            buttons.append((f"{PREFIX}{item}", SCREENS[item].label))
    buttons.append((CLOSE, "✖ Cerrar") if screen.back is None else (f"{PREFIX}{screen.back}", "‹ Volver"))
    return f"{screen.title}\n{screen.hint}", buttons


def press(rest: str) -> tuple[str, list[tuple[str, str]]] | None:
    """Atiende un botón (lo que viene después de «menu:»). Devuelve la pantalla a mostrar,
    o None si el id no es del menú. Lanza config.ConfigError u OSError si no se pudo guardar."""
    if rest == "close":
        return "Menú cerrado.", []
    if rest in SCREENS:
        return render(rest)
    kind, _, key = rest.partition(":")
    found = _toggles().get(key) if kind == "t" else None
    if not found:
        return None
    screen, _ = found
    config.update({key: "no" if config.enabled(key) else "si"}, [])
    return render(screen)
