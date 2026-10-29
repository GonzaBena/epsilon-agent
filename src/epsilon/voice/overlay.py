# La ventanita del Voice Companion: un cuadro anclado a una esquina (o al centro) que muestra, en un solo lugar que se va
# actualizando, lo que va pasando —«Escuchando…», lo que dijiste, «Pensando…», el texto de la respuesta mientras se escribe— y
# se esconde solo al terminar. Reemplaza a las notificaciones, que se acumulaban. Ver docs/plan-voice-companion.md.

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from epsilon import tools
from epsilon.settings import config
from epsilon.utils import pantalla

# Dónde puede aparecer: nombre -> a qué bordes se ancla (sin bordes = centrado).
POSITIONS = {
    "arriba-izquierda": ("top", "left"),
    "arriba-centro": ("top",),
    "arriba-derecha": ("top", "right"),
    "centro": (),
    "abajo-izquierda": ("bottom", "left"),
    "abajo-centro": ("bottom",),
    "abajo-derecha": ("bottom", "right"),
}

DEFAULT_POSITION = "abajo-derecha"
MAX_TEXT = 600  # de la respuesta se muestra el final (lo último que se escribió); la ventanita no crece sin límite
MAX_HEARD = 200
SPEECH_CAP = 180  # mientras habla, la ventanita espera su fin; esto es la red de seguridad por si ese aviso nunca llega (segundos)


def _daemon_timer(seconds: float, function):
    """Un temporizador que no retiene a Epsilon al cerrarse (uno normal lo haría esperar hasta que venza)."""
    timer = threading.Timer(seconds, function)
    timer.daemon = True
    return timer


