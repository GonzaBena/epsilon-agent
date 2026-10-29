import os
import socket
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from epsilon import core, state
from epsilon.ai import chat, permissions
from epsilon.data import conversations, notes
from epsilon.routines import notify
from epsilon.settings import config
from epsilon.utils import hyprland
from epsilon.voice import capture, client, events, overlay, speaker, transcribe

# Voice Companion: hablarle a Epsilon desde cualquier lado con un atajo global (ver
# docs/superpowers/specs/2026-10-29-voice-companion-design.md). Es un canal más: solo traduce entre «micrófono /
# avisos» y el resto, sin lógica de negocio. `Companion` es la máquina de estados (idle <-> recording); no sabe de
# sockets ni de Hyprland, así se prueba sola. Todo lo que dice a la persona sale por `announce` (una notificación
# de escritorio) y lo que graba se entrega a `on_clip`, que es quien lo usa y lo borra.

POLL_SECONDS = (
    1.0  # cada cuánto el servidor mira si hay que apagarse o si cambió el atajo
)
MAX_REQUEST = (
    64  # lo que llega por el socket es una palabra: algo más largo no se obedece
)
WORDS = ("toggle", "cancel", "status")
SESSION_TEXT = "companion:text"  # lo escrito con el atajo de texto: misma charla, otro gasto («Companion») y otro modo de permisos
SESSION = "companion:voice"  # la sesión que le dice al núcleo de dónde viene (permisos, gasto «Voz»)
TAG = "epsilon-voice"  # todos los avisos de escritorio comparten este: es UN globo que se va actualizando
BUS = events.Bus()  # lo que pasa en una charla hablada; quien quiera mostrarlo (una ventanita, por ejemplo) se suscribe
MIN_SECONDS = 0.4  # menos que esto no es una frase: no se manda a transcribir (Whisper inventa texto con el silencio)


def limit() -> tuple[int, str]:
    """Cuántos segundos puede durar una grabación, y qué ajuste la acota si no es VOICE_MAX_RECORD_S. Es lo MENOR entre
    lo que pediste y lo que el transcriptor acepta después (minutos y megas de `voice.max_audio_*`): grabar una charla
    que después se rechaza por larga sería perder el tiempo de la persona."""
    wanted = int(config.number("VOICE_MAX_RECORD_S"))
    by_minutes = int(config.number("VOICE_MAX_AUDIO_MINUTES") * 60)
    by_size = int(config.number("VOICE_MAX_AUDIO_MB") * 1024 * 1024) // (
        capture.SAMPLE_RATE * 2
    )  # 16 bits, mono
    seconds, source = min(
        (wanted, ""),
        (by_minutes, "voice.max_audio_minutes"),
        (by_size, "voice.max_audio_mb"),
    )
    return seconds, source


