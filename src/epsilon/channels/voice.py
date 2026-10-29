import os
import socket
import threading
from pathlib import Path
from typing import Callable

from epsilon import state
from epsilon.data import notes
from epsilon.routines import notify
from epsilon.settings import config
from epsilon.utils import hyprland
from epsilon.voice import capture, client

# Voice Companion: hablarle a Epsilon desde cualquier lado con un atajo global (ver
# docs/superpowers/specs/2026-10-29-voice-companion-design.md). Es un canal más: solo traduce entre «micrófono /
# avisos» y el resto, sin lógica de negocio. `Companion` es la máquina de estados (idle <-> recording); no sabe de
# sockets ni de Hyprland, así se prueba sola. Todo lo que dice a la persona sale por `announce` (una notificación
# de escritorio) y lo que graba se entrega a `on_clip`, que es quien lo usa y lo borra.

POLL_SECONDS = 1.0  # cada cuánto el servidor mira si hay que apagarse o si cambió el atajo
MAX_REQUEST = 64  # lo que llega por el socket es una palabra: algo más largo no se obedece
WORDS = ("toggle", "cancel", "status")
MIN_SECONDS = 0.4  # menos que esto no es una frase: no se manda a transcribir (Whisper inventa texto con el silencio)


class Companion:
    def __init__(
        self,
        folder: Path,
        announce: Callable[[str], None],
        on_clip: Callable[[capture.Clip], None] | None = None,
        timer=threading.Timer,
    ):
        self.folder = Path(folder)
        self.announce = announce
        self.on_clip = on_clip or self._report
        self._timer_factory = timer
        self._lock = threading.Lock()
        self._recording: capture.Recording | None = None
        self._timer = None
        self._generation = 0  # cada grabación tiene el suyo: un temporizador viejo no corta la siguiente

    def status(self) -> str:
        return "recording" if self._recording is not None else "idle"

    def toggle(self) -> str:
        """El atajo: empieza a grabar o, si ya graba, termina y entrega el audio. Devuelve el estado en que queda."""
        with self._lock:
            if self._recording is None:
                return self._start()
            clip = self._stop()
        self._deliver(clip)
        return "idle"

    def cancel(self) -> str:
        """Tira lo grabado sin usarlo."""
        with self._lock:
            recording, self._recording = self._recording, None
            self._cancel_timer()
        if recording is not None:
            capture.cancel(recording)
        return "idle"

    # --- por dentro ---

    def _say(self, text: str) -> None:
        try:
            self.announce(text)
        except Exception:  # un aviso que falla nunca frena la grabación
            pass

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _start(self) -> str:
        try:
            self._recording = capture.start(self.folder)
        except capture.CaptureError as e:
            self._say(str(e))
            return "idle"
        self._generation += 1
        generation = self._generation
        self._timer = self._timer_factory(config.number("VOICE_MAX_RECORD_S"), lambda: self._time_is_up(generation))
        self._timer.start()
        self._say("Escuchando…")
        return "recording"

    def _stop(self) -> capture.Clip | None:
        """Termina la grabación (con el candado tomado). None si no quedó nada que usar."""
        recording, self._recording = self._recording, None
        self._cancel_timer()
        try:
            clip = capture.stop(recording)
        except capture.CaptureError as e:
            self._say(str(e))
            return None
        if clip.seconds < MIN_SECONDS:
            capture.discard(clip.path)
            self._say("No te escuché: probá de nuevo.")
            return None
        return clip

    def _time_is_up(self, generation: int) -> None:
        """Se acabó el tiempo máximo: es como apretar el atajo, pero solo si sigue siendo ESA grabación."""
        with self._lock:
            if self._recording is None or generation != self._generation:
                return
            self._say(f"Llegué al límite de {config.number('VOICE_MAX_RECORD_S')} s de grabación.")
            clip = self._stop()
        self._deliver(clip)

    def _deliver(self, clip: capture.Clip | None) -> None:
        """Le da el audio a quien lo usa, fuera del candado (puede tardar). Si falla, no queda nada trabado."""
        if clip is None:
            return
        try:
            self.on_clip(clip)
        except Exception:
            capture.discard(clip.path)
            self._say("Algo falló al procesar el audio.")

    def _report(self, clip: capture.Clip) -> None:
        """Lo que se hace con el audio hasta que haya transcripción: decir cuánto duró y borrarlo."""
        self._say(f"Grabé {clip.seconds:.1f} s.".replace(".", ",", 1))
        capture.discard(clip.path)


