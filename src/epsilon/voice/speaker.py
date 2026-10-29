import os
import queue
import re
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

from epsilon.settings import config
from epsilon.voice import tts

# Hablar la respuesta de la IA al vuelo, oración por oración, a medida que el modelo la escribe (Voice Companion; ver
# docs/superpowers/specs/2026-10-29-voice-companion-design.md). Este módulo tiene tres partes:
#   - `SentenceSplitter` (pura): del texto ACUMULADO que va llegando, saca las oraciones ya completas.
#   - `Speaker`: recibe los eventos de la charla, corta en oraciones, las sintetiza con Kokoro y las reproduce.
#   - el reproductor: un solo proceso `aplay` con la entrada abierta, así las oraciones suenan seguidas, sin cortes.

MIN_CHARS = 20  # una oración más corta se junta con la siguiente (si no, suena a ametralladora)
MAX_NOTICE = 160  # un aviso hablado (un problema) es corto: la primera oración, o esto como mucho
MAX_CHARS = 240  # un tramo sin puntos más largo que esto se corta en una coma o un espacio, para poder empezar a hablar

# Palabras que terminan en punto sin terminar la oración («el Sr. Pérez»). Además, cualquier letra suelta («p. ej.», iniciales).
ABBREVIATIONS = {
    "sr", "sra", "srta", "dr", "dra", "ud", "uds", "vs", "av", "prof", "ing", "lic", "cap", "pág", "pag", "núm", "num",
    "tel", "aprox", "sta", "sto", "gral", "dpto", "depto", "ej", "art", "ed", "fig", "vol",
}
_END = re.compile(r"[.!?…]+[\"'”’»)\]]*(?=\s)")  # un final de oración con sus comillas, SEGUIDO de un espacio (3.5 no corta)
_LAST_WORD = re.compile(r"([^\W\d_]+)\.+[\"'”’»)\]]*$")
_CLAUSE = re.compile(r"[,;:]\s")


class SentenceSplitter:
    """Del texto acumulado de una vuelta del modelo, entrega las oraciones nuevas y completas. Recuerda hasta dónde ya
    entregó. Cada vez que el modelo empieza una vuelta nueva hay que llamar a `reset` (o llegar con un texto más corto)."""

    def __init__(self):
        self._spoken = 0

    def reset(self) -> None:
        self._spoken = 0

    def feed(self, text: str) -> list[str]:
        """Las oraciones que se completaron desde la última vez. Lo que todavía no terminó queda esperando."""
        if len(text) < self._spoken:  # el texto se reemplazó por uno más corto: otra vuelta
            self._spoken = 0
        chunks = []
        while (cut := self._next_cut(text)) is not None:
            chunk = text[self._spoken : cut].strip()
            self._spoken = cut
            if chunk:
                chunks.append(chunk)
        return chunks

    def flush(self, text: str) -> list[str]:
        """Todo lo que quedó sin entregar (la vuelta terminó)."""
        if len(text) < self._spoken:
            self._spoken = 0
        rest = text[self._spoken :].strip()
        self._spoken = len(text)
        pieces = []
        while len(rest) > MAX_CHARS:
            cut = _long_cut(rest)
            pieces.append(rest[:cut].strip())
            rest = rest[cut:].strip()
        return [piece for piece in (*pieces, rest) if piece]

    def _next_cut(self, text: str) -> int | None:
        """Dónde termina la próxima oración (un índice del texto entero), o None si todavía no hay una completa."""
        rest = text[self._spoken :]
        ends = [(m.end(), True) for m in _END.finditer(rest)] + [(i + 1, False) for i, c in enumerate(rest) if c == "\n"]
        for end, terminator in sorted(ends):
            chunk = rest[:end]
            if len(chunk.strip()) < MIN_CHARS:
                continue
            if terminator and _is_abbreviation(chunk):
                continue
            return self._spoken + end
        if len(rest) > MAX_CHARS:
            return self._spoken + _long_cut(rest)
        return None


def _is_abbreviation(chunk: str) -> bool:
    found = _LAST_WORD.search(chunk.rstrip())
    if not found or not chunk.rstrip().endswith("."):
        return False
    word = found.group(1).lower()
    return len(word) == 1 or word in ABBREVIATIONS


def _long_cut(rest: str) -> int:
    """Dónde cortar un tramo demasiado largo: después de la última coma (o similar) que entre, si no en el último espacio."""
    window = rest[: MAX_CHARS + 1]
    clauses = [m.end() for m in _CLAUSE.finditer(window) if m.end() >= MIN_CHARS]
    if clauses:
        return clauses[-1]
    spaces = [i + 1 for i, c in enumerate(window) if c.isspace() and i + 1 >= MIN_CHARS]
    return spaces[-1] if spaces else MAX_CHARS


# --- Reproducir: un solo aplay con la entrada abierta -------------------------------------------------------------------

