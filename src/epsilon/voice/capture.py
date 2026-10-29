import os
import signal
import subprocess
import time
import wave
from dataclasses import dataclass
from pathlib import Path

# Graba el micrófono para el Voice Companion (ver docs/superpowers/specs/2026-10-29-voice-companion-design.md).
# Usa SIEMPRE el micrófono predeterminado de PipeWire (`pw-record` sin --target): si la persona cambia de micrófono en
# el sistema, Epsilon lo sigue. 16 kHz mono, que es lo que Whisper quiere. `_spawn` es el ÚNICO punto que lanza el
# proceso (sin shell, argumentos fijos); los tests lo bloquean. El audio vive solo en una carpeta privada (0700) y quien
# lo usa lo borra apenas lo transcribe (`discard`): nunca se guarda ni sale de la PC.

SAMPLE_RATE = 16000
FILE_NAME = "rec.wav"
STOP_TIMEOUT = 3.0
# pw-record sale enseguida (código 1) si PipeWire no tiene un micrófono predeterminado al que conectarlo. Se espera un
# instante para enterarse en el primer toque y no después de que la persona habló diez segundos a la nada. La grabación
# ya corre mientras tanto: no se pierde audio, solo el aviso «Escuchando…» llega esa fracción de segundo después.
STARTUP_CHECK = 0.25
NO_SOURCE = (
    "No pude grabar: PipeWire no tiene un micrófono predeterminado disponible. Conectá uno y elegilo como entrada "
    "en los ajustes de sonido (con «wpctl status» ves los que hay y con «wpctl set-default <id>» fijás uno)."
)
ENV_KEYS = ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "PIPEWIRE_RUNTIME_DIR", "PULSE_SERVER", "DBUS_SESSION_BUS_ADDRESS")


class CaptureError(Exception):
    """No se pudo grabar; el texto va para la persona."""


@dataclass
class Recording:
    """Una grabación en curso."""

    path: Path
    process: object


@dataclass
class Clip:
    """Una grabación terminada."""

    path: Path
    seconds: float


def _spawn(argv: list[str], env: dict[str, str]):
    """Lanza el grabador. El ÚNICO punto que toca el sistema."""
    return subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start(folder: Path) -> Recording:
    """Empieza a grabar en `folder` (se crea privada). Una grabación vieja que haya quedado se reemplaza."""
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / FILE_NAME
    discard(path)
    argv = ["pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1", "--format", "s16", str(path)]
    env = {key: os.environ[key] for key in ENV_KEYS if key in os.environ}
    try:
        process = _spawn(argv, env)
    except FileNotFoundError:
        raise CaptureError("Falta el programa «pw-record» (PipeWire) para grabar el micrófono.") from None
    except OSError:
        raise CaptureError("No pude empezar a grabar el micrófono.") from None
    time.sleep(STARTUP_CHECK)
    if process.poll() is not None:  # murió solo: casi siempre es que no hay micrófono predeterminado
        discard(path)
        raise CaptureError(NO_SOURCE)
    return Recording(path, process)


def _finish(recording: Recording) -> None:
    """Le pide al grabador que termine (SIGINT: así cierra bien el encabezado del WAV) y lo espera."""
    process = recording.process
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()


def stop(recording: Recording) -> Clip:
    """Termina de grabar y dice cuánto duró. Lanza CaptureError si no quedó audio (sin micrófono, grabador caído)."""
    if recording.process.poll() is not None:  # se cayó solo a mitad de camino (p. ej. desenchufaron el micrófono)
        discard(recording.path)
        raise CaptureError("La grabación se cortó sola: revisá que el micrófono siga conectado.")
    _finish(recording)
    try:
        with wave.open(str(recording.path), "rb") as audio:
            seconds = audio.getnframes() / audio.getframerate()
    except (OSError, EOFError, wave.Error):
        discard(recording.path)
        raise CaptureError("No quedó nada grabado: revisá que haya un micrófono conectado y activo.") from None
    return Clip(recording.path, seconds)


def cancel(recording: Recording) -> None:
    """Corta la grabación y borra lo grabado."""
    _finish(recording)
    discard(recording.path)


def discard(path: Path) -> None:
    """Borra un audio. Si ya no está, no pasa nada."""
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
