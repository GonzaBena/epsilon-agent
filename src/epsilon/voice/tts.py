import collections
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

# Módulo de salida de voz (Text-to-Speech / TTS) con Kokoro y Piper.
# Sintetiza respuestas de texto a audio localmente sin gastar tokens ni enviar datos afuera.
# Complementa a transcribe.py (Whisper local para entrada).

from epsilon.settings import config
from epsilon.voice import kokoro_engine

DEFAULT_ENGINE = "kokoro"
DEFAULT_KOKORO_VOICE = "af_bella"
DEFAULT_PIPER_VOICE = "es_AR-daniela-high"
DEFAULT_VOICE = DEFAULT_KOKORO_VOICE
DEFAULT_VOICES_DIR = Path.home() / ".local/share/piper-voices"
DEFAULT_SPEED = 1.0
DEFAULT_LENGTH_SCALE = 1.0526  # 1 / 0.95 (velocidad 0.95 en Piper)

# Cache en memoria de archivos de audio temporales para servir por la API web.
# token -> (Path, timestamp). Máximo 50 audios.
_AUDIO_CACHE: collections.OrderedDict[str, tuple[Path, float]] = collections.OrderedDict()
MAX_CACHE_ITEMS = 50


class TTSError(Exception):
    """Error general de síntesis de voz."""


class PiperMissingError(TTSError):
    """No se encontró piper-tts instalado en el sistema."""


class KokoroMissingError(TTSError):
    """No se encontró kokoro-onnx o sus modelos en el sistema."""


class VoiceNotFoundError(TTSError):
    """No se encontró el modelo de voz solicitado."""


def engine() -> str:
    """Motor de voz activo: 'kokoro' o 'piper'."""
    eng = config.value("TTS_ENGINE").strip().lower()
    return eng if eng in ("kokoro", "piper") else DEFAULT_ENGINE


def is_installed() -> bool:
    """Verifica si las librerías del motor TTS activo están disponibles."""
    if engine() == "piper":
        return bool(shutil.which("piper-tts") or shutil.which("piper"))
    return kokoro_engine.kokoro_onnx is not None


def voices_dir() -> Path:
    """Carpeta donde residen los modelos onnx de voces de Piper."""
    custom = config.value("TTS_VOICES_DIR").strip()
    return Path(custom).expanduser() if custom else DEFAULT_VOICES_DIR


def list_voices(custom_dir: Path | None = None) -> list[dict]:
    """Lista las voces disponibles según el motor activo o carpeta."""
    eng = engine()
    if eng == "kokoro" and custom_dir is None:
        return kokoro_engine.list_kokoro_voices()

    folder = custom_dir or voices_dir()
    if not folder.is_dir():
        return []

    found = []
    for model_path in sorted(folder.glob("*.onnx")):
        json_path = model_path.with_name(f"{model_path.name}.json")
        if not json_path.exists():
            # Algunos modelos usan .json directo sin repetir .onnx
            json_path = model_path.with_suffix(".json")

        meta = {}
        if json_path.exists():
            try:
                meta = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        voice_id = model_path.stem
        dataset = meta.get("dataset") or voice_id
        lang_info = meta.get("language") or {}
        lang_code = lang_info.get("code") if isinstance(lang_info, dict) else ""
        lang_name = lang_info.get("name_native") or lang_info.get("name_english") or ""
        quality = (meta.get("audio") or {}).get("quality", "")

        label_parts = [dataset.capitalize()]
        if lang_code:
            label_parts.append(f"({lang_code})")
        if quality:
            label_parts.append(f"[{quality}]")

        label = " ".join(label_parts) if label_parts else voice_id

        found.append({
            "id": voice_id,
            "name": label,
            "dataset": dataset,
            "language": lang_code,
            "language_name": lang_name,
            "quality": quality,
            "path": str(model_path.resolve()),
            "engine": "piper",
        })

    return found


def default_voice() -> str:
    """Nombre o id de la voz configurada por defecto."""
    custom = config.value("TTS_VOICE").strip()
    # El valor de fábrica del ajuste es la voz de Kokoro: con Piper, si nadie eligió otra, rige la de Piper.
    if custom and custom != config.BY_KEY["TTS_VOICE"].default:
        return custom
    return DEFAULT_PIPER_VOICE if engine() == "piper" else DEFAULT_KOKORO_VOICE