ENV_KEYS = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "PIPEWIRE_RUNTIME_DIR", "PULSE_SERVER", "DBUS_SESSION_BUS_ADDRESS")
FINISH_TIMEOUT = 600  # lo máximo que se espera a que termine de sonar una respuesta (segundos)


def _spawn(argv: list[str], env: dict[str, str]):
    """Lanza el reproductor. El ÚNICO punto que toca el sistema (sin shell, argumentos fijos); los tests lo bloquean."""
    return subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _Player:
    """Un proceso `aplay` con la entrada abierta durante toda la respuesta: las oraciones se van escribiendo y suenan
    seguidas, sin cortes entre ellas. Audio crudo de 16 bits, mono."""

    def __init__(self, rate: int):
        env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
        self._process = _spawn(["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(rate), "-c", "1"], env)

    def write(self, data: bytes) -> None:
        self._process.stdin.write(data)
        self._process.stdin.flush()

    def finish(self) -> None:
        """Cierra la entrada y espera a que termine de sonar lo que ya se escribió."""
        try:
            self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=FINISH_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._process.kill()

    def kill(self) -> None:
        """Corta el audio ya (interrumpir)."""
        self._process.kill()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


# --- El Speaker ----------------------------------------------------------------------------------------------------------


@dataclass
class _Say:
    turn: int
    text: str


@dataclass
class _End:
    turn: int


@dataclass
class _Pcm:
    turn: int
    data: bytes
    rate: int


_STOP = object()


class _TurnState:
    def __init__(self):
        self.splitter = SentenceSplitter()
        self.last = ""  # el último texto acumulado de la vuelta
        self.spoke = False  # ya se dijo algo en este turno (si no, se dice la respuesta entera al final)
        self.ended = False


class Speaker:
    """Suscriptor de los eventos de la charla del Voice Companion: habla la respuesta a medida que el modelo la escribe.
    Corta el texto en oraciones (`SentenceSplitter`) y, en dos hilos, una se sintetiza mientras la anterior suena. Todo
    es del TURNO en curso: al interrumpir (`stop`) se mata el audio, se descarta lo que esperaba y lo que siga llegando
    de ese turno se ignora. Las respuestas se hablan enteras, sea cual sea su largo.

    Eventos que mira (todos con el número de turno): {"type": "turn", "state": "start"|"end"}, "model" y "tool" (cierran el
    texto de la vuelta), "text" (el texto acumulado), "reply" y "error"."""

    def __init__(self, announce: Callable[[str], None], synth=None, player_factory=None):
        self.announce = announce
        self._synth = synth
        self._player_factory = player_factory
        self._lock = threading.Lock()  # el turno en curso
        self._plock = threading.Lock()  # el reproductor
        self._turn: int | None = None
        self._state: _TurnState | None = None
        self._notices = 0  # los avisos hablados usan "turnos" con número negativo, para no cruzarse con los de la charla
        self._failed: int | None = None  # el turno en que la voz falló (el resto de ese turno se salta)
        self._warned = False
        self._player = None
        self._texts: queue.Queue = queue.Queue()
        self._audio: queue.Queue = queue.Queue()
        self._threads = [
            threading.Thread(target=self._synth_loop, name="voice-synth", daemon=True),
            threading.Thread(target=self._play_loop, name="voice-play", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    # --- lo que entra ---

    def on_event(self, event: dict) -> None:
        """Rápido y sin bloquear: solo encola. Lo llama quien publica los eventos."""
        kind, turn = event.get("type"), event.get("turn")
        if kind == "say":
            self._notice(str(event.get("text", "")))
            return
        if kind == "turn" and event.get("state") == "start":
            with self._lock:
                self._turn, self._state = turn, _TurnState()
            return
        with self._lock:
            state = self._state
            if state is None or turn != self._turn or not config.enabled("VOICE_SPEAK"):
                return
            if kind == "model" or (kind == "tool" and event.get("state") == "running"):
                self._close_round(state, turn)  # la vuelta del modelo terminó: lo que quedaba sin decir se dice ya
            elif kind == "text":
                state.last = event.get("text", "")
                self._say(turn, state, state.splitter.feed(state.last))
            elif kind == "reply":
                self._close_round(state, turn)
                if not state.spoke:  # el modelo no transmitió texto (o es un mensaje del sistema): se dice la respuesta entera
                    self._say(turn, state, [event.get("text", "")])
                self._end(turn, state)
            elif kind == "error":
                if not state.spoke:
                    self._say(turn, state, [event.get("text", "")])
                self._end(turn, state)
            elif kind == "turn":
                self._end(turn, state)

    def _notice(self, text: str) -> None:
        """Un aviso suelto (un problema con el micrófono o la transcripción): se dice aunque no haya una charla en curso."""
        short = _first_sentence(text)
        if not short or not config.enabled("VOICE_SPEAK"):
            return
        with self._lock:
            self._notices -= 1
            turn = self._notices
            self._turn, self._state = turn, _TurnState()
            self._say(turn, self._state, [short])
            self._end(turn, self._state)

    def stop(self) -> None:
        """Interrumpir: corta el audio ya, descarta lo que esperaba y deja de escuchar lo que siga llegando de ese turno."""
        with self._lock:
            self._turn, self._state = None, None
        _clear(self._texts)
        _clear(self._audio)
        with self._plock:
            player, self._player = self._player, None
        if player is not None:
            try:
                player.kill()
            except Exception:  # noqa: BLE001 - ya estaba muerto
                pass

    def close(self) -> None:
        self.stop()
        self._texts.put(_STOP)
        for thread in self._threads:
            thread.join(2)

    def drain(self, timeout: float = 5.0) -> bool:
        """Espera a que no quede nada por sintetizar ni por reproducir (lo usan los tests)."""
        import time

        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._texts.unfinished_tasks == 0 and self._audio.unfinished_tasks == 0:
                return True
            time.sleep(0.005)
        return False

    # --- por dentro (con self._lock tomado) ---

    def _close_round(self, state: _TurnState, turn: int) -> None:
        pending, state.last = state.splitter.flush(state.last), ""
        state.splitter.reset()
        self._say(turn, state, pending)

    def _say(self, turn: int, state: _TurnState, chunks: list[str]) -> None:
        for chunk in chunks:
            cleaned = tts.clean_for_speech(chunk, max_chars=tts.MAX_SPOKEN_CHARS)
            if cleaned:
                state.spoke = True
                self._texts.put(_Say(turn, cleaned))

    def _end(self, turn: int, state: _TurnState) -> None:
        if not state.ended:
            state.ended = True
            self._texts.put(_End(turn))

    # --- los dos hilos: sintetizar y reproducir ---

    def _synth_loop(self) -> None:
        while True:
            item = self._texts.get()
            try:
                if item is _STOP:
                    self._audio.put(_STOP)
                    return
                if item.turn != self._turn:
                    continue
                if isinstance(item, _End):
                    self._audio.put(item)
                    continue
                if self._failed == item.turn:
                    continue
                try:
                    synth = self._synth or tts.synthesize_pcm
                    data, rate = synth(item.text, config.value("VOICE_TTS_VOICE"), config.number("VOICE_TTS_SPEED"))
                except Exception as e:  # noqa: BLE001
                    self._fail(item.turn, e)
                    continue
                if item.turn == self._turn:  # si te interrumpieron mientras sintetizaba, esa oración no suena
                    self._audio.put(_Pcm(item.turn, data, rate))
            finally:
                self._texts.task_done()

    def _play_loop(self) -> None:
        while True:
            item = self._audio.get()
            try:
                if item is _STOP:
                    self._finish_player()
                    return
                if item.turn != self._turn:
                    continue
                if isinstance(item, _End):
                    self._finish_player()
                else:
                    self._write(item)
            finally:
                self._audio.task_done()

    def _write(self, item: _Pcm) -> None:
        with self._plock:
            if item.turn != self._turn:
                return
            player = self._player
            if player is None:
                try:
                    player = (self._player_factory or _Player)(item.rate)
                except Exception as e:  # noqa: BLE001
                    self._fail(item.turn, e)
                    return
                self._player = player
        try:
            player.write(item.data)
        except (OSError, ValueError) as e:
            if item.turn == self._turn:  # no fue una interrupción: el reproductor murió solo
                self._fail(item.turn, e)

    def _finish_player(self) -> None:
        with self._plock:
            player = self._player
        if player is None:
            return
        try:
            player.finish()
        except Exception:  # noqa: BLE001
            pass
        with self._plock:
            if self._player is player:
                self._player = None

    def _fail(self, turn: int, error: Exception) -> None:
        """La voz falló: se avisa UNA vez (sin detalles internos) y el resto de ese turno se salta."""
        self._failed = turn
        if self._warned:
            return
        self._warned = True
        if isinstance(error, ImportError):
            text = "Para que hable instalá la voz: uv sync --extra voz"
        elif isinstance(error, FileNotFoundError):
            text = "Falta el programa «aplay» (alsa-utils) para reproducir la voz."
        elif isinstance(error, OSError):
            text = "No pude reproducir el audio."
        else:
            print(f"Voice Companion: falló la voz: {config.redact(repr(error))}")
            text = "No pude generar la voz."
        try:
            self.announce(text)
        except Exception:  # noqa: BLE001
            pass


def _first_sentence(text: str) -> str:
    """La primera oración de un aviso (hasta MAX_NOTICE): en voz alta no se lee un párrafo técnico entero."""
    text = " ".join(text.split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    if len(first) <= MAX_NOTICE:
        return first
    cut = first[:MAX_NOTICE]
    return cut[: cut.rfind(" ")].rstrip() if " " in cut else cut


def _clear(q: queue.Queue) -> None:
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return
        q.task_done()
