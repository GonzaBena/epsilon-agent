import json
import threading
import types
from types import SimpleNamespace

import pytest

from epsilon.data import notes
from epsilon.voice import transcribe


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
    monkeypatch.setenv("VOICE_MAX_AUDIO_MB", "1")
    audio.write_bytes(b"x" * (1024 * 1024 + 1))  # un byte más que el mínimo permitido

    with pytest.raises(transcribe.VoiceError, match="pesa demasiado"):
        transcribe.audio_to_text(audio)
    assert voice.builds == []  # ni siquiera se cargó el modelo


def test_a_long_audio_is_refused_without_transcribing_it(voice, audio, monkeypatch):
    monkeypatch.setenv("VOICE_MAX_AUDIO_MINUTES", "1")
    voice.duration = 61

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
        assert transcribe.idle_seconds() == 10 * 60, bad


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

    import epsilon

    root = Path(epsilon.__file__).resolve().parents[2]
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


# --- Borrar el modelo: solo el que bajó Epsilon, y con la voz apagada ----------


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Una caché de Hugging Face de mentira, y DATA_DIR aparte para la marca."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(notes, "DATA_DIR", data)
    repo = tmp_path / "hub" / "models--org--faster-whisper-large-v3-turbo"
    (repo / "blobs").mkdir(parents=True)
    (repo / "snapshots" / "abc123").mkdir(parents=True)
    (repo / "blobs" / "weights").write_bytes(b"x" * 1000)
    (repo / "snapshots" / "abc123" / "model.bin").symlink_to(repo / "blobs" / "weights")
    transcribe._reset()
    return SimpleNamespace(repo=repo, snapshot=repo / "snapshots" / "abc123", data=data)


def wrote_marker(cache):
    transcribe._remember_download(cache.snapshot)


def test_a_finished_download_leaves_a_mark_so_the_model_can_be_deleted_later(cache, monkeypatch):
    monkeypatch.setattr(transcribe, "_download", lambda: cache.snapshot)

    transcribe._run_download()

    assert json.loads((cache.data / transcribe.MARKER).read_text())["path"] == str(cache.snapshot)
    assert transcribe.deletable_model() == 1000  # el peso real (blobs), sin contar los enlaces