class Companion:
    def __init__(
        self,
        folder: Path,
        announce: Callable[[str], None],
        on_clip: Callable[[capture.Clip], None] | None = None,
        timer=threading.Timer,
        on_start: Callable[[], None] | None = None,
        alert: Callable[[str], None] | None = None,
    ):
        self.folder = Path(folder)
        self.announce = announce
        self.alert = (
            alert or announce
        )  # los problemas (sin micrófono, no te escuché…) también se pueden decir en voz alta
        self.on_clip = on_clip or self._report
        self.on_start = on_start  # se avisa cada vez que empieza una grabación (para cortar lo que estuviera en curso)
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

    def _warn(self, text: str) -> None:
        """Un problema: como `_say` pero por el canal de alertas (que además se habla)."""
        try:
            self.alert(text)
        except Exception:  # noqa: BLE001
            pass

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _start(self) -> str:
        try:
            self._recording = capture.start(self.folder)
        except capture.CaptureError as e:
            self._warn(str(e))
            return "idle"
        self._generation += 1
        generation = self._generation
        self._timer = self._timer_factory(
            limit()[0], lambda: self._time_is_up(generation)
        )
        self._timer.start()
        self._say("Escuchando…")
        if self.on_start is not None:
            try:
                self.on_start()
            except Exception:  # noqa: BLE001 - cortar lo anterior nunca puede impedir la grabación nueva
                pass
        return "recording"

    def _stop(self) -> capture.Clip | None:
        """Termina la grabación (con el candado tomado). None si no quedó nada que usar."""
        recording, self._recording = self._recording, None
        self._cancel_timer()
        try:
            clip = capture.stop(recording)
        except capture.CaptureError as e:
            self._warn(str(e))
            return None
        if clip.seconds < MIN_SECONDS:
            capture.discard(clip.path)
            self._warn("No te escuché: probá de nuevo.")
            return None
        return clip

    def _time_is_up(self, generation: int) -> None:
        """Se acabó el tiempo máximo: es como apretar el atajo, pero solo si sigue siendo ESA grabación."""
        with self._lock:
            if self._recording is None or generation != self._generation:
                return
            seconds, source = limit()
            blame = (
                f": lo marca {source}; subilo si querés grabar más" if source else ""
            )
            self._warn(f"Llegué al límite de {seconds} s de grabación{blame}.")
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
            self._warn("Algo falló al procesar el audio.")

    def _report(self, clip: capture.Clip) -> None:
        """Lo que se hace con el audio hasta que haya transcripción: decir cuánto duró y borrarlo."""
        self._say(f"Grabé {clip.seconds:.1f} s.".replace(".", ",", 1))
        capture.discard(clip.path)


# --- Qué se hace con cada audio -----------------------------------------------------------------------------------


def _in_thread(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="voice-listener", daemon=True).start()


class Listener:
    """Lo que pasa con un audio ya grabado: si es puro silencio no se hace nada; si no, se transcribe con Whisper, se
    muestra lo que dijiste y el texto sigue a `on_text` (la charla con la IA). Corre en su propio hilo porque transcribir
    tarda (y la primera vez carga el modelo): el hilo que atiende el atajo tiene que seguir libre. Whisper se usa de a un
    audio (lo serializa `transcribe`). El audio se borra siempre, apenas se transcribe: antes de hablar con la IA."""

    def __init__(
        self,
        announce: Callable[[str], None],
        on_text: Callable[[str], None] | None = None,
        start: Callable[[Callable[[], None]], None] = _in_thread,
        alert: Callable[[str], None] | None = None,
    ):
        self.announce = announce
        self.alert = alert or announce  # lo que salió mal se dice además en voz alta
        self.on_text = on_text
        self._start = start

    def handle(self, clip: capture.Clip) -> None:
        self._start(lambda: self._process(clip))

    def _process(self, clip: capture.Clip) -> None:
        text = self._transcribe(clip)
        if not text:
            return
        self.announce(f"Dijiste: {text}")
        if self.on_text is not None:
            self.on_text(text)

    def _transcribe(self, clip: capture.Clip) -> str | None:
        """El texto, o None si no hubo nada que decir (ya se avisó por qué)."""
        try:
            if capture.is_silent(clip.path):
                self.alert("No te escuché: probá de nuevo.")
                return None
            self.announce("Transcribiendo…")
            return transcribe.audio_to_text(
                clip.path, commands=False
            )  # crudo: la IA recibe lo que dijiste, no un comando
        except transcribe.VoiceError as e:  # el mensaje ya está pensado para la persona
            self.alert(str(e))
        except Exception as e:  # noqa: BLE001 - nada de lo que pase acá puede tumbar el canal ni mostrar detalles internos
            print(
                f"Voice Companion: falló al procesar el audio: {config.redact(repr(e))}"
            )
            self.alert("Algo falló al procesar el audio.")
        finally:
            capture.discard(clip.path)
        return None


# --- La charla con la IA (paso 3) ----------------------------------------------------------------------------------


@dataclass
class _Turn:
    conversation_id: str
    interrupted: bool = False
    source: str = "voice"  # «voice» (hablaste) o «text» (escribiste con el atajo de texto)
    context: str = ""  # lo que se sabe de dónde estás (la ventana en foco), solo para el modelo


