import re

from epsilon.channels import web
from tests.web.helpers import js, transcribe_audio


# --- Voz: /api/transcribe ----------------------------------------------------


def test_transcribe_returns_the_text_without_running_it(client, tmp_path, fake_voice):
    reply = transcribe_audio(client)

    assert reply.status_code == 200
    assert reply.json() == {"text": "más comprar pan"}
    assert fake_voice["bytes"] == b"audio"
    assert not (tmp_path / "inbox.md").exists()  # solo transcribe: el texto no entra al núcleo
    assert not (tmp_path / "conversations").exists()


def test_transcribe_deletes_the_temp_file_and_keeps_audio_out_of_data_dir(client, tmp_path, fake_voice):
    transcribe_audio(client)

    assert not fake_voice["path"].exists()
    assert tmp_path not in fake_voice["path"].parents


def test_transcribe_deletes_the_temp_file_even_when_it_fails(client, monkeypatch):
    seen = {}

    def boom(path):
        seen["path"] = path
        raise web.transcribe.VoiceError("No entendí nada en el audio.")

    monkeypatch.setattr(web.transcribe, "audio_to_text", boom)
    reply = transcribe_audio(client)

    assert reply.status_code == 422
    assert reply.json() == {"detail": "No entendí nada en el audio."}
    assert not seen["path"].exists()


def test_transcribe_voice_problems_are_shown_as_is(client, monkeypatch):
    for exc in (web.transcribe.VoiceNotInstalled, web.transcribe.VoiceDownloading):
        monkeypatch.setattr(
            web.transcribe, "audio_to_text", lambda path, exc=exc: (_ for _ in ()).throw(exc("aviso claro"))
        )
        reply = transcribe_audio(client)
        assert reply.status_code == 422 and reply.json() == {"detail": "aviso claro"}


def test_transcribe_unexpected_errors_do_not_leak_details(client, monkeypatch):
    def boom(path):
        raise RuntimeError("clave AIzaSECRETA en /ruta/interna")

    monkeypatch.setattr(web.transcribe, "audio_to_text", boom)
    reply = transcribe_audio(client)

    assert reply.status_code == 500
    assert reply.json() == {"detail": "No pude procesar el audio."}
    assert "SECRETA" not in reply.text


def test_transcribe_accepts_only_audio(client, fake_voice):
    for mime in ("image/png", "application/pdf", "text/plain", ""):
        assert transcribe_audio(client, mime=mime).status_code == 415
    assert "path" not in fake_voice


def test_transcribe_rejects_big_audio_before_transcribing(client, monkeypatch, fake_voice):
    monkeypatch.setattr(web, "MAX_UPLOAD", 10)
    reply = transcribe_audio(client, data=b"x" * 11)

    assert reply.status_code == 413
    assert "path" not in fake_voice


def test_transcribe_without_file_is_rejected(client):
    assert client.post("/api/transcribe", data={"text": "hola"}).status_code == 422


def test_foreign_origin_cannot_transcribe(client, fake_voice):
    reply = transcribe_audio(client, headers={"Origin": "http://evil.com"})

    assert reply.status_code == 403
    assert "path" not in fake_voice


def test_foreign_host_cannot_transcribe(client, fake_voice):
    reply = transcribe_audio(client, headers={"Host": "evil.com"})

    assert reply.status_code == 403
    assert "path" not in fake_voice


def test_mic_button_dictates_into_the_box_without_sending(client):
    html = client.get("/").text
    js = (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert 'id="mic"' in html and 'id="mic-status"' in html
    assert 'role="status"' in html  # los avisos se anuncian a lectores de pantalla
    assert "/api/transcribe" in js
    # El texto transcripto va al cuadro (insertText) y la grabación nunca llama a submit/ask.
    voice = js[js.index("async function sendAudio") : js.index("// --- Comandos con un toque")]
    assert "insertText(" in voice
    assert "submit(" not in voice and "ask(" not in voice and "/api/message" not in voice
    assert "innerHTML" not in voice


def test_mic_button_stays_hidden_if_the_browser_cannot_record(client):
    assert 'id="mic"' in client.get("/").text
    mic_tag = client.get("/").text.split('id="mic"', 1)[1].split(">", 1)[0]
    assert "hidden" in mic_tag  # arranca oculto; app.js lo muestra solo si hay MediaRecorder
    js = (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "window.MediaRecorder" in js and "getUserMedia" in js


def test_mic_stops_the_microphone_when_the_recording_ends(client):
    assert "track.stop()" in (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")  # si no, el navegador deja el micrófono prendido


# --- Voz apagada: vale para la web igual que para Telegram -------------------


def test_transcribe_refuses_when_voice_is_off(client, monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    reply = transcribe_audio(client)

    assert reply.status_code == 422
    assert reply.json()["detail"] == web.transcribe.VOICE_OFF


def test_the_api_says_voice_is_off_and_the_mic_button_follows_it(client, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    js = (web.STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert client.get("/api/config").json()["voice"] == "apagada"
    assert 'state.voice === "apagada"' in js and "voiceOff" in js  # sin botón de micrófono
    assert "apagada" in (web.STATIC_DIR / "config.js").read_text(encoding="utf-8")


# --- Borrar el modelo de voz ---------------------------------------------------


def test_the_api_reports_the_model_size_only_when_epsilon_downloaded_it(client, monkeypatch):
    assert client.get("/api/config").json()["voice_model_bytes"] is None

    monkeypatch.setattr(web.transcribe, "deletable_model", lambda: 1_700_000_000)

    assert client.get("/api/config").json()["voice_model_bytes"] == 1_700_000_000


def test_deleting_the_model_returns_the_new_state(client, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "no")
    calls = []
    monkeypatch.setattr(web.transcribe, "delete_model", lambda: calls.append(1) or 5)

    reply = client.post("/api/voice/delete-model")

    assert reply.status_code == 200 and calls == [1]
    assert reply.json()["voice"] == "apagada"


def test_deleting_the_model_with_the_voice_on_is_refused_and_touches_nothing(client, tmp_path):
    reply = client.post("/api/voice/delete-model")  # sin marca ni voz apagada

    assert reply.status_code == 409 and reply.json()["detail"]


def test_deleting_the_model_asks_for_a_second_tap_and_is_hidden_otherwise():
    js_text = (web.STATIC_DIR / "config.js").read_text(encoding="utf-8")
    html = (web.STATIC_DIR / "config.html").read_text(encoding="utf-8")

    assert 'id="voice-model"' in html and re.search(r'id="voice-model"[^>]*hidden', html)
    assert "deleteArmed" in js_text and "¿Seguro?" in js_text
    assert 'state.voice === "apagada" && bytes' in js_text  # solo con la voz apagada


def test_deleting_the_model_is_blocked_for_foreign_origins(client):
    reply = client.post("/api/voice/delete-model", headers={"Origin": "http://evil.example"})

    assert reply.status_code == 403
