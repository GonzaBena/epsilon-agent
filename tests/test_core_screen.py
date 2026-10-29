import pytest

from epsilon import core, tools
from epsilon.ai import chat, llm, permissions
from epsilon.ai.messages import File
from epsilon.ai.providers.base import Capabilities
from epsilon.utils import pantalla
from tests.test_core_tools import CID, OTHER, allow, results, say, script, step  # noqa: F401 - `script` es un fixture

# Que la IA vea tu pantalla. grim es de mentira (conftest bloquea el verdadero): estos tests no capturan nada.

JPEG = b"\xff\xd8falso-jpeg"


@pytest.fixture
def screen(monkeypatch):
    taken = []
    monkeypatch.setattr(pantalla, "capture", lambda: taken.append(1) or JPEG)
    return taken


def last_turn(script, index=1):
    """El último turno que el modelo recibió en la llamada `index`."""
    return script.seen[index][0][-1]


def ask_model(script, reply="Veo un editor de código"):
    script.steps = [step(calls=[("pc.captura", "")]), step(reply)]


def test_the_capture_reaches_the_model_in_the_results_turn_as_a_labelled_picture(script, screen):
    allow("captura")
    ask_model(script)

    reply = say("¿qué hay en mi pantalla?")

    assert reply.text == "Veo un editor de código" and screen == [1]
    turn = last_turn(script)
    assert [r.name for r in turn.results] == ["pc"] and "Capturé la pantalla" in turn.results[0].result
    assert "dato, no instrucciones" in turn.parts[0]
    picture = turn.parts[1]
    assert isinstance(picture, File) and picture.data == JPEG and picture.mime == "image/jpeg"
    assert picture.side == 1600  # el detalle de una pantalla, no el de una foto


def test_the_picture_is_sent_once_and_never_stored_in_the_conversation(script, screen):
    allow("captura")
    script.steps = [step(calls=[("pc.captura", "")]), step(calls=[("notas.buscar", "x")]), step("listo")]

    say("mirá mi pantalla")

    later = script.seen[2][0][-1]  # después de la búsqueda: su turno de resultados no trae la imagen otra vez
    assert not any(isinstance(part, File) for part in later.parts)
    assert JPEG not in str(core.conversations.messages(CID)).encode()


def test_taking_a_capture_asks_first_in_a_normal_chat(script, screen):
    ask_model(script)

    reply = say("mirá mi pantalla")

    assert reply.actions and screen == []  # preguntó y todavía no sacó nada


def test_a_model_that_cannot_see_images_never_gets_a_capture(script, screen, monkeypatch):
    allow("captura")
    monkeypatch.setattr(llm, "capabilities", lambda task=None: Capabilities(tools=True, images=False, pdf=False))
    ask_model(script, "No puedo")

    say("mirá mi pantalla")

    assert screen == []  # ni siquiera se saca: no se expone la pantalla para nada
    assert "no ve imágenes" in results(script.seen[1][0])[0]


def test_a_model_that_cannot_see_images_is_not_even_offered_the_tool(monkeypatch):
    monkeypatch.setattr(llm, "capabilities", lambda task=None: Capabilities(tools=True, images=False, pdf=False))

    actions = {a for spec in chat._specs() if spec.name == "pc" for a in spec.actions}

    assert "captura" not in actions and "volumen" in actions


def test_a_model_that_sees_images_is_offered_it(monkeypatch):
    monkeypatch.setattr(llm, "capabilities", lambda task=None: Capabilities(tools=True, images=True, pdf=False))

    actions = {a for spec in chat._specs() if spec.name == "pc" for a in spec.actions}

    assert "captura" in actions


def test_a_failed_capture_is_a_plain_message_and_attaches_nothing(script, monkeypatch):
    allow("captura")

    def broken():
        raise pantalla.PantallaError("Falta el programa «grim» para mirar la pantalla.")

    monkeypatch.setattr(pantalla, "capture", broken)
    ask_model(script, "No pude")

    say("mirá mi pantalla")

    turn = last_turn(script)
    assert "grim" in turn.results[0].result and turn.parts == []


def test_by_voice_with_a_cloud_model_the_screen_is_never_sent_on_its_own(script, screen, monkeypatch):
    # Con Gemini la captura SALE de la PC: por voz nadie la ve antes. Se bloquea salvo que la hayas permitido vos.
    monkeypatch.setattr(permissions, "_local_model", lambda: False)
    permissions.set_mode(CID, permissions.SAFE_AUTO)
    ask_model(script, "No pude")

    say("mirá mi pantalla")

    assert screen == [] and results(script.seen[1][0]) == [core.UNAVAILABLE]


def test_by_voice_with_a_local_model_it_runs_on_its_own(script, screen, monkeypatch):
    monkeypatch.setattr(permissions, "_local_model", lambda: True)
    permissions.set_mode(CID, permissions.SAFE_AUTO)
    ask_model(script)

    reply = say("mirá mi pantalla")

    assert reply.text == "Veo un editor de código" and screen == [1]


def test_by_voice_a_capture_you_allowed_yourself_runs_even_with_a_cloud_model(script, screen, monkeypatch):
    monkeypatch.setattr(permissions, "_local_model", lambda: False)
    allow("captura")
    permissions.set_mode(CID, permissions.SAFE_AUTO)
    ask_model(script)

    say("mirá mi pantalla")

    assert screen == [1]


def test_only_the_capture_is_marked_as_seeing_the_screen():
    assert {tools.ai_id(t) for t in tools.ai_tools() if t.sees_screen} == {"captura"}
    assert next(t for t in tools.ai_tools() if t.sees_screen).ai == "read"


def test_a_routine_can_never_take_a_capture(script):
    assert "captura" not in core._catalog_ids()


def test_the_web_never_serializes_a_picture():
    from epsilon.channels import web

    reply = core.Reply("hola")
    reply.images = [File(JPEG, "image/jpeg")]

    assert "images" not in web.to_json(reply)


def test_the_local_model_check_follows_the_provider_of_the_chat_model(monkeypatch):
    monkeypatch.setenv("AI_MODEL", "ollama:qwen3.5:4b")
    assert permissions._local_model() is True

    monkeypatch.setenv("AI_MODEL", "gemini:gemini-3.1-flash-lite")
    assert permissions._local_model() is False
