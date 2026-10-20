import threading
import types

import pytest

from epsilon import transcribe


class FakeModel:
    """Un Whisper de mentira: devuelve frases fijas y anota cómo lo llamaron."""

    def __init__(self, texts=("hola", "mundo"), duration=5.0):
        self.texts, self.duration, self.calls = texts, duration, []

    def transcribe(self, path, **options):
        self.calls.append((path, options))
        segments = (types.SimpleNamespace(text=f" {t} ") for t in self.texts)
        return segments, types.SimpleNamespace(duration=self.duration)


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "voz.ogg"
    path.write_bytes(b"audio")
    return path


@pytest.fixture
def voice(monkeypatch):
    """Whisper instalado, modelo ya descargado y cargable: todo de mentira."""
    model = FakeModel()
    builds = []
    monkeypatch.setattr(transcribe, "_whisper_module", lambda: object())
    monkeypatch.setattr(transcribe, "_cached", lambda: True)
    monkeypatch.setattr(transcribe, "_build_model", lambda: builds.append(1) or model)
    model.builds = builds
    return model


# --- Transcribir -------------------------------------------------------------


def test_it_returns_the_text_of_all_segments_joined(voice, audio):
    assert transcribe.audio_to_text(audio) == "hola mundo"


def test_spanish_is_the_default_language_and_auto_detect_when_empty(voice, audio, monkeypatch):
    transcribe.audio_to_text(audio)
    assert voice.calls[-1][1]["language"] == "es"

    monkeypatch.setenv("WHISPER_LANGUAGE", "")
    transcribe.audio_to_text(audio)
    assert voice.calls[-1][1]["language"] is None

    monkeypatch.setenv("WHISPER_LANGUAGE", "en")
    transcribe.audio_to_text(audio)
    assert voice.calls[-1][1]["language"] == "en"


def test_silence_is_reported_not_returned_as_empty_text(voice, audio):
    voice.texts = ("  ", "")

    with pytest.raises(transcribe.VoiceError, match="No entendí nada"):
        transcribe.audio_to_text(audio)


def test_a_missing_file_is_a_clear_error(voice, tmp_path):
    with pytest.raises(transcribe.VoiceError, match="audio"):
        transcribe.audio_to_text(tmp_path / "no-existe.ogg")


def test_a_huge_file_is_refused_before_loading_anything(voice, audio, monkeypatch):
    monkeypatch.setattr(transcribe, "MAX_BYTES", 3)  # el audio de prueba pesa 5

    with pytest.raises(transcribe.VoiceError, match="pesa demasiado"):
        transcribe.audio_to_text(audio)
    assert voice.builds == []  # ni siquiera se cargó el modelo


def test_a_long_audio_is_refused_without_transcribing_it(voice, audio):
    voice.duration = transcribe.MAX_SECONDS + 1

    with pytest.raises(transcribe.VoiceError, match="muy largo"):
        transcribe.audio_to_text(audio)


def test_a_model_failure_is_a_short_message_without_details(voice, audio, monkeypatch):
    def boom(path, **options):
        raise RuntimeError("detalle interno con /ruta/secreta")

    monkeypatch.setattr(voice, "transcribe", boom)

    with pytest.raises(transcribe.VoiceError) as error:
        transcribe.audio_to_text(audio)

    assert "No pude transcribir" in str(error.value) and "secreta" not in str(error.value)


# --- Cargar y descargar ------------------------------------------------------


def test_the_model_loads_once_and_is_reused(voice, audio):
    transcribe.audio_to_text(audio)
    transcribe.audio_to_text(audio)

    assert voice.builds == [1]


def test_without_the_extra_it_says_how_to_install_it(audio, monkeypatch):
    def missing():
        raise ImportError("no hay faster_whisper")

    monkeypatch.setattr(transcribe, "_whisper_module", missing)

    with pytest.raises(transcribe.VoiceNotInstalled, match="uv sync --extra voz"):
        transcribe.audio_to_text(audio)