def find_voice_path(voice_id: str | None = None, custom_dir: Path | None = None) -> Path:
    """Obtiene la ruta absoluta al archivo .onnx de la voz."""
    target = voice_id.strip() if voice_id else default_voice()
    folder = custom_dir or voices_dir()

    # Si pasaron una ruta completa que existe
    as_path = Path(target).expanduser()
    if as_path.is_file():
        return as_path

    # Buscar por id (nombre de archivo sin .onnx o con .onnx)
    candidates = [
        folder / f"{target}.onnx",
        folder / target,
    ]
    for c in candidates:
        if c.is_file():
            return c

    # Búsqueda insensible a mayúsculas o coincidencia parcial
    if folder.is_dir():
        for p in folder.glob("*.onnx"):
            if p.stem.lower() == target.lower():
                return p

    raise VoiceNotFoundError(f"No encontré el modelo de voz «{target}» en {folder}.")


def clean_for_speech(text: str, max_chars: int | None = None) -> str:
    """Limpia markdown, código, tablas y enlaces para que suene natural al hablar."""
    if not text:
        return ""

    s = text

    # Bloques de código ```...``` -> omitir o resumir
    s = re.sub(r"```[\s\S]*?```", " (bloque de código) ", s)

    # Código inline `...` -> texto interior
    s = re.sub(r"`([^`]+)`", r"\1", s)

    # Imágenes ![alt](url) -> ""
    s = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", s)

    # Enlaces [texto](url) -> texto
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)

    # URLs directas
    s = re.sub(r"https?://\S+", "enlace", s)

    # Encabezados markdown (# Titulo)
    s = re.sub(r"^#+\s*", "", s, flags=re.MULTILINE)

    # Tablas markdown: líneas con pipes (| a | b |) -> convertir a texto separado por coma
    def _clean_table_line(match):
        cells = [c.strip() for c in match.group(0).split("|") if c.strip() and not re.fullmatch(r"[-:]+", c.strip())]
        return ", ".join(cells)

    s = re.sub(r"^\|.*\|$", _clean_table_line, s, flags=re.MULTILINE)

    # Negrita y cursiva (**texto**, *texto*, __texto__)
    s = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", s)

    # Tachado (~~texto~~)
    s = re.sub(r"~~([^~]+)~~", r"\1", s)

    # Listas: viñetas iniciales (- item, * item) -> item
    s = re.sub(r"^[\s]*[-*+]\s+", "", s, flags=re.MULTILINE)

    # Números de lista (1. item) -> 1, item (para mejor pausa)
    s = re.sub(r"^[\s]*(\d+)\.\s+", r"\1, ", s, flags=re.MULTILINE)

    # Líneas separadoras (--- o ***)
    s = re.sub(r"^[-*_]{3,}\s*$", "", s, flags=re.MULTILINE)

    # Emojis habituales o caracteres decorativos que piper no lee
    s = re.sub(r"[\U00010000-\U0010ffff]", "", s)

    # Normalizar espacios y saltos de línea repetidos
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    s = s.strip()

    limit = max_chars
    if limit is None:
        limit = config.number("TTS_MAX_CHARS")

    if len(s) > limit:
        # Cortar en el último punto, coma o espacio antes del límite
        cut = s[:limit]
        last_punct = max(cut.rfind("."), cut.rfind(","), cut.rfind("!"), cut.rfind("?"))
        if last_punct > limit // 2:
            s = cut[: last_punct + 1]
        else:
            s = cut.rstrip() + "..."

    return s


def piper_binary() -> str:
    """Busca el ejecutable de piper-tts o piper en el PATH."""
    bin_path = shutil.which("piper-tts") or shutil.which("piper")
    if not bin_path:
        raise PiperMissingError("No se encontró el ejecutable piper-tts en el sistema.")
    return bin_path


def speed() -> float:
    """Velocidad de habla para la síntesis (multiplicador estándar, ej: 1.0)."""
    val = config.number("TTS_SPEED")  # None: vacío o roto, se usa el de siempre
    return DEFAULT_SPEED if val is None else val


def length_scale() -> float:
    """Multiplicador de duración de fonemas para modular la velocidad del habla en Piper."""
    if (scale := config.number("TTS_LENGTH_SCALE")) is not None:
        return scale
    if (val := config.number("TTS_SPEED")) is not None:
        return round(1.0 / val, 4)
    return DEFAULT_LENGTH_SCALE


