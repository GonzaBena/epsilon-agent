from pathlib import Path
import shutil
import tempfile
import urllib.request

from epsilon.settings import config

try:
    import kokoro_onnx
    import soundfile as sf
except ImportError:
    kokoro_onnx = None  # type: ignore
    sf = None  # type: ignore

DEFAULT_KOKORO_DIR = Path.home() / ".local/share/kokoro-models"
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

MODEL_FILENAME = "kokoro-v1.0.onnx"
VOICES_FILENAME = "voices-v1.0.bin"

DEFAULT_KOKORO_VOICE = "af_bella"

# Catálogo completo de las 54 voces oficiales de Kokoro
KOKORO_VOICES = [
    # Recomendadas / Destacadas para Companion
    {"id": "af_bella", "name": "Bella (EE.UU. - Femenina, muy dulce y clara)", "category": "Recomendadas", "gender": "female"},
    {"id": "af_sarah", "name": "Sarah (EE.UU. - Femenina, cálida y natural)", "category": "Recomendadas", "gender": "female"},
    {"id": "af_nicole", "name": "Nicole (EE.UU. - Femenina, suave y relajada)", "category": "Recomendadas", "gender": "female"},
    {"id": "af_sky", "name": "Sky (EE.UU. - Femenina, juvenil y vivaz)", "category": "Recomendadas", "gender": "female"},
    {"id": "jf_alpha", "name": "Alpha (Japonesa - Femenina, estilo anime/companion)", "category": "Recomendadas", "gender": "female"},
    {"id": "ef_dora", "name": "Dora (Español - Femenina estándar)", "category": "Español", "gender": "female"},
    {"id": "em_alex", "name": "Alex (Español - Masculina)", "category": "Español", "gender": "male"},
    {"id": "em_santa", "name": "Santa (Español - Masculina)", "category": "Español", "gender": "male"},
    # Inglés Americano (Femeninas)
    {"id": "af_heart", "name": "Heart (EE.UU. - Femenina)", "category": "Inglés EE.UU.", "gender": "female"},
    {"id": "af_alloy", "name": "Alloy (EE.UU. - Femenina)", "category": "Inglés EE.UU.", "gender": "female"},
    {"id": "af_aoede", "name": "Aoede (EE.UU. - Femenina)", "category": "Inglés EE.UU.", "gender": "female"},
    {"id": "af_jessica", "name": "Jessica (EE.UU. - Femenina)", "category": "Inglés EE.UU.", "gender": "female"},
    {"id": "af_kore", "name": "Kore (EE.UU. - Femenina)", "category": "Inglés EE.UU.", "gender": "female"},
    {"id": "af_nova", "name": "Nova (EE.UU. - Femenina)", "category": "Inglés EE.UU.", "gender": "female"},
    {"id": "af_river", "name": "River (EE.UU. - Femenina)", "category": "Inglés EE.UU.", "gender": "female"},
    # Inglés Americano (Masculinas)
    {"id": "am_adam", "name": "Adam (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    {"id": "am_echo", "name": "Echo (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    {"id": "am_eric", "name": "Eric (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    {"id": "am_fenrir", "name": "Fenrir (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    {"id": "am_liam", "name": "Liam (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    {"id": "am_michael", "name": "Michael (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    {"id": "am_onyx", "name": "Onyx (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    {"id": "am_puck", "name": "Puck (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    {"id": "am_santa", "name": "Santa (EE.UU. - Masculina)", "category": "Inglés EE.UU.", "gender": "male"},
    # Inglés Británico
    {"id": "bf_alice", "name": "Alice (Británica - Femenina)", "category": "Inglés Británico", "gender": "female"},
    {"id": "bf_emma", "name": "Emma (Británica - Femenina)", "category": "Inglés Británico", "gender": "female"},
    {"id": "bf_isabella", "name": "Isabella (Británica - Femenina)", "category": "Inglés Británico", "gender": "female"},
    {"id": "bf_lily", "name": "Lily (Británica - Femenina)", "category": "Inglés Británico", "gender": "female"},
    {"id": "bm_daniel", "name": "Daniel (Británico - Masculina)", "category": "Inglés Británico", "gender": "male"},
    {"id": "bm_fable", "name": "Fable (Británico - Masculina)", "category": "Inglés Británico", "gender": "male"},
    {"id": "bm_george", "name": "George (Británico - Masculina)", "category": "Inglés Británico", "gender": "male"},
    {"id": "bm_lewis", "name": "Lewis (Británico - Masculina)", "category": "Inglés Británico", "gender": "male"},
    # Japonés
    {"id": "jf_gongitsune", "name": "Gongitsune (Japonesa - Femenina)", "category": "Japonés", "gender": "female"},
    {"id": "jf_nezumi", "name": "Nezumi (Japonesa - Femenina)", "category": "Japonés", "gender": "female"},
    {"id": "jf_tebukuro", "name": "Tebukuro (Japonesa - Femenina)", "category": "Japonés", "gender": "female"},
    {"id": "jm_kumo", "name": "Kumo (Japonés - Masculina)", "category": "Japonés", "gender": "male"},
    # Italiano y Francés
    {"id": "if_sara", "name": "Sara (Italiana - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    {"id": "im_nicola", "name": "Nicola (Italiano - Masculina)", "category": "Otros Idiomas", "gender": "male"},
    {"id": "ff_siwis", "name": "Siwis (Francesa - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    # Portugués
    {"id": "pf_dora", "name": "Dora (Portuguesa - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    {"id": "pm_alex", "name": "Alex (Portugués - Masculina)", "category": "Otros Idiomas", "gender": "male"},
    {"id": "pm_santa", "name": "Santa (Portugués - Masculina)", "category": "Otros Idiomas", "gender": "male"},
    # Hindi
    {"id": "hf_alpha", "name": "Alpha (Hindi - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    {"id": "hf_beta", "name": "Beta (Hindi - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    {"id": "hm_omega", "name": "Omega (Hindi - Masculina)", "category": "Otros Idiomas", "gender": "male"},
    {"id": "hm_psi", "name": "Psi (Hindi - Masculina)", "category": "Otros Idiomas", "gender": "male"},
    # Chino Mandarín
    {"id": "zf_xiaobei", "name": "Xiaobei (China - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    {"id": "zf_xiaoni", "name": "Xiaoni (China - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    {"id": "zf_xiaoxiao", "name": "Xiaoxiao (China - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    {"id": "zf_xiaoyi", "name": "Xiaoyi (China - Femenina)", "category": "Otros Idiomas", "gender": "female"},
    {"id": "zm_yunjian", "name": "Yunjian (Chino - Masculina)", "category": "Otros Idiomas", "gender": "male"},
    {"id": "zm_yunxi", "name": "Yunxi (Chino - Masculina)", "category": "Otros Idiomas", "gender": "male"},
    {"id": "zm_yunxia", "name": "Yunxia (Chino - Masculina)", "category": "Otros Idiomas", "gender": "male"},
    {"id": "zm_yunyang", "name": "Yunyang (Chino - Masculina)", "category": "Otros Idiomas", "gender": "male"},
]

_KOKORO_INSTANCE = None


def models_dir() -> Path:
    """Carpeta donde residen el modelo ONNX y el archivo voices.bin de Kokoro."""
    custom = config.value("KOKORO_MODELS_DIR").strip()
    return Path(custom).expanduser() if custom else DEFAULT_KOKORO_DIR


def model_path() -> Path:
    return models_dir() / MODEL_FILENAME


def voices_path() -> Path:
    return models_dir() / VOICES_FILENAME


def are_models_present() -> bool:
    """Comprueba si ambos archivos de pesos existen y tienen tamaño válido."""
    m = model_path()
    v = voices_path()
    return m.is_file() and m.stat().st_size > 1_000_000 and v.is_file() and v.stat().st_size > 1_000_000


def is_available() -> bool:
    """Indica si el paquete kokoro-onnx está instalado y los modelos descargados."""
    return kokoro_onnx is not None and are_models_present()


def download_models(force: bool = False) -> None:
    """Descarga los modelos ONNX y binario de voces si no existen."""
    target_dir = models_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    items = [
        (MODEL_URL, model_path()),
        (VOICES_URL, voices_path()),
    ]

    for url, dest in items:
        if dest.is_file() and not force:
            continue
        tmp_dest = dest.with_suffix(".tmp")
        print(f"Descargando {dest.name} desde {url}...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Epsilon/1.0"})
            with urllib.request.urlopen(req) as resp, open(tmp_dest, "wb") as out_f:
                shutil.copyfileobj(resp, out_f)
            tmp_dest.replace(dest)
            print(f"✓ {dest.name} descargado correctamente.")
        except Exception as e:
            tmp_dest.unlink(missing_ok=True)
            raise RuntimeError(f"Error al descargar {dest.name}: {e}") from e


def get_kokoro() -> "kokoro_onnx.Kokoro":
    """Obtiene o inicializa la instancia singleton de Kokoro ONNX."""
    global _KOKORO_INSTANCE
    if _KOKORO_INSTANCE is not None:
        return _KOKORO_INSTANCE

    if kokoro_onnx is None:
        raise ImportError(
            "El paquete 'kokoro-onnx' no está instalado. Instalalo con: uv sync --extra voz"
        )

    if not are_models_present():
        # Intentar descarga automática
        print("Modelos de Kokoro no encontrados. Iniciando descarga automática...")
        download_models()

    _KOKORO_INSTANCE = kokoro_onnx.Kokoro(
        model_path=str(model_path()),
        voices_path=str(voices_path()),
    )
    return _KOKORO_INSTANCE


def list_kokoro_voices() -> list[dict]:
    """Lista las voces de Kokoro disponibles."""
    lang_map = {
        "a": "en",
        "b": "en",
        "e": "es",
        "f": "fr",
        "h": "hi",
        "i": "it",
        "j": "ja",
        "p": "pt",
        "z": "zh",
    }
    result = []
    for v in KOKORO_VOICES:
        item = dict(v)
        item["engine"] = "kokoro"
        item["language"] = lang_map.get(item["id"][0], "es")
        result.append(item)
    return result


def resolve_voice_name(kokoro: "kokoro_onnx.Kokoro", name: str) -> str:
    """Busca coincidencia exacta, por prefijo o insensible a mayúsculas entre las 54 voces de Kokoro."""
    target = name.strip()
    try:
        available = kokoro.get_voices()
    except Exception:
        return target or DEFAULT_KOKORO_VOICE

    if target in available:
        return target
    lower = target.lower()
    for v in available:
        if v.lower() == lower:
            return v
    for v in available:
        if v.lower().endswith(f"_{lower}"):
            return v
    print(f"Aviso: La voz '{name}' no existe en Kokoro. Usando '{DEFAULT_KOKORO_VOICE}' por defecto.")
    return DEFAULT_KOKORO_VOICE


def resolve_voice_style(kokoro: "kokoro_onnx.Kokoro", voice_spec: str):
    """Resuelve un nombre de voz simple o una combinación ponderada (ej: 'af_bella:70,ef_dora:30' o 'af_bella+ef_dora')."""
    spec = voice_spec.strip()
    if not ("," in spec or "+" in spec or ":" in spec):
        return resolve_voice_name(kokoro, spec)

    parts = spec.replace("+", ",").split(",")
    items = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if ":" in p:
            name, weight_str = p.split(":", 1)
            try:
                w = float(weight_str.strip())
            except ValueError:
                w = 1.0
            items.append((resolve_voice_name(kokoro, name.strip()), w))
        else:
            items.append((resolve_voice_name(kokoro, p.strip()), 1.0))

    total_w = sum(w for _, w in items)
    if total_w <= 0:
        return DEFAULT_KOKORO_VOICE

    blended = None
    for name, weight in items:
        try:
            style = kokoro.get_voice_style(name)
        except Exception:
            continue
        norm_w = weight / total_w
        blended = (norm_w * style) if blended is None else (blended + norm_w * style)

    return blended if blended is not None else DEFAULT_KOKORO_VOICE


def synthesize_samples(text: str, voice: str = "af_bella", speed: float = 1.0):
    """Sintetiza texto con Kokoro ONNX y devuelve (muestras, frecuencia), sin pasar por un archivo."""
    if not text.strip():
        raise ValueError("El texto está vacío o no contiene palabras pronunciables.")

    kokoro = get_kokoro()
    voice_name = voice.strip() or DEFAULT_KOKORO_VOICE

    # El fonetizador para pronunciar en español debe ser 'es'
    lang = config.value("TTS_LANG").strip() or "es"
    resolved_voice = resolve_voice_style(kokoro, voice_name)

    return kokoro.create(
        text=text,
        voice=resolved_voice,
        speed=speed,
        lang=lang,
    )


def synthesize(text: str, voice: str = "af_bella", speed: float = 1.0) -> Path:
    """Sintetiza texto a audio WAV utilizando Kokoro ONNX."""
    if not text.strip():
        raise ValueError("El texto está vacío o no contiene palabras pronunciables.")

    if sf is None:
        raise ImportError("El paquete 'soundfile' es necesario para guardar audio.")

    samples, sample_rate = synthesize_samples(text, voice, speed)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        wav_path = Path(tmp_wav.name)

    sf.write(str(wav_path), samples, sample_rate)
    return wav_path