def test_a_missing_model_starts_the_download_and_asks_to_come_back(audio, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    downloads = []

    def slow_download():
        downloads.append(1)
        started.set()
        release.wait(5)

    monkeypatch.setattr(transcribe, "_whisper_module", lambda: object())
    monkeypatch.setattr(transcribe, "_cached", lambda: False)
    monkeypatch.setattr(transcribe, "_download", slow_download)

    with pytest.raises(transcribe.VoiceDownloading, match="descargando"):
        transcribe.audio_to_text(audio)
    assert started.wait(5)

    # Un segundo audio mientras baja NO lanza otra descarga.
    with pytest.raises(transcribe.VoiceDownloading):
        transcribe.audio_to_text(audio)
    assert downloads == [1]

    release.set()
    transcribe._download_thread.join(5)


def test_after_the_download_finishes_the_next_audio_works(audio, monkeypatch):
    state = {"cached": False}
    model = FakeModel()
    monkeypatch.setattr(transcribe, "_whisper_module", lambda: object())
    monkeypatch.setattr(transcribe, "_cached", lambda: state["cached"])
    monkeypatch.setattr(transcribe, "_download", lambda: state.update(cached=True))
    monkeypatch.setattr(transcribe, "_build_model", lambda: model)

    with pytest.raises(transcribe.VoiceDownloading):
        transcribe.audio_to_text(audio)
    transcribe._download_thread.join(5)

    assert transcribe.audio_to_text(audio) == "hola mundo"


def test_a_failed_download_is_reported_once_and_then_retried(audio, monkeypatch):
    attempts = []

    def failing_download():
        attempts.append(1)
        raise OSError("sin red, token AIzaSyD-falsa_clave_de_prueba_0123456789")

    monkeypatch.setattr(transcribe, "_whisper_module", lambda: object())
    monkeypatch.setattr(transcribe, "_cached", lambda: False)
    monkeypatch.setattr(transcribe, "_download", failing_download)

    with pytest.raises(transcribe.VoiceDownloading):
        transcribe.audio_to_text(audio)
    transcribe._download_thread.join(5)

    with pytest.raises(transcribe.VoiceError, match="No pude descargar") as error:
        transcribe.audio_to_text(audio)
    assert "AIzaSyD" not in str(error.value)  # los mensajes pasan por config.redact

    with pytest.raises(transcribe.VoiceDownloading):  # y vuelve a intentar
        transcribe.audio_to_text(audio)
    transcribe._download_thread.join(5)
    assert len(attempts) == 2


# --- Descargar de memoria tras un rato sin usarse ------------------------------


def test_an_idle_model_is_unloaded_and_loads_again_on_the_next_audio(voice, audio, monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(transcribe, "_now", lambda: clock["now"])
    transcribe.audio_to_text(audio)
    assert transcribe.status() == "cargado"

    clock["now"] += 5 * 60  # 5 minutos: todavía no
    transcribe.unload_if_idle()
    assert transcribe.status() == "cargado"

    clock["now"] += 6 * 60  # 11 minutos sin usarse
    transcribe.unload_if_idle()
    assert transcribe.status() == "descargado"

    transcribe.audio_to_text(audio)
    assert voice.builds == [1, 1]


def test_using_the_model_restarts_the_idle_clock(voice, audio, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(transcribe, "_now", lambda: clock["now"])
    transcribe.audio_to_text(audio)
    clock["now"] = 9 * 60
    transcribe.audio_to_text(audio)
    clock["now"] = 18 * 60  # 9 minutos desde el último uso

    transcribe.unload_if_idle()

    assert transcribe.status() == "cargado"


def test_the_idle_minutes_are_configurable(voice, audio, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(transcribe, "_now", lambda: clock["now"])
    monkeypatch.setenv("WHISPER_IDLE_MINUTES", "1")
    transcribe.audio_to_text(audio)
    clock["now"] = 61

    transcribe.unload_if_idle()

    assert transcribe.status() == "descargado"


def test_a_garbage_idle_setting_falls_back_to_the_default(voice, audio, monkeypatch):
    for bad in ("abc", "", "-5", "0"):
        monkeypatch.setenv("WHISPER_IDLE_MINUTES", bad)
        assert transcribe.idle_seconds() == transcribe.DEFAULT_IDLE_MINUTES * 60, bad


def test_a_timer_is_armed_after_each_use_and_does_not_keep_the_process_alive(voice, audio):
    transcribe.audio_to_text(audio)

    timer = transcribe._timer
    assert timer is not None and timer.daemon


def test_unloading_never_happens_in_the_middle_of_a_transcription(voice, audio, monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(transcribe, "_now", lambda: clock["now"])
    transcribe.audio_to_text(audio)
    clock["now"] = 10_000
    inside = []

    def slow(path, **options):
        # Mientras se transcribe, el temporizador se dispara: tiene que esperar.
        transcribe.unload_if_idle()
        inside.append(transcribe.status())
        return iter([types.SimpleNamespace(text="ok")]), types.SimpleNamespace(duration=1)

    monkeypatch.setattr(voice, "transcribe", slow)
    transcribe.audio_to_text(audio)

    assert inside == ["cargado"]


# --- Estado ------------------------------------------------------------------


def test_status_tells_what_the_voice_is_doing(audio, monkeypatch):
    def missing():
        raise ImportError

    monkeypatch.setattr(transcribe, "_whisper_module", missing)
    assert transcribe.status() == "no instalado"

    monkeypatch.setattr(transcribe, "_whisper_module", lambda: object())
    monkeypatch.setattr(transcribe, "_cached", lambda: False)
    assert transcribe.status() == "sin descargar"

    monkeypatch.setattr(transcribe, "_cached", lambda: True)
    assert transcribe.status() == "descargado"


# --- Los tests nunca cargan ni descargan Whisper de verdad --------------------


def test_the_real_model_is_blocked_in_tests(audio, monkeypatch):
    monkeypatch.setattr(transcribe, "_whisper_module", lambda: object())
    monkeypatch.setattr(transcribe, "_cached", lambda: True)  # y NO reemplazamos _build_model

    with pytest.raises(transcribe.VoiceError, match="de verdad"):
        transcribe.audio_to_text(audio)


def test_the_voice_extra_pins_av_below_19():
    # faster-whisper 1.2.1 llama av.open(metadata_errors=...), que av 19 ya no acepta: con av 19
    # TODO audio falla con «No pude transcribir». (Se descubrió probando con un audio real.)
    import tomllib
    from pathlib import Path

    root = Path(transcribe.__file__).resolve().parents[2]
    extra = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]["voz"]

    assert "faster-whisper>=1.2.1" in extra and "av<19" in extra


# --- Devolver la memoria al sistema ------------------------------------------
# Medido con el modelo real: soltar el modelo NO bajaba la RAM (1058 MB); con malloc_trim bajó a 200 MB.


def test_unloading_gives_the_memory_back_to_the_system(voice, audio, monkeypatch):
    released = []
    monkeypatch.setattr(transcribe, "_release_memory", lambda: released.append(1))
    clock = {"now": 0.0}
    monkeypatch.setattr(transcribe, "_now", lambda: clock["now"])
    transcribe.audio_to_text(audio)

    clock["now"] = 60  # todavía no es hora
    transcribe.unload_if_idle()
    assert released == []

    clock["now"] = 11 * 60
    transcribe.unload_if_idle()
    assert released == [1]


def test_releasing_memory_never_breaks_where_malloc_trim_does_not_exist(monkeypatch):
    def no_libc(name):
        raise OSError("no hay libc.so.6 (por ejemplo, en macOS o Windows)")

    monkeypatch.setattr(transcribe.ctypes, "CDLL", no_libc)

    transcribe._release_memory()  # no lanza


# --- Palabras de comando dichas en voz alta ----------------------------------


@pytest.mark.parametrize(
    "heard, expected",
    [
        ("Más comprar pan.", "+ comprar pan."),
        ("mas, comprar pan", "+ comprar pan"),
        ("MÁS: llamar a Ana", "+ llamar a Ana"),
        ("Barra uso.", "/uso"),
        ("Barra, ver inbox", "/ver inbox"),
        ("barra archivos tickets.", "/archivos tickets"),
    ],
)
def test_spoken_command_words_become_symbols(heard, expected):
    assert transcribe.spoken_commands(heard) == expected


@pytest.mark.parametrize(
    "heard",
    [
        "Más",  # sola, sin nada que guardar
        "Barra",  # sola, sin comando
        "Comprar más pan",  # «más» en el medio no es comando
        "La barra uso",  # «barra» en el medio tampoco
        "Masa madre",  # empieza igual, pero es otra palabra
        "Barrabás uso",
        "Barra 123",  # un comando es una palabra, no un número
    ],
)
def test_other_texts_are_left_alone(heard):
    assert transcribe.spoken_commands(heard) == heard


def test_audio_to_text_applies_the_translation(voice, audio):
    voice.texts = ("Más", "comprar pan.")
    assert transcribe.audio_to_text(audio) == "+ comprar pan."


def test_auto_language_means_let_whisper_detect(monkeypatch):
    for value in ("auto", "AUTO", ""):
        monkeypatch.setenv("WHISPER_LANGUAGE", value)
        assert transcribe.language() is None


# --- Un solo interruptor para todos los canales ------------------------------


def test_with_voice_turned_off_nothing_is_transcribed_loaded_or_downloaded(audio, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    touched = []
    monkeypatch.setattr(transcribe, "_build_model", lambda: touched.append("build"))
    monkeypatch.setattr(transcribe, "_download", lambda: touched.append("download"))

    with pytest.raises(transcribe.VoiceError) as error:
        transcribe.audio_to_text(audio)

    assert str(error.value) == transcribe.VOICE_OFF and touched == []
    assert transcribe.status() == "apagada"


def test_turning_it_back_on_restores_the_normal_status(monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "si")
    monkeypatch.setattr(transcribe, "_whisper_module", lambda: object())
    monkeypatch.setattr(transcribe, "_cached", lambda: True)

    assert transcribe.status() == "descargado"
