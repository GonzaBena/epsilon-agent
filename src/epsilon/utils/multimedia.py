import json
import os
import re
import subprocess

# Volumen y música de la máquina donde corre Epsilon. Sin modelo. NO ejecuta comandos que alguien escriba: cada
# orden sale de una función fija con argumentos armados acá (el número se valida y se topa). El volumen va por
# `wpctl` (PipeWire) y la música por MPRIS, que cualquier reproductor expone en el bus de la sesión (Spotify, mpv,
# Chromium/YouTube…); se habla con `busctl`, que viene con systemd, así no hay nada más que instalar. `_run` es el
# ÚNICO punto que lanza procesos: sin shell, con tiempo máximo y con el entorno justo para llegar al bus y a PipeWire.

SINK = "@DEFAULT_AUDIO_SINK@"
MAX_VOLUME = 100  # nunca más fuerte que esto, diga lo que diga quien lo pida
MAX_STEP = 50  # lo más que se sube o baja de una vez
TIMEOUT = 5.0
ENV_KEYS = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
MAX_TEXT = 100  # título y artista los escribe el reproductor: se cortan, son dato y no instrucciones

MUTE_WORDS = {"silencio": "1", "silenciar": "1", "mute": "1", "activar": "0", "sonido": "0", "unmute": "0"}
# lo que se escribe -> método MPRIS (vacío = alternar)
MUSIC_ACTIONS = {
    "": "PlayPause", "alternar": "PlayPause", "pausa": "Pause", "pausar": "Pause", "pause": "Pause",
    "play": "Play", "reproducir": "Play", "reanudar": "Play",
    "siguiente": "Next", "next": "Next", "anterior": "Previous", "previous": "Previous",
}
MUSIC_DONE = {"PlayPause": "Alterné play/pausa.", "Pause": "Pausé.", "Play": "Reproduciendo.",
              "Next": "Pasé a la siguiente.", "Previous": "Volví a la anterior."}
# repetir y aleatorio: propiedades LoopStatus (None|Track|Playlist) y Shuffle (bool) de MPRIS. No todo reproductor las
# expone (Spotify y mpv sí; YouTube en Chromium no): si falta, se dice claro. Valor None = alternar.
REPEAT_MODES = {"": "Track", "cancion": "Track", "canción": "Track", "una": "Track", "lista": "Playlist", "todo": "Playlist",
                "todas": "Playlist", "no": "None", "off": "None", "apagar": "None"}
SHUFFLE_MODES = {"": None, "si": True, "sí": True, "on": True, "no": False, "off": False}
REPEAT_WORDS = ("repetir", "repeat")
SHUFFLE_WORDS = ("aleatorio", "shuffle")
MODES_USAGE = "repetir [canción|lista|no] y aleatorio [sí|no]"
VOLUME_USAGE = "Escribí un número (40), una subida o bajada (+5, -10), «silenciar» o «activar»."


class MultimediaError(Exception):
    """Algo falló al hablar con el sistema; el texto va para la persona (o el modelo)."""


def _run(argv: list[str]) -> str:
    """Lanza una orden fija y devuelve su salida. El ÚNICO punto que toca el sistema."""
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, env=env, check=False)
    except FileNotFoundError:
        raise MultimediaError(f"Falta el programa «{argv[0]}» en esta máquina.") from None
    except (OSError, subprocess.SubprocessError):
        raise MultimediaError("No pude hablar con el sistema de audio.") from None
    if done.returncode != 0:
        raise MultimediaError("El sistema de audio no respondió.")
    return done.stdout


# --- Volumen ------------------------------------------------------------------


def parse_volume(arg: str) -> list[str]:
    """El texto de la persona -> los argumentos de wpctl que lo cumplen. ValueError si no se entiende."""
    arg = arg.strip().lower()
    if arg in MUTE_WORDS:
        return ["set-mute", SINK, MUTE_WORDS[arg]]
    match = re.fullmatch(r"([+-]?)(\d{1,3})\s*%?", arg)
    if not match:
        raise ValueError(VOLUME_USAGE)
    sign, digits = match.groups()
    number = int(digits)
    limit = ["-l", f"{MAX_VOLUME / 100:.2f}"]  # wpctl no pasa de acá ni subiendo de a poco
    if sign:
        step = min(number, MAX_STEP)
        return ["set-volume", *limit, SINK, f"{step}%{sign}"]
    return ["set-volume", *limit, SINK, f"{min(number, MAX_VOLUME)}%"]


def volume_now() -> str:
    """«Volumen: 30 %» o «Volumen: 30 % (silenciado)»."""
    out = _run(["wpctl", "get-volume", SINK])
    match = re.search(r"Volume:\s*([\d.]+)", out)
    if not match:
        raise MultimediaError("No pude leer el volumen.")
    text = f"Volumen: {round(float(match.group(1)) * 100)} %"
    return text + " (silenciado)" if "[MUTED]" in out else text


