import pytest

from epsilon import core
from epsilon.ai import permissions
from epsilon.data import notes
from epsilon.routines import runner

# El catálogo de pasos de una rutina y el ejecutor que el núcleo le da al runner.


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    monkeypatch.setattr(permissions, "_granted", {})


def ids():
    return {item["id"] for item in core.routine_catalog()}


def set_permission(tool_id, state):
    states = permissions.load()
    states[tool_id] = state
    permissions.save(states)


def test_the_catalog_has_the_usual_tools_and_the_routine_only_steps():
    assert {"volumen", "musica", "nav_abrir", "avisar", "esperar"} <= ids()


def test_the_catalog_leaves_out_ai_spending_conversation_and_routine_tools():
    assert not ids() & {"resumir", "enviar", "rutina_crear", "rutina_ver", "rutina_borrar"}


def test_a_tool_blocked_in_permissions_is_not_offered_and_cannot_run(monkeypatch):
    set_permission("volumen", "deny")
    monkeypatch.setitem(core.AI_RUNNERS, "volumen", lambda args: pytest.fail("no debería correr"))

    assert "volumen" not in ids()
    result = core.run_step("volumen", "20")
    assert result.ok is False and "disponible" in result.text


def test_a_tool_that_asks_the_ai_runs_in_a_routine_without_asking(monkeypatch):
    set_permission("ventanas", "ask")
    monkeypatch.setitem(core.AI_RUNNERS, "ventanas", lambda args: core.Reply("firefox"))

    result = core.run_step("ventanas", "activa")

    assert result == runner.StepResult(True, "firefox")


def test_a_failed_reply_is_a_failed_step(monkeypatch):
    monkeypatch.setitem(core.AI_RUNNERS, "nav_abrir", lambda args: core.Reply("Chromium no abre", failed=True))

    assert core.run_step("nav_abrir", "https://x.com") == runner.StepResult(False, "Chromium no abre")


def test_a_raising_tool_is_a_failed_step_without_leaking_the_error(monkeypatch):
    def broken(args):
        raise RuntimeError("secreto interno")

    monkeypatch.setitem(core.AI_RUNNERS, "volumen", broken)

    result = core.run_step("volumen", "20")

    assert result.ok is False and "secreto" not in result.text


def test_the_pc_tools_flag_their_errors():
    assert core._volume_cmd("mucho").failed is True
    assert core._music_cmd("bailar").failed is True


def test_the_browser_tools_flag_their_errors(monkeypatch):
    def broken(url):
        raise core.navegador.NavegadorError("no responde")

    monkeypatch.setattr(core.navegador, "open_url", broken)

    reply = core.AI_RUNNERS["nav_abrir"]("https://x.com")

    assert reply.failed is True and reply.text == "no responde"


def test_a_hand_edited_argument_is_validated_again_by_the_real_function(monkeypatch):
    result = core.run_step("volumen", "40; reboot")

    assert result.ok is False


def test_a_tool_that_is_not_in_the_catalog_does_not_run():
    result = core.run_step("rutina_crear", "23:00 | volumen 20")

    assert result.ok is False and "disponible" in result.text


def test_the_connector_version_must_match_the_one_the_routine_was_saved_with(monkeypatch):
    monkeypatch.setattr(core, "_connector_hash", lambda tool_id: "nuevo")
    monkeypatch.setattr(core, "_catalog_ids", lambda: {"mcp_luces__prender"})

    result = core.run_step("mcp_luces__prender", "{}", version="viejo")

    assert result.ok is False and "cambió" in result.text


def test_stamp_steps_records_the_connector_version(monkeypatch):
    monkeypatch.setattr(core, "_connector_hash", lambda tool_id: "abc" if tool_id.startswith("mcp_") else None)

    stamped = core.stamp_steps([{"tool": "volumen", "args": "1"}, {"tool": "mcp_luces__prender", "args": "{}"}])

    assert stamped == [{"tool": "volumen", "args": "1"}, {"tool": "mcp_luces__prender", "args": "{}", "version": "abc"}]