def _run_piper(text: str, model_path: Path, wav_output: Path) -> None:
    """Ejecuta el binario de piper-tts para generar un archivo WAV."""
    binary = piper_binary()
    scale = str(length_scale())
    cmd = [binary, "-m", str(model_path), "--length-scale", scale, "-f", str(wav_output)]
    proc = subprocess.run(
        cmd,
        input=text.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise TTSError(f"Error al ejecutar piper-tts: {err or 'proceso falló'}")


def _wav_to_ogg(wav_path: Path, ogg_path: Path) -> None:
    """Convierte un WAV a OGG Opus para que sea óptimo en Telegram y navegadores."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        cmd = [ffmpeg_bin, "-y", "-i", str(wav_path), "-c:a", "libopus", "-b:a", "32k", str(ogg_path)]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode == 0 and ogg_path.is_file():
            return

    # Si ffmpeg no está o falló, intentamos con PyAV (av)
    try:
        import av

        input_container = av.open(str(wav_path))
        output_container = av.open(str(ogg_path), "w", format="ogg")
        in_stream = input_container.streams.audio[0]
        out_stream = output_container.add_stream("libopus", rate=24000)

        for frame in input_container.decode(in_stream):
            for packet in out_stream.encode(frame):
                output_container.mux(packet)
        for packet in out_stream.encode():
            output_container.mux(packet)

        input_container.close()
        output_container.close()
        return
    except Exception as e:
        # Si fallan ambos, copiamos el WAV como fallback
        shutil.copy(wav_path, ogg_path)


def synthesize(text: str, voice_id: str | None = None) -> Path:
    """Sintetiza el texto y devuelve la ruta al archivo temporal de audio (.ogg)."""
    cleaned = clean_for_speech(text)
    if not cleaned:
        raise ValueError("El texto está vacío o no contiene palabras pronunciables.")

    eng = engine()
    if eng == "kokoro":
        voice = voice_id or default_voice()
        spd = speed()
        wav_path = kokoro_engine.synthesize(cleaned, voice=voice, speed=spd)
        ogg_path = wav_path.with_suffix(".ogg")
        try:
            _wav_to_ogg(wav_path, ogg_path)
            return ogg_path
        finally:
            wav_path.unlink(missing_ok=True)

    # Motor Piper
    model_path = find_voice_path(voice_id)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        wav_path = Path(tmp_wav.name)

    ogg_path = wav_path.with_suffix(".ogg")
    try:
        _run_piper(cleaned, model_path, wav_path)
        _wav_to_ogg(wav_path, ogg_path)
        return ogg_path
    finally:
        wav_path.unlink(missing_ok=True)


MAX_SPOKEN_CHARS = 5000  # tope de UNA oración al hablar al vuelo (el Voice Companion corta el texto antes en oraciones)


def synthesize_pcm(text: str, voice: str | None = None, speed: float | None = None) -> tuple[bytes, int]:
    """Sintetiza el texto con Kokoro y devuelve (audio, frecuencia): PCM de 16 bits little-endian, mono, en memoria. Es lo que
    necesita el Voice Companion para reproducir al vuelo, oración por oración. Lanza ValueError si no hay nada que decir."""
    cleaned = clean_for_speech(text, max_chars=MAX_SPOKEN_CHARS)
    if not cleaned:
        raise ValueError("El texto está vacío o no contiene palabras pronunciables.")
    samples, rate = kokoro_engine.synthesize_samples(cleaned, voice=voice or default_voice(), speed=speed or DEFAULT_SPEED)
    import numpy as np  # viene con kokoro-onnx (extra «voz»)

    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767).astype("<i2").tobytes(), int(rate)


def cache_audio(audio_path: Path) -> str:
    """Guarda un audio en la caché efímera y devuelve su token."""
    token = uuid.uuid4().hex
    _AUDIO_CACHE[token] = (audio_path, time.monotonic())
    while len(_AUDIO_CACHE) > MAX_CACHE_ITEMS:
        old_token, (old_path, _) = _AUDIO_CACHE.popitem(last=False)
        old_path.unlink(missing_ok=True)
    return token


def get_cached_audio(token: str) -> Path | None:
    """Recupera la ruta de un audio en caché si aún existe."""
    entry = _AUDIO_CACHE.get(token)
    if entry and entry[0].is_file():
        return entry[0]
    return None
