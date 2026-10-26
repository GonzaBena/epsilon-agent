import re

from epsilon import tools
from epsilon.data import activity
from epsilon.routines import steps


def test_every_ai_tool_and_routine_step_says_what_it_did_in_plain_words():
    for tool in tools.ai_tools():
        assert tool.done and not tool.done.startswith(("/", "+")), tools.ai_id(tool)
    for step in steps.LOCAL.values():
        assert step["done"]


def test_activity_api_describes_each_action_newest_first(client):
    activity.record("rutina", "volumen", "20", "Volumen: 20 %", True, routine="Mañana")
    activity.record("ia", "nota_editar", "todo 3", "Antes… ahora…", True, asked=True, conversation="c1")
    activity.record("ia", "mcp_luces__apagar", "{}", "Error: no hay luz", False)  # un conector que ya no existe

    data = client.get("/api/activity").json()

    assert data["total"] == 3
    assert [(i["what"], i["kind"], i["ok"]) for i in data["items"]] == [
        ("Usó luces: apagar", "write", False),
        ("Editó una línea de una nota", "write", True),
        ("Cambió el volumen", "write", True),
    ]
    assert data["items"][1]["conversation"] == "c1" and data["items"][2]["routine"] == "Mañana"


def test_activity_api_filters_before_paging(client):
    activity.record("ia", "clima", "", "Soleado", True)  # solo lectura
    activity.record("ia", "nota", "pan", "Guardado", True)
    activity.record("rutina", "avisar", "hola", "hola", True)
    activity.record("rutina", "volumen", "x", "No entiendo", False)

    tools_of = lambda **q: [i["tool"] for i in client.get("/api/activity", params=q).json()["items"]]

    assert tools_of(who="ia") == ["nota", "clima"]
    assert tools_of(who="rutina", failed="true") == ["volumen"]
    assert tools_of(changes="true") == ["volumen", "avisar", "nota"]  # sin «Consultó el clima»
    assert tools_of(changes="true", limit=1, offset=1) == ["avisar"]


def test_activity_api_validates_its_parameters(client):
    assert client.get("/api/activity", params={"who": "persona"}).status_code == 422
    assert client.get("/api/activity", params={"limit": 1000}).status_code == 422


def test_the_activity_page_is_served_and_draws_only_text(client):
    from tests.channels.web.helpers import js

    reply = client.get("/actividad")
    assert reply.status_code == 200 and "actividad.js" in reply.text and "Actividad" in reply.text

    script = js("actividad.js")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("//"))
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in code
    assert "encodeURIComponent(item.conversation)" in script  # el enlace a la conversación no arma HTML ni rutas a mano


def test_the_page_hides_reads_by_default():
    from tests.channels.web.helpers import js, page

    assert re.search(r'id="hide-reads" aria-pressed="true"', page("actividad.html"))
    assert "changes: true" in js("actividad.js")