def test_the_executor_is_registered_for_the_runner():
    core.register_routine_executor()

    assert runner._executor is core.run_step
    runner.set_executor(None)


# --- arreglos de la revisión final ---------------------------------------------------------------------------------


def test_a_loose_site_name_is_resolved_when_saving(monkeypatch):
    monkeypatch.setattr(core.navegador, "resolve_target", lambda text: "https://music.youtube.com/")

    assert core.stamp_steps([{"tool": "nav_abrir", "args": "yt"}]) == [{"tool": "nav_abrir", "args": "https://music.youtube.com/"}]


def test_an_unknown_site_name_is_refused_when_saving(monkeypatch):
    def unknown(text):
        raise core.navegador.NavegadorError("No encontré «xyz» en el historial de Chromium")

    monkeypatch.setattr(core.navegador, "resolve_target", unknown)

    with pytest.raises(ValueError, match="No encontré"):
        core.stamp_steps([{"tool": "nav_abrir", "args": "xyz"}])


def test_a_site_name_is_also_resolved_when_the_step_runs(monkeypatch):
    seen = []
    monkeypatch.setattr(core.navegador, "resolve_target", lambda text: "https://music.youtube.com/")
    monkeypatch.setitem(core.AI_RUNNERS, "nav_abrir", lambda args: seen.append(args) or core.Reply("abierta"))

    result = core.run_step("nav_abrir", "yt")

    assert result.ok is True and seen == ["https://music.youtube.com/"]


@pytest.mark.parametrize("tool", ["nav_abrir", "nav_clic", "nav_escribir", "nav_buscar", "nota_editar", "olvidar"])
def test_steps_that_act_on_the_world_do_not_take_the_output_of_other_steps(tool):
    with pytest.raises(ValueError, match="salida"):
        core.stamp_steps([{"tool": "nav_leer", "args": ""}, {"tool": tool, "args": "{{paso 1}}"}])


def test_a_notice_may_use_the_output_of_a_previous_step():
    assert core.stamp_steps([{"tool": "sonando", "args": ""}, {"tool": "avisar", "args": "Suena {{paso 1}}"}])[1]["args"] == "Suena {{paso 1}}"


def test_a_note_edit_that_changes_nothing_is_a_failed_step():
    assert core.run_step("nota_editar", "sin formato").ok is False


def test_forgetting_a_line_that_does_not_exist_is_a_failed_step():
    set_permission("olvidar", "allow")
    result = core.run_step("olvidar", "99")

    assert result.ok is False and "disponible" not in result.text


@pytest.fixture
def connector(monkeypatch):
    monkeypatch.setattr(core, "_catalog_ids", lambda: {"mcp_luces__prender"})
    monkeypatch.setattr(core, "_connector_hash", lambda tool_id: "h")
    monkeypatch.setattr(core.connector_registry, "resolve", lambda tool_id: ("luces", "prender", {"type": "object"}, "h"))


def test_a_connector_result_with_an_error_line_inside_is_still_a_success(connector, monkeypatch):
    monkeypatch.setattr(core.connectors, "call", lambda name, real, parsed, version: "Listo\nError: solo un texto adentro")

    assert core.run_step("mcp_luces__prender", "{}").ok is True


def test_a_connector_that_raises_its_error_is_a_failed_step(connector, monkeypatch):
    def broken(name, real, parsed, version):
        raise core.connectors.ConnectorError("se cayó")

    monkeypatch.setattr(core.connectors, "call", broken)

    result = core.run_step("mcp_luces__prender", "{}")

    assert result.ok is False and "se cayó" in result.text


def test_a_connector_that_is_gone_or_has_bad_arguments_is_a_failed_step(connector, monkeypatch):
    assert core.run_step("mcp_luces__prender", "no es json").ok is False
    monkeypatch.setattr(core.connector_registry, "resolve", lambda tool_id: None)
    assert core.run_step("mcp_luces__prender", "{}").ok is False