def volume(arg: str) -> str:
    """Sin argumento dice cómo está; con argumento lo cambia y dice cómo quedó."""
    if arg.strip():
        _run(["wpctl", *parse_volume(arg)])
    return volume_now()


# --- Música (MPRIS) -------------------------------------------------------------


def _players() -> list[str]:
    names = (line.split(None, 1)[0] for line in _run(["busctl", "--user", "--no-legend", "list"]).splitlines() if line.strip())
    return [name for name in names if name.startswith(MPRIS_PREFIX)]


def _property(player: str, prop: str):
    out = _run(["busctl", "--user", "--json=short", "get-property", player, MPRIS_PATH, PLAYER_IFACE, prop])
    try:
        return json.loads(out)["data"]
    except (ValueError, KeyError, TypeError):
        raise MultimediaError("No pude leer lo que suena.") from None


def _clean(text: str) -> str:
    return " ".join(text.split())[:MAX_TEXT]


def _app(player: str) -> str:
    """org.mpris.MediaPlayer2.chromium.instance123 -> «chromium»."""
    return player.removeprefix(MPRIS_PREFIX).split(".", 1)[0]


def _pick() -> str:
    """El reproductor que suena; si ninguno suena, el primero. MultimediaError si no hay ninguno."""
    players = _players()
    if not players:
        raise MultimediaError("No hay ningún reproductor abierto.")
    for player in players:
        if _property(player, "PlaybackStatus") == "Playing":
            return player
    return players[0]


def now_playing() -> str:
    player = _pick()
    status = _property(player, "PlaybackStatus")
    meta = _property(player, "Metadata")
    title = _clean(str(meta.get("xesam:title", {}).get("data", "")))
    artists = meta.get("xesam:artist", {}).get("data", [])
    artist = _clean(", ".join(str(a) for a in artists)) if isinstance(artists, list) else ""
    state = {"Playing": "Sonando", "Paused": "En pausa"}.get(status, "Detenido")
    track = " — ".join(part for part in (artist, title) if part) or "sin título"
    return f"{state}: {track} ({_app(player)})"


def _mode(arg: str) -> tuple[str, str] | None:
    """«repetir lista» -> ("repeat", "lista"); None si no es de repetir ni de aleatorio o la opción no existe."""
    word, _, rest = arg.partition(" ")
    rest = rest.strip()
    if word in REPEAT_WORDS and rest in REPEAT_MODES:
        return "repeat", rest
    if word in SHUFFLE_WORDS and rest in SHUFFLE_MODES:
        return "shuffle", rest
    return None


def is_music_action(arg: str) -> bool:
    """¿Es algo que `music` entiende? (lo usan las rutinas para validar sin tocar el sistema)."""
    arg = " ".join(arg.lower().split())
    return arg in MUSIC_ACTIONS or _mode(arg) is not None


def _read_mode(player: str, prop: str):
    try:
        return _property(player, prop)
    except MultimediaError:
        raise MultimediaError(f"Este reproductor ({_app(player)}) no permite cambiar repetir ni aleatorio.") from None


def _set_mode(player: str, kind: str, option: str) -> str:
    """Cambia repetir o aleatorio en el reproductor y dice cómo quedó. Valores fijos, nunca texto de la persona."""
    if kind == "repeat":
        current = _read_mode(player, "LoopStatus")
        # sin opción alterna: si ya repite la canción lo apaga, si no la repite
        value = "None" if option == "" and current == "Track" else REPEAT_MODES[option]
        _run(["busctl", "--user", "set-property", player, MPRIS_PATH, PLAYER_IFACE, "LoopStatus", "s", value])
        text = {"None": "Repetir apagado", "Track": "Repitiendo la canción", "Playlist": "Repitiendo la lista"}[value]
    else:
        current = _read_mode(player, "Shuffle")
        value = (not current) if SHUFFLE_MODES[option] is None else SHUFFLE_MODES[option]
        _run(["busctl", "--user", "set-property", player, MPRIS_PATH, PLAYER_IFACE, "Shuffle", "b", "true" if value else "false"])
        text = "Aleatorio activado" if value else "Aleatorio apagado"
    return f"{text}. ({_app(player)})"


def music(arg: str) -> str:
    """Play, pausa, siguiente, anterior, repetir o aleatorio en el reproductor activo. ValueError si no es una opción."""
    arg = " ".join(arg.lower().split())
    mode = _mode(arg)
    if mode:
        return _set_mode(_pick(), *mode)
    if arg not in MUSIC_ACTIONS:
        raise ValueError("Opciones: pausa, play, siguiente, anterior, " + MODES_USAGE + " (sin opción, alterna play/pausa).")
    method = MUSIC_ACTIONS[arg]
    player = _pick()
    _run(["busctl", "--user", "call", player, MPRIS_PATH, PLAYER_IFACE, method])
    return f"{MUSIC_DONE[method]} ({_app(player)})"