class Assistant:
    """Le pasa lo que dijiste al núcleo, en una conversación de modo IA del canal (la misma mientras no la cierres; la
    web la ve de solo lectura). Todo lo que va pasando sale por `emit` como eventos: {"type": "status"|"model"|"tool"|
    "text"|"reply"|"error"}; no sabe quién los muestra. Los permisos van en modo «YOLO seguro» (ver `permissions.SAFE_AUTO`):
    lo que lee o escribe sin pisar nada corre solo, y lo destructivo, las rutinas y los conectores no. Si aun así llegara una
    pregunta de permiso, se DENIEGA para no dejar la sesión trabada."""

    def __init__(self, emit: Callable[[dict], None], handle=None, cancel=None):
        self.emit = emit
        self._handle = handle
        self._cancel = cancel
        self._lock = threading.Lock()
        self._turn: _Turn | None = None
        self._count = 0  # el número del último turno

    def ask(self, text: str, *, source: str = "voice", context: str = "") -> None:
        """Una pregunta: `source` «voice» (la dijiste) o «text» (la escribiste con el atajo de texto, sin audio y con `context`,
        la ventana en foco, que va solo al modelo: lo guardado en la charla es lo que escribiste)."""
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._count += 1
            number = self._count
        # Cada turno lleva su número en todos sus eventos: quien los escucha (la voz) ignora los de un turno que se cortó.
        emit = lambda event: self.emit({**event, "turn": number})  # noqa: E731
        emit({"type": "turn", "state": "start", **({"source": "text"} if source == "text" else {})})
        try:
            self._run(text, emit, source, context)
        finally:
            emit({"type": "turn", "state": "end"})

    def _run(self, text: str, emit: Callable[[dict], None], source: str = "voice", context: str = "") -> None:
        try:
            conversation_id = self._conversation()
        except Exception as e:  # noqa: BLE001 - el disco puede fallar; la persona recibe un aviso sin detalles
            print(
                f"Voice Companion: no pude abrir la conversación: {config.redact(repr(e))}"
            )
            emit({"type": "error", "text": "No pude abrir la conversación de voz."})
            return
        by_text = source == "text"
        # Nadie contesta un «¿lo permitís?» en la tarjeta: lo que no es destructivo corre solo y lo demás no se hace.
        permissions.set_mode(
            conversation_id, permissions.SAFE_AUTO_TEXT if by_text else permissions.SAFE_AUTO
        )
        turn = _Turn(conversation_id, source=source, context=context)
        with self._lock:
            self._turn = turn
        if by_text:
            emit({"type": "status", "text": f"Dijiste: {text}"})  # la tarjeta muestra lo que escribiste, como lo que dijiste
        emit({"type": "status", "text": "Pensando…"})
        try:
            reply = self._deny_permission(
                self._call(
                    core.Message(
                        text=f"{context}\n{text}" if context else text,
                        session_id=SESSION_TEXT if by_text else SESSION,
                        conversation_id=conversation_id,
                    ),
                    emit,
                    True,
                ),
                conversation_id,
                emit,
            )
        except Exception as e:  # noqa: BLE001
            print(
                f"Voice Companion: falló la charla con la IA: {config.redact(repr(e))}"
            )
            if not turn.interrupted:
                emit({"type": "error", "text": "Algo falló al hablar con la IA."})
            return
        finally:
            with self._lock:
                if self._turn is turn:
                    self._turn = None
        self._save(conversation_id, text, reply)
        if (
            not turn.interrupted
        ):  # una respuesta que ya no querés no se muestra (igual queda en la charla)
            emit({"type": "reply", "text": reply.text})

    def interrupt(self) -> None:
        """Corta el turno que esté corriendo (si hay uno): hablar de nuevo encima de la respuesta."""
        with self._lock:
            turn = self._turn
            if turn is None or turn.interrupted:
                return
            turn.interrupted = True
        (self._cancel or core.cancel)(turn.conversation_id)

    # --- por dentro ---

    def _conversation(self) -> str:
        found = conversations.current("companion")
        if found and conversations.mode(found) == "ai":
            return found
        return conversations.start_channel("companion", "ai", chat.default_limit())

    def _call(
        self, msg: core.Message, emit: Callable[[dict], None], stream_text: bool = False
    ) -> core.Reply:
        handle = self._handle or core.handle
        return handle(msg, on_event=emit, stream_text=stream_text)

    def _deny_permission(
        self, reply: core.Reply, conversation_id: str, emit: Callable[[dict], None]
    ) -> core.Reply:
        if not any(action.id.startswith("perm:") for action in reply.actions):
            return reply
        self._call(
            core.Message(
                action="perm:no", session_id=SESSION, conversation_id=conversation_id
            ),
            emit,
        )
        note = "No te puedo preguntar por voz todavía, así que no lo hice. Si querés que lo haga sola, cambiá el permiso en /permisos."
        return core.Reply(f"{reply.text}\n\n{note}", ai=True)

    def _save(self, conversation_id: str, said: str, reply: core.Reply) -> None:
        try:
            conversations.add_message(conversation_id, "user", said)
            conversations.add_message(
                conversation_id,
                "bot",
                reply.text,
                actions=[asdict(a) for a in reply.actions],
                artifacts=[asdict(a) for a in reply.artifacts],
                ai=reply.ai,
            )
        except Exception as e:  # noqa: BLE001 - anotar la charla nunca rompe la respuesta
            print(
                f"Voice Companion: no pude anotar la conversación: {config.redact(repr(e))}"
            )


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
        data = conn.recv(
            4096
        )  # el cliente manda todo junto; leerlo entero evita cortarle la conexión con datos sin leer
        word = (
            data.decode("utf-8", "replace").strip()
            if len(data) <= MAX_REQUEST and data.endswith(b"\n")
            else ""
        )
        conn.sendall((dispatch(companion, word) + "\n").encode())