def tail(text: str, limit: int) -> str:
    """El final del texto, en una línea y sin cortar una palabra a la mitad: «…últimas palabras». Corto, queda igual."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    space = cut.find(" ")
    return "…" + (cut[space + 1 :] if 0 <= space < 40 else cut)


def tool_label(name: str) -> str:
    """Cómo se nombra una herramienta en la ventanita: su rótulo sin la explicación entre paréntesis."""
    for tool in tools.ai_tools():
        if tools.ai_id(tool) == name:
            return tool.label.split(" (")[0]
    return name


def layout() -> dict:
    """Cómo se dibuja ahora (los ajustes se leen en cada tarjeta: cambiar la esquina vale para la próxima)."""
    anchors = POSITIONS.get(config.value("VOICE_OVERLAY_POSITION"), POSITIONS[DEFAULT_POSITION])  # el centro es () y vale
    return {
        "anchors": list(anchors),
        "margin": int(config.number("VOICE_OVERLAY_MARGIN")),
        "width": int(config.number("VOICE_OVERLAY_WIDTH")),
        "font": int(config.number("VOICE_OVERLAY_FONT")),
    }


class Presenter:
    """Convierte los eventos de la charla (voice/events.Bus) en órdenes para la ventanita: «mostrá esto» y «escondete». Es una
    sola tarjeta que se va actualizando; se esconde sola unos segundos después de terminar (y, si está hablando, después de que
    termine de hablar). No sabe cómo se dibuja ni quién la dibuja: manda dicts a `send`.

    Los eventos de un turno cortado (le hablaste encima) llegan tarde y se ignoran: solo vale el turno en curso."""

    def __init__(self, send, timer=_daemon_timer, label_of=tool_label):
        self._send = send
        self._timer_factory = timer
        self._label_of = label_of
        self._lock = threading.Lock()
        self._timer = None
        self._turn: int | None = None
        self._speaking = False
        self._stay = False  # el turno es de texto (Alt+C): la tarjeta espera a que la cierres, no se esconde sola
        self._closed = False  # ya hay una respuesta o un aviso en pantalla (lo que sigue es esconderse)
        self._kind = ""
        self._heard = ""
        self._text = ""
        self._last: dict | None = None

    def on_event(self, event: dict) -> None:
        with self._lock:
            self._handle(event)

    def close(self) -> None:
        """Deja de esperar para esconderse (se apagó el canal)."""
        with self._lock:
            self._cancel_timer()

    # --- por dentro (con el candado tomado) ---

    def _handle(self, event: dict) -> None:
        kind, turn = event.get("type"), event.get("turn")
        if kind == "turn":
            if event.get("state") == "start":
                self._turn = turn
                self._stay = event.get("source") == "text"
                if self._stay:  # escribiste: no hay «Escuchando…» que reinicie la tarjeta, se limpia acá
                    self._speaking = self._closed = False
                    self._heard = self._text = ""
                    self._cancel_timer()
            elif turn == self._turn and not self._closed:
                self._hide_later()  # el turno terminó sin respuesta (lo cortaron): que no quede colgado
            return
        if turn is not None and turn != self._turn:
            return
        text = str(event.get("text", ""))
        if kind == "status":
            self._status(text)
        elif kind in ("say", "error"):
            if kind == "say":
                self._heard = ""
            self._show("alert", "Aviso" if kind == "say" else "Algo falló", text=tail(text, MAX_TEXT))
            self._closed = True
            self._hide_later()
        elif kind == "tool" and event.get("state") == "running":
            # lo que el modelo escribió antes de pedir la herramienta era un pensamiento, no la respuesta: no queda a la vista
            self._show("working", f"Usando {self._label_of(str(event.get('name', '')))}…", text="")
        elif kind == "text":
            self._show("talking", "Respondiendo…", text=tail(text, MAX_TEXT))
        elif kind == "reply":
            self._show("done", "Listo", text=tail(text, MAX_TEXT))
            self._closed = True
            self._hide_later(SPEECH_CAP if self._speaking else None)
        elif kind == "speech":
            if event.get("state") == "start":
                self._speaking = True
                self._cancel_timer()  # mientras habla no se esconde
            else:
                self._speaking = False
                if self._closed:
                    self._hide_later()

    def _status(self, text: str) -> None:
        if text == "Escuchando…":
            self._turn, self._speaking, self._closed, self._stay = None, False, False, False  # otra charla: lo viejo no cuenta
            self._heard = self._text = ""
            self._show("listening", text)
        elif text == "Transcribiendo…" or text == "Pensando…":
            self._show("working", text)
        elif text.startswith("Dijiste: "):
            self._heard = "" if self._stay else tail(text.removeprefix("Dijiste: "), MAX_HEARD)  # lo escribiste vos: no hace falta repetirlo
            self._show("working", "Pensando…")
        else:  # un aviso cualquiera (p. ej. un atajo que no se pudo aplicar)
            self._show("info", tail(text, MAX_TEXT))
            self._closed = True
            self._hide_later()

    def _show(self, kind: str, title: str, text: str | None = None) -> None:
        self._cancel_timer()
        self._kind = kind
        if text is not None:
            self._text = text
        elif kind == "listening":
            self._text = ""
        command = {"op": "show", "kind": kind, "title": title, "heard": self._heard, "text": self._text, "layout": layout()}
        if command != self._last:  # lo mismo dos veces seguidas no se vuelve a mandar
            self._last = command
            self._send(command)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _hide_later(self, seconds: float | None = None) -> None:
        self._cancel_timer()
        if self._stay:
            return
        self._timer = self._timer_factory(seconds if seconds is not None else config.number("VOICE_OVERLAY_HIDE_S"), self._hide)
        self._timer.start()

    def _hide(self) -> None:
        with self._lock:
            self._timer = None
            self._last, self._kind, self._heard, self._text = None, "", "", ""
            self._send({"op": "hide"})


# --- El proceso de la ventanita -------------------------------------------------------------------------------------------
#
# La ventanita es un programa aparte (overlay_window.py: GTK4 + gtk4-layer-shell, con el Python DEL SISTEMA que trae PyGObject; el
# de Epsilon, en su entorno virtual, no lo tiene). Estar aparte tiene tres ventajas: si se cuelga, Epsilon sigue; no hay que
# instalar nada en el entorno de Epsilon; y se puede probar sola. Epsilon le escribe una orden JSON por línea en su stdin y él
# contesta por stdout «ready» o «error: motivo». `_spawn` es el ÚNICO punto que lanza el proceso (los tests lo bloquean).

SCRIPT = Path(__file__).with_name("overlay_window.py")
LIB_DIRS = ["/usr/lib", "/usr/lib64", "/usr/lib/x86_64-linux-gnu", "/usr/local/lib"]
PYTHONS = ["/usr/bin/python3", "/usr/local/bin/python3"]  # NO `which python3`: en el servicio puede ser el del entorno virtual, sin PyGObject
RETRY_AFTER = 60.0  # segundos que espera antes de volver a intentar tras un fallo
MAX_SUBMIT = 500  # lo más largo que se acepta de lo escrito en el campo (la ventanita lo limita igual: un test vigila que coincidan)
MAX_FAILURES = 3  # tras tantos fallos se rinde hasta que se reinicie Epsilon
ENV_KEYS = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR")
INSTALL = "instalá gtk4-layer-shell y python-gobject"


def _spawn(argv: list[str], env: dict[str, str]):
    """Lanza la ventanita. El ÚNICO punto que toca el sistema (sin shell, argumentos fijos)."""
    return subprocess.Popen(argv, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def _fire_and_forget(work) -> None:
    threading.Thread(target=work, name="voice-overlay-notice", daemon=True).start()


class Window:
    """El proceso de la ventanita: lo lanza la primera vez que hay algo para mostrar, le manda las órdenes y vigila que viva. Si no
    se puede abrir o se cae, se avisa UNA vez por qué y todo vuelve a las notificaciones (`active()` pasa a False); pasado un rato
    vuelve a intentar, y tras `MAX_FAILURES` fallos se rinde hasta que se reinicie Epsilon."""

    def __init__(self, announce, spawn=None, clock=time.monotonic, defer=_fire_and_forget, on_submit=None, on_closed=None):
        self._announce = announce
        self.on_submit = on_submit  # recibe lo que se escribió en el campo (Enter)
        self.on_closed = on_closed  # el campo se cerró sin enviar (Esc)
        self._defer = defer  # los avisos salen desde otro hilo: ver `_tell`
        self._spawn_override = spawn  # la de verdad (`_spawn`) se resuelve al usarla, no acá
        self._clock = clock
        self._lock = threading.RLock()
        self._proc = None
        self._closing = None  # el proceso que cerramos nosotros: que se cierre no es un fallo
        self._reason = ""  # lo que dijo el proceso antes de morir («error: …»)
        self._failures = 0
        self._last_failure: float | None = None
        self._given_up = False
        self._said: set[str] = set()
        self.ready = False

    def active(self) -> bool:
        """¿La ventanita se está ocupando de mostrar lo que pasa (y por eso no hacen falta notificaciones)? Sí si está prendida y
        funciona o todavía no falló; no si el usuario la apagó, si falta algo para abrirla o si acaba de fallar."""
        if not config.enabled("VOICE_OVERLAY"):
            return False
        with self._lock:
            if self._given_up:
                return False
            if self._proc is not None:
                return True
            if self._recently_failed():
                return False
            return self._prerequisites()[0]

    def send(self, command: dict) -> bool:
        """Le manda una orden. False si no hay ventanita (apagada, fallada o imposible): quien llama no tiene que hacer nada."""
        if not config.enabled("VOICE_OVERLAY"):
            self.close()
            return False
        with self._lock:
            if command.get("op") == "hide" and self._proc is None:
                return False  # esconder lo que nunca se mostró no tiene por qué abrir nada
            if not self._ensure_started():
                return False
            return self._write(command)

    def close(self) -> None:
        """Le pide que se vaya y la cierra (la mata si no obedece). Se puede llamar cuantas veces se quiera."""
        with self._lock:
            proc, self._proc = self._proc, None
            self.ready = False
            if proc is None:
                return
            self._closing = proc
        try:
            proc.stdin.write(b'{"op": "quit"}\n')
            proc.stdin.flush()
        except (OSError, ValueError):
            pass
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001 - no obedeció (o ya no existe): se la mata
            try:
                proc.kill()
            except OSError:
                pass

    # --- por dentro ---

    def _recently_failed(self) -> bool:
        return self._last_failure is not None and self._clock() - self._last_failure < RETRY_AFTER

    def _prerequisites(self) -> tuple[bool, str]:
        """Lo que hace falta para abrirla: el Python del sistema, gtk4-layer-shell y una sesión de Wayland. (ok, qué falta)."""
        self._argv = None
        python = next((p for p in PYTHONS if Path(p).is_file()), None)
        if python is None:
            return False, "falta el python3 del sistema con PyGObject (" + INSTALL + ")"
        lib = next((str(path) for d in LIB_DIRS for path in [Path(d) / "libgtk4-layer-shell.so"] if path.exists()), None)
        if lib is None:
            return False, "falta gtk4-layer-shell (" + INSTALL + ")"
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        display = pantalla.wayland_display(Path(runtime)) if runtime and Path(runtime).is_dir() else None
        if display is None:
            return False, "no encuentro una sesión gráfica de Wayland"
        env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
        env.update(WAYLAND_DISPLAY=display, GDK_BACKEND="wayland", LD_PRELOAD=lib)  # layer-shell tiene que cargarse antes que libwayland
        self._argv, self._env = [python, "-I", str(SCRIPT)], env  # -I: aislado (ni variables PYTHON* ni la carpeta del script en sys.path)
        return True, ""

    def _ensure_started(self) -> bool:
        if self._proc is not None:
            return True
        if self._given_up or self._recently_failed():
            return False
        ok, missing = self._prerequisites()
        if not ok:
            self._tell(f"La ventanita no se puede abrir: {missing}. Sigo con avisos.")
            return False
        try:
            proc = (self._spawn_override or _spawn)(self._argv, self._env)
        except OSError as e:
            self._failed(f"no pude lanzarla ({e.__class__.__name__})")
            return False
        try:
            os.set_blocking(proc.stdin.fileno(), False)  # si se cuelga y la tubería se llena, falla en vez de trabar a Epsilon
        except (OSError, ValueError, AttributeError):
            pass
        self._proc, self._reason, self.ready = proc, "", False
        threading.Thread(target=self._read, args=(proc,), name="voice-overlay-reader", daemon=True).start()
        return True

    def _write(self, command: dict) -> bool:
        proc = self._proc
        try:
            proc.stdin.write(json.dumps(command, ensure_ascii=False).encode() + b"\n")
            proc.stdin.flush()
        except (OSError, ValueError):  # BlockingIOError (se llenó), BrokenPipe (murió) o cerrada
            self._failed("dejó de responder")
            return False
        return True

    def _read(self, proc) -> None:
        """Lee lo que dice la ventanita hasta que se cierra."""
        try:
            while line := proc.stdout.readline():
                text = line.decode("utf-8", "replace").strip()
                if text == "ready":
                    self.ready = True
                elif text.startswith("error:"):
                    self._reason = text.removeprefix("error:").strip()[:200]
                elif text.startswith("submit:"):
                    self._submitted(text.removeprefix("submit:"))
                elif text == "closed":
                    self._call(self.on_closed)
        except (OSError, ValueError):
            pass
        self._call(self.on_closed)  # sin ventanita no hay campo abierto: el próximo atajo vuelve a intentar
        with self._lock:
            if self._closing is proc:
                self._closing = None
            elif self._proc is proc:
                self._failed(self._reason or "se cerró sola")

    def _submitted(self, payload: str) -> None:
        """Lo que se escribió en el campo: JSON de un texto de 1 a MAX_SUBMIT caracteres; lo demás se descarta."""
        try:
            text = json.loads(payload)
        except ValueError:
            return
        if isinstance(text, str) and 0 < len(text.strip()) and len(text) <= MAX_SUBMIT:
            self._call(self.on_submit, text)

    @staticmethod
    def _call(callback, *args) -> None:
        """Avisa a quien escucha, fuera de cualquier candado; si falla, el hilo lector sigue."""
        if callback is None:
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001
            pass

    def _failed(self, reason: str) -> None:
        """La ventanita falló: se la descarta, se avisa UNA vez por qué y se vuelve a intentar más adelante (o nunca, si insiste)."""
        proc, self._proc = self._proc, None
        self.ready = False
        self._failures += 1
        self._last_failure = self._clock()
        if proc is not None:
            self._closing = proc
            try:
                proc.kill()
            except OSError:
                pass
        if self._failures >= MAX_FAILURES:
            self._given_up = True
            self._tell("La ventanita falló varias veces; sigo con avisos hasta que reinicies Epsilon.")
        else:
            self._tell(f"La ventanita no pudo abrirse ({reason}). Sigo con avisos.")

    def _tell(self, text: str) -> None:
        """Un aviso al usuario, una sola vez por texto (el mismo problema no se repite en cada tarjeta)."""
        if text in self._said:
            return
        self._said.add(text)

        def tell() -> None:
            try:
                self._announce(text)
            except Exception:  # noqa: BLE001
                pass

        # Desde OTRO hilo a propósito: quien manda una tarjeta puede estar dentro del candado del Presenter, y este aviso vuelve a
        # pasar por él (por el bus). En el mismo hilo esperaría para siempre a un candado que él mismo tiene tomado.
        self._defer(tell)

