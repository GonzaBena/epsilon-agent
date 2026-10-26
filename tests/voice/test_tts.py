import json
from pathlib import Path
import pytest

from epsilon.voice import tts


@pytest.fixture
def fake_voices_dir(tmp_path):
    # Crear dos voces simuladas con sus .json
    daniela_onnx = tmp_path / "es_AR-daniela-high.onnx"
    daniela_onnx.write_bytes(b"model data")
    daniela_json = tmp_path / "es_AR-daniela-high.onnx.json"
    daniela_json.write_text(
        json.dumps({
            "dataset": "daniela",
            "audio": {"quality": "high"},
            "language": {
                "code": "es_AR",
                "name_native": "Español",
                "name_english": "Spanish",
            },
        }),
        encoding="utf-8",
    )

    english_onnx = tmp_path / "en_US-hfc_female-medium.onnx"
    english_onnx.write_bytes(b"model data 2")
    english_json = tmp_path / "en_US-hfc_female-medium.onnx.json"
    english_json.write_text(
        json.dumps({
            "dataset": "hfc_female",
            "audio": {"quality": "medium"},
            "language": {
                "code": "en_US",
                "name_native": "English",
                "name_english": "English",
            },
        }),
        encoding="utf-8",
    )
    return tmp_path


def test_list_voices_finds_models_and_parses_metadata(fake_voices_dir):
    voices = tts.list_voices(fake_voices_dir)
    assert len(voices) == 2

    daniela = next(v for v in voices if v["id"] == "es_AR-daniela-high")
    assert daniela["dataset"] == "daniela"
    assert daniela["language"] == "es_AR"
    assert daniela["quality"] == "high"
    assert "Daniela" in daniela["name"]

    english = next(v for v in voices if v["id"] == "en_US-hfc_female-medium")
    assert english["quality"] == "medium"
    assert english["language"] == "en_US"


def test_list_voices_empty_folder(tmp_path):
    assert tts.list_voices(tmp_path / "nonexistent") == []


def test_list_voices_kokoro(monkeypatch):
    monkeypatch.setenv("TTS_ENGINE", "kokoro")
    voices = tts.list_voices()
    assert any(v["id"] == "ef_dora" for v in voices)
    assert any(v["language"] == "es" for v in voices)


def test_default_voice(monkeypatch):
    monkeypatch.delenv("TTS_VOICE", raising=False)
    monkeypatch.setenv("TTS_ENGINE", "kokoro")
    assert tts.default_voice() == "af_bella"

    monkeypatch.setenv("TTS_ENGINE", "piper")
    assert tts.default_voice() == "es_AR-daniela-high"

    monkeypatch.setenv("TTS_VOICE", "otra_voz")
    assert tts.default_voice() == "otra_voz"


def test_default_length_scale_is_slower_at_095(monkeypatch):
    monkeypatch.delenv("TTS_LENGTH_SCALE", raising=False)
    monkeypatch.delenv("TTS_SPEED", raising=False)
    assert tts.length_scale() == 1.0526

    monkeypatch.setenv("TTS_SPEED", "1.0")
    assert tts.length_scale() == 1.0

    monkeypatch.setenv("TTS_SPEED", "0.95")
    assert tts.length_scale() == 1.0526

    monkeypatch.setenv("TTS_LENGTH_SCALE", "1.25")
    assert tts.length_scale() == 1.25

    monkeypatch.setenv("TTS_SPEED", "invalido")
    monkeypatch.delenv("TTS_LENGTH_SCALE", raising=False)
    assert tts.length_scale() == 1.0526


def test_find_voice_path(fake_voices_dir, monkeypatch):
    monkeypatch.setattr(tts, "voices_dir", lambda: fake_voices_dir)

    p = tts.find_voice_path("es_AR-daniela-high")
    assert p.name == "es_AR-daniela-high.onnx"

    # Con .onnx explícito
    p2 = tts.find_voice_path("es_AR-daniela-high.onnx")
    assert p2 == p

    # Inexistente
    with pytest.raises(tts.VoiceNotFoundError):
        tts.find_voice_path("voz_fantasma")