def serve(
    listener: socket.socket, companion, stop: threading.Event, tick: Callable[[], None]
) -> None:
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
    """Mantiene los atajos de Hyprland iguales a los ajustes VOICE_HOTKEY (hablar) y VOICE_TEXT_HOTKEY (escribir): los aplica al
    empezar y cada vez que cambian. Un atajo inválido se dice UNA vez y se sigue con el anterior; sin Hyprland, el canal sigue
    andando. Los dos van en los mismos archivos de config; solo se vuelve a aplicar el que cambió."""

    def __init__(self, folder: Path, script: Path, announce: Callable[[str], None], text_script: Path | None = None):
        self.folder, self.script, self.announce = folder, script, announce
        self.text_script = text_script
        self.seen: tuple[str, str] | None = None  # el último par de textos de los ajustes que miramos (buenos o malos)
        self.applied: str | None = None  # el atajo de voz que quedó puesto en Hyprland
        self.applied_text: str | None = None  # y el de texto

    def sync(self) -> None:
        wanted = config.value("VOICE_HOTKEY")
        wanted_text = config.value("VOICE_TEXT_HOTKEY") if self.text_script else ""
        if (wanted, wanted_text) == self.seen:
            return
        self.seen = (wanted, wanted_text)
        try:
            hotkey = hyprland.normalize_hotkey(wanted)
        except ValueError as e:
            self.announce(f"El atajo «{wanted}» no sirve: {e}")
            return
        try:
            text_hotkey = hyprland.normalize_hotkey(wanted_text) if wanted_text.strip() else None
        except ValueError as e:
            self.announce(f"El atajo de texto «{wanted_text}» no sirve: {e}")
            return
        if text_hotkey == hotkey:
            self.announce(f"El atajo de texto es igual al de voz ({hotkey}): sigo solo con el de voz.")
            text_hotkey = None
        try:
            extra = (text_hotkey, str(self.text_script)) if text_hotkey else None
            hyprland.write_conf(self.folder, hotkey, str(self.script), extra=extra)
            if hotkey != self.applied:
                hyprland.apply_hotkey(hotkey, str(self.script), previous=self.applied)
                self.applied = hotkey
            if text_hotkey != self.applied_text:
                if text_hotkey:
                    hyprland.apply_hotkey(text_hotkey, str(self.text_script), previous=self.applied_text)
                else:
                    hyprland.unbind_hotkey(self.applied_text)
                self.applied_text = text_hotkey
        except hyprland.HyprlandError as e:
            self.announce(str(e))
        else:
            shown = f"{self.applied} · escribir: {self.applied_text}" if self.applied_text else self.applied
            state.set("companion", "running", f"Atajo: {shown}")


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
    """Un aviso de estado («Escuchando…», «Dijiste: …»): sale como evento, y el escritorio lo muestra."""
    BUS.emit({"type": "status", "text": text})