def test_a_model_epsilon_did_not_download_cannot_be_deleted(cache, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")  # sin marca: ni siquiera apagada

    assert transcribe.deletable_model() is None
    with pytest.raises(transcribe.VoiceError, match="no borro nada"):
        transcribe.delete_model()
    assert cache.repo.exists()


def test_the_model_stays_if_the_voice_is_on(cache):
    wrote_marker(cache)

    with pytest.raises(transcribe.VoiceError, match="Apagá la voz"):
        transcribe.delete_model()
    assert cache.repo.exists() and (cache.data / transcribe.MARKER).exists()


def test_deleting_removes_the_model_and_the_mark_and_unloads_it(cache, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    wrote_marker(cache)
    transcribe._model = object()

    freed = transcribe.delete_model()

    assert freed == 1000 and not cache.repo.exists()
    assert not (cache.data / transcribe.MARKER).exists() and transcribe._model is None
    assert transcribe.deletable_model() is None


def test_deleting_is_refused_while_a_download_or_a_transcription_runs(cache, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    wrote_marker(cache)
    release = threading.Event()
    transcribe._download_thread = threading.Thread(target=release.wait, daemon=True)
    transcribe._download_thread.start()

    with pytest.raises(transcribe.VoiceError, match="se está bajando"):
        transcribe.delete_model()
    release.set()
    transcribe._download_thread.join()

    with transcribe._lock, pytest.raises(transcribe.VoiceError, match="audio en proceso"):
        transcribe.delete_model()
    assert cache.repo.exists()


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        '{"model": "otro", "path": "/x/models--a--whisper/snapshots/s"}',  # de otro modelo
        '{"model": "large-v3-turbo", "path": "/etc"}',  # no es una carpeta de snapshots
        '{"model": "large-v3-turbo"}',  # sin ruta
    ],
)
def test_a_damaged_or_foreign_mark_never_deletes_anything(cache, monkeypatch, bad):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    (cache.data / transcribe.MARKER).write_text(bad)

    assert transcribe.deletable_model() is None
    with pytest.raises(transcribe.VoiceError):
        transcribe.delete_model()


def test_a_mark_pointing_outside_a_whisper_model_folder_deletes_nothing(cache, tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    other = tmp_path / "hub" / "models--org--otra-cosa"
    (other / "snapshots" / "s").mkdir(parents=True)
    (cache.data / transcribe.MARKER).write_text(
        json.dumps({"model": transcribe.MODEL, "path": str(other / "snapshots" / "s")})
    )

    with pytest.raises(transcribe.VoiceError):
        transcribe.delete_model()
    assert other.exists()


# --- «Hola <nombre>, …» ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "heard, expected",
    [
        ("Hola IA, tengo tareas para hoy?", "/ia tengo tareas para hoy?"),
        ("hola ia tengo tareas", "/ia tengo tareas"),
        ("Hola, AI: ¿qué día es hoy?", "/ia ¿qué día es hoy?"),
        ("¡Hola IA! Resumime inbox", "/ia Resumime inbox"),
    ],
)
def test_hello_plus_the_default_name_becomes_an_ia_question(heard, expected):
    assert transcribe.spoken_commands(heard) == expected


@pytest.mark.parametrize(
    "heard",
    ["Hola Meli, tengo tareas para hoy?", "hola, MELÍ: tengo tareas para hoy?", "Hola Mely tengo tareas para hoy?"],
)
def test_a_configured_name_is_the_one_that_answers(monkeypatch, heard):
    monkeypatch.setenv("ASSISTANT_NAME", "Meli, Melí, Mely")

    assert transcribe.spoken_commands(heard) == "/ia tengo tareas para hoy?"


def test_the_default_names_stop_working_once_another_is_set(monkeypatch):
    monkeypatch.setenv("ASSISTANT_NAME", "Meli")

    assert transcribe.spoken_commands("Hola IA, qué hora es") == "Hola IA, qué hora es"


@pytest.mark.parametrize(
    "heard",
    [
        "Hola",
        "Hola IA",  # el saludo solo, sin pregunta
        "Hola IA.",
        "Hola, ¿qué tal?",  # no lleva el nombre
        "Hola Iana, qué tal",  # empieza igual que el nombre, pero es otra palabra
        "Che, hola IA, qué hora es",  # solo al principio
        "Holandesa IA, qué hora es",
    ],
)
def test_a_greeting_without_the_name_or_a_question_is_left_alone(heard):
    assert transcribe.spoken_commands(heard) == heard


def test_the_name_is_not_a_regex(monkeypatch):
    monkeypatch.setenv("ASSISTANT_NAME", "a.b")  # el punto es un punto, no «cualquier letra»

    assert transcribe.spoken_commands("Hola axb, qué tal") == "Hola axb, qué tal"
    assert transcribe.spoken_commands("Hola a.b, qué tal") == "/ia qué tal"


def test_audio_to_text_turns_the_greeting_into_an_ia_question(voice, audio):
    voice.texts = ("Hola IA,", "tengo tareas para hoy?")

    assert transcribe.audio_to_text(audio) == "/ia tengo tareas para hoy?"


def test_a_caller_can_ask_for_the_raw_text_without_spoken_commands(voice, audio):
    voice.texts = ("Hola IA,", "tengo tareas para hoy?")

    assert transcribe.audio_to_text(audio) == "/ia tengo tareas para hoy?"
    assert transcribe.audio_to_text(audio, commands=False) == "Hola IA, tengo tareas para hoy?"


# --- Precalentar: cargar el modelo mientras la persona todavía está hablando -------------------------------------------------


def test_warming_loads_the_model_once_and_the_audio_afterwards_reuses_it(voice, audio):
    assert transcribe.warm() is True
    assert voice.builds == [1]

    transcribe.audio_to_text(audio)

    assert voice.builds == [1]  # el audio no lo vuelve a cargar: ya estaba


def test_warming_twice_does_not_load_twice(voice):
    transcribe.warm()
    transcribe.warm()

    assert voice.builds == [1]


def test_warming_does_nothing_when_the_voice_is_off(voice, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")

    assert transcribe.warm() is False
    assert voice.builds == []


def test_warming_never_downloads_the_model(voice, monkeypatch):
    # Apretar un atajo no tiene que bajar 1,6 GB: eso lo decide el primer audio.
    monkeypatch.setattr(transcribe, "_cached", lambda: False)
    monkeypatch.setattr(transcribe, "_start_download", lambda: pytest.fail("no debía bajar nada"))

    assert transcribe.warm() is False
    assert voice.builds == []


def test_warming_without_the_voice_package_is_silent(voice, monkeypatch):
    def missing():
        raise ImportError

    monkeypatch.setattr(transcribe, "_whisper_module", missing)

    assert transcribe.warm() is False


def test_a_model_that_fails_to_load_is_not_an_error_when_warming(voice, monkeypatch):
    def broken():
        raise RuntimeError("sin memoria")

    monkeypatch.setattr(transcribe, "_build_model", broken)

    assert transcribe.warm() is False  # el audio, cuando llegue, mostrará el error de verdad


def test_a_warmed_model_still_gets_released_after_the_idle_time(voice):
    transcribe.warm()

    assert transcribe._timer is not None  # el temporizador de descarga quedó armado