def test_clean_for_speech_markdown_elements():
    raw = (
        "# Título\n"
        "Hola **mundo**, esto es *cursiva* y [un link](https://google.com).\n"
        "```python\nprint(123)\n```\n"
        "Mirá el `código inline`.\n"
        "- Elemento 1\n"
        "- Elemento 2\n"
        "1. Primero\n"
        "2. Segundo\n"
        "| Col1 | Col2 |\n"
        "| --- | --- |\n"
        "| Val1 | Val2 |\n"
        "Fin con emoji 🚀🎉!"
    )
    cleaned = tts.clean_for_speech(raw)

    assert "Título" in cleaned
    assert "#" not in cleaned
    assert "Hola mundo, esto es cursiva y un link." in cleaned
    assert "**" not in cleaned
    assert "(bloque de código)" in cleaned
    assert "código inline" in cleaned
    assert "`" not in cleaned
    assert "Val1, Val2" in cleaned
    assert "|" not in cleaned
    assert "🚀" not in cleaned
    assert "🎉" not in cleaned


def test_clean_for_speech_truncation():
    long_text = "Esta es una frase completa. " * 30  # ~840 caracteres
    cleaned = tts.clean_for_speech(long_text, max_chars=100)
    assert len(cleaned) <= 100
    assert cleaned.endswith(".") or cleaned.endswith("...")


def test_clean_for_speech_empty():
    assert tts.clean_for_speech("") == ""
    assert tts.clean_for_speech("   \n\t  ") == ""


def test_piper_binary_missing(monkeypatch):
    monkeypatch.setattr(tts.shutil, "which", lambda cmd: None)
    with pytest.raises(tts.PiperMissingError):
        tts.piper_binary()


def test_synthesize_flow(fake_voices_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_ENGINE", "piper")
    monkeypatch.setattr(tts, "voices_dir", lambda: fake_voices_dir)

    piper_called = []

    def fake_run_piper(text, model_path, wav_output):
        piper_called.append((text, model_path, wav_output))
        wav_output.write_bytes(b"RIFF fake wav data")

    def fake_wav_to_ogg(wav_path, ogg_path):
        ogg_path.write_bytes(b"OggS fake opus data")

    monkeypatch.setattr(tts, "_run_piper", fake_run_piper)
    monkeypatch.setattr(tts, "_wav_to_ogg", fake_wav_to_ogg)

    result_path = tts.synthesize("Hola mundo", voice_id="es_AR-daniela-high")
    assert result_path.is_file()
    assert result_path.suffix == ".ogg"
    assert piper_called[0][0] == "Hola mundo"
    assert piper_called[0][1].name == "es_AR-daniela-high.onnx"

    # Limpiar archivo generado
    result_path.unlink()


def test_synthesize_kokoro_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_ENGINE", "kokoro")

    kokoro_called = []

    def fake_kokoro_synthesize(text, voice="ef_dora", speed=1.0):
        kokoro_called.append((text, voice, speed))
        fake_wav = tmp_path / "kokoro.wav"
        fake_wav.write_bytes(b"RIFF fake kokoro wav")
        return fake_wav

    def fake_wav_to_ogg(wav_path, ogg_path):
        ogg_path.write_bytes(b"OggS fake kokoro ogg")

    monkeypatch.setattr(tts.kokoro_engine, "synthesize", fake_kokoro_synthesize)
    monkeypatch.setattr(tts, "_wav_to_ogg", fake_wav_to_ogg)

    result_path = tts.synthesize("Hola companion", voice_id="ef_dora")
    assert result_path.is_file()
    assert result_path.suffix == ".ogg"
    assert kokoro_called[0][0] == "Hola companion"
    assert kokoro_called[0][1] == "ef_dora"

    result_path.unlink()


def test_synthesize_empty_text():
    with pytest.raises(ValueError):
        tts.synthesize("   \n\t  ")


def test_audio_cache(tmp_path):
    file1 = tmp_path / "audio1.ogg"
    file1.write_bytes(b"123")

    token = tts.cache_audio(file1)
    assert tts.get_cached_audio(token) == file1
    assert tts.get_cached_audio("token_invalido") is None