def _alert(text: str) -> None:
    """Un problema que la persona tiene que enterarse aunque no mire la pantalla: sale como aviso y además se dice en voz alta."""
    BUS.emit({"type": "say", "text": text})


_WINDOW = None  # la ventanita del canal en marcha (la pone run()); mientras esté viva, ella muestra todo y las notificaciones callan


def notifier(event: dict) -> None:
    """El suscriptor del escritorio: estados, respuesta y errores como notificación. Calla mientras la ventanita está viva (ella
    muestra todo en una sola tarjeta que se actualiza); si no se puede abrir o se cae, vuelve a avisar por acá."""
    if _WINDOW is not None and _WINDOW.active():
        return
    if event.get("type") in ("status", "say", "reply", "error") and event.get("text"):
        notify.desktop(event["text"], tag=TAG)


def _warm_up() -> None:
    try:
        transcribe.warm()
    except Exception as e:  # noqa: BLE001 - cargar el modelo de antemano es un favor: si falla, el audio mostrará el motivo
        print(f"Voice Companion: no pude precalentar la voz: {config.redact(repr(e))}")


def run() -> None:
    folder = notes.DATA_DIR / client.FOLDER
    script = client.write_script(folder)
    ask_script = client.write_script(folder, "ask")
    capture.sweep(folder)  # restos de un cierre brusco: audios que nadie va a usar
    path = client.socket_path()
    listener = bind(
        path
    )  # primero el socket: si ya hay otro Voice Companion, se corta acá sin dejar nada puesto
    assistant = Assistant(BUS.emit)
    voice = speaker.Speaker(_announce, emit=BUS.emit)
    window = overlay.Window(_announce)
    presenter = overlay.Presenter(window.send)

    def cut_everything() -> None:
        """Hablarle de nuevo encima de la respuesta: se corta el audio y el turno de la IA que seguía en curso."""
        voice.stop()
        assistant.interrupt()
        # Whisper tarda ~10 s en cargar: se lo empieza a cargar ya, mientras la persona habla, y cuando termine está listo.
        threading.Thread(target=_warm_up, name="voice-warm", daemon=True).start()

    companion = Companion(
        folder,
        _announce,
        Listener(_announce, on_text=assistant.ask, alert=_alert).handle,
        on_start=cut_everything,
        alert=_alert,
    )
    hotkey = Hotkey(folder, script, _announce, text_script=ask_script)
    unsubscribes = [
        BUS.subscribe(notifier),
        BUS.subscribe(voice.on_event),
        BUS.subscribe(presenter.on_event),
    ]
    global _WINDOW
    _WINDOW = window
    state.set("companion", "running", "Esperando el atajo.")
    try:
        serve(listener, companion, _stop, hotkey.sync)
    finally:
        _WINDOW = None
        voice.stop()
        assistant.interrupt()
        companion.cancel()
        for unsubscribe in unsubscribes:
            unsubscribe()
        presenter.close()
        window.close()
        voice.close()
        listener.close()
        path.unlink(missing_ok=True)