# --- El socket: por acá le habla el script del atajo -------------------------------------------------------------


def dispatch(companion, word: str) -> str:
    """Una palabra del socket -> lo que se hace. Solo se entienden `WORDS`; todo lo demás es «error» y no toca nada."""
    if word not in WORDS:
        return "error"
    return getattr(companion, word)()


def bind(path: Path) -> socket.socket:
    """Abre el socket (solo para tu usuario). Un archivo que quedó de un cierre brusco se reemplaza; si alguien
    escucha de verdad, ya hay otro Voice Companion andando y no se le pisa."""
    path = Path(path)
    if path.exists():
        probe = socket.socket(socket.AF_UNIX)
        probe.settimeout(1)
        try:
            probe.connect(str(path))
        except OSError:
            path.unlink(missing_ok=True)  # nadie escucha: es un resto
        else:
            raise SystemExit("Ya hay otro Voice Companion andando en este usuario.")
        finally:
            probe.close()
    listener = socket.socket(socket.AF_UNIX)
    try:
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(4)
    except OSError:
        listener.close()
        raise
    return listener


def _answer(conn: socket.socket, companion) -> None:
    with conn:
        conn.settimeout(1)
        data = conn.recv(4096)  # el cliente manda todo junto; leerlo entero evita cortarle la conexión con datos sin leer
        word = data.decode("utf-8", "replace").strip() if len(data) <= MAX_REQUEST and data.endswith(b"\n") else ""
        conn.sendall((dispatch(companion, word) + "\n").encode())


def serve(listener: socket.socket, companion, stop: threading.Event, tick: Callable[[], None]) -> None:
    """Atiende el socket hasta que `stop` se active. `tick` corre en cada vuelta (mirar si cambió el atajo)."""
    listener.settimeout(POLL_SECONDS)
    while not stop.is_set():
        tick()
        try:
            conn, _ = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            return  # el socket se cerró
        try:
            _answer(conn, companion)
        except OSError:
            pass  # el cliente se fue antes de tiempo


# --- El atajo ----------------------------------------------------------------------------------------------------


class Hotkey:
    """Mantiene el atajo de Hyprland igual al ajuste VOICE_HOTKEY: lo aplica al empezar y cada vez que cambia.
    Un atajo inválido se dice UNA vez y se sigue con el anterior; sin Hyprland, el canal sigue andando."""

    def __init__(self, folder: Path, script: Path, announce: Callable[[str], None]):
        self.folder, self.script, self.announce = folder, script, announce
        self.seen: str | None = None  # el último texto del ajuste que miramos (bueno o malo)
        self.applied: str | None = None  # el atajo que quedó puesto en Hyprland

    def sync(self) -> None:
        wanted = config.value("VOICE_HOTKEY")
        if wanted == self.seen:
            return
        self.seen = wanted
        try:
            hotkey = hyprland.normalize_hotkey(wanted)
            hyprland.write_conf(self.folder, hotkey, str(self.script))
            hyprland.apply_hotkey(hotkey, str(self.script), previous=self.applied)
        except ValueError as e:
            self.announce(f"El atajo «{wanted}» no sirve: {e}")
        except hyprland.HyprlandError as e:
            self.announce(str(e))
        else:
            self.applied = hotkey
            state.set("companion", "running", f"Atajo: {hotkey}")


# --- El canal (lo que main prende y apaga) -----------------------------------------------------------------------

_stop = threading.Event()


def is_configured(env) -> bool:
    """No necesita nada para arrancar: el micrófono se revisa recién al grabar. Lo prende o apaga COMPANION_ENABLED."""
    return True


def reset() -> None:
    """Borra un pedido de apagado viejo (main lo llama justo antes de prender el canal)."""
    _stop.clear()


def stop() -> None:
    """Pide que el canal termine (run() vuelve enseguida). No espera: main espera al hilo."""
    _stop.set()


def _announce(text: str) -> None:
    notify.desktop(text)


def run() -> None:
    folder = notes.DATA_DIR / client.FOLDER
    script = client.write_script(folder)
    companion = Companion(folder, _announce)
    hotkey = Hotkey(folder, script, _announce)
    path = client.socket_path()
    listener = bind(path)
    state.set("companion", "running", "Esperando el atajo.")
    try:
        serve(listener, companion, _stop, hotkey.sync)
    finally:
        companion.cancel()
        listener.close()
        path.unlink(missing_ok=True)
