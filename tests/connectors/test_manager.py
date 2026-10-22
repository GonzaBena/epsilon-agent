import threading
import time

import mcp.types as mcp_types
import pytest

from epsilon import core
from epsilon.connectors import manager, store
from tests.connectors.fakes import FakeLink, install, tool


def add(name: str, env: dict | None = None) -> None:
    store.add(name, [name, "--opcion"], env or {})


def wait_for(condition, seconds: float = 2.0) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, "no pasó a tiempo"
        time.sleep(0.005)


def in_thread(fn, *args):
    """Corre fn en otro hilo y devuelve (hilo, dict con 'result' o 'error')."""
    out: dict = {}

    def run():
        try:
            out["result"] = fn(*args)
        except Exception as error:  # noqa: BLE001 - el test lo revisa
            out["error"] = error

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def test_minimal_env_has_only_path_home_lang_and_extra(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-epsilon")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/x")
    monkeypatch.setenv("LANG", "es_AR.UTF-8")

    env = manager.minimal_env({"X": "1"})

    assert env == {"PATH": "/usr/bin", "HOME": "/home/x", "LANG": "es_AR.UTF-8", "X": "1"}


def test_open_gets_the_argv_and_the_minimal_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-epsilon")
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add("luces", {"TOKEN": "abc"})

    manager.call("luces", "saludar", {})

    argv, env = link.opens[0]
    assert argv == ["luces", "--opcion"]
    assert env["TOKEN"] == "abc" and "GEMINI_API_KEY" not in env


def test_open_is_lazy_start_happens_on_first_call(monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add("luces")

    assert manager.status("luces") == "dormido"
    assert link.opens == []
    assert manager.call("luces", "saludar", {"a": 1}) == "ok"
    assert manager.status("luces") == "activo"
    assert manager.call("luces", "saludar", {}) == "ok"
    assert len(link.opens) == 1
    assert link.calls == [("saludar", {"a": 1}), ("saludar", {})]


def test_unknown_connector_is_a_spanish_error():
    with pytest.raises(manager.ConnectorError, match="No hay un conector"):
        manager.call("nada", "x", {})
    with pytest.raises(manager.ConnectorError, match="No hay un conector"):
        manager.status("nada")


def test_disabled_connector_never_opens(monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add("luces")
    store.update("luces", enabled=False)

    with pytest.raises(manager.ConnectorError, match="apagado"):
        manager.call("luces", "saludar", {})
    with pytest.raises(manager.ConnectorError, match="apagado"):
        manager.start("luces")
    assert link.opens == []
    assert manager.status("luces") == "apagado"


def test_start_stores_tools_and_reports_new_then_changed(monkeypatch):
    link = FakeLink(tools=[tool("prender", "Prende"), tool("apagar", "Apaga")])
    install(monkeypatch, {"luces": link})
    add("luces")
    seen = []
    manager.set_changed_hook(lambda name, changed: seen.append((name, changed)))

    assert sorted(manager.start("luces")) == ["apagar", "prender"]
    saved = store.get("luces")["tools"]
    assert [t["name"] for t in saved] == ["prender", "apagar"]
    assert all(len(t["hash"]) == 64 for t in saved)

    assert manager.start("luces") == []

    link.tools[1] = tool("apagar", "Apaga", {"type": "object", "properties": {"pieza": {"type": "string"}}})
    assert manager.start("luces") == ["apagar"]
    assert seen == [("luces", ["prender", "apagar"]), ("luces", []), ("luces", ["apagar"])]
    assert len(link.opens) == 1  # ya estaba prendido: solo vuelve a mirar las herramientas


def test_lazy_open_also_discovers_tools(monkeypatch):
    install(monkeypatch, {"luces": FakeLink(tools=[tool("prender")])})
    add("luces")

    manager.call("luces", "prender", {})

    assert [t["name"] for t in store.get("luces")["tools"]] == ["prender"]


def test_tool_descriptions_are_trimmed_and_bad_tools_skipped(monkeypatch):
    bad = [{"name": 5}, {"description": "sin nombre"}, "nada", {"name": "x", "schema": "no-objeto"}]
    install(monkeypatch, {"luces": FakeLink(tools=[tool("larga", "a" * 1000), *bad])})
    add("luces")

    manager.start("luces")

    saved = store.get("luces")["tools"]
    assert [t["name"] for t in saved] == ["larga", "x"]
    assert len(saved[0]["description"]) <= manager.MAX_DESCRIPTION
    assert saved[1]["schema"] == {}


def test_a_failing_hook_does_not_break_start(monkeypatch):
    install(monkeypatch, {"luces": FakeLink()})
    add("luces")

    def broken(name, changed):
        raise RuntimeError("roto")

    manager.set_changed_hook(broken)

    assert manager.start("luces") == ["saludar"]


def test_idle_connector_is_closed_by_tick(monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add("luces")
    manager.call("luces", "saludar", {})

    manager.tick(time.monotonic() + manager.IDLE_CLOSE + 1)

    assert link.closed
    assert manager.status("luces") == "dormido"
    assert manager.call("luces", "saludar", {}) == "ok"
    assert len(link.opens) == 2
    assert manager.status("luces") == "activo"


def test_tick_does_not_close_a_busy_or_recent_connector(monkeypatch):
    link = FakeLink(delay=0.2)
    install(monkeypatch, {"luces": link})
    add("luces")
    manager.call("luces", "saludar", {})

    manager.tick(time.monotonic())  # recién usado
    assert not link.closed

    thread, out = in_thread(manager.call, "luces", "saludar", {})
    wait_for(lambda: len(link.calls) == 2)
    manager.tick(time.monotonic() + manager.IDLE_CLOSE + 1)  # ocupado: no se cierra
    thread.join()

    assert out == {"result": "ok"}
    assert not link.closed
    assert link.close_count == 0


def test_max_running_closes_the_least_recently_used(monkeypatch):
    links = {name: FakeLink() for name in ("aa", "bb", "cc", "dd")}
    install(monkeypatch, links)
    for name in links:
        add(name)

    for name in ("aa", "bb", "cc", "aa"):  # el menos usado hace rato es bb
        manager.call(name, "saludar", {})
    manager.call("dd", "saludar", {})

    assert links["bb"].closed
    assert not links["aa"].closed and not links["cc"].closed and not links["dd"].closed
    assert manager.status("bb") == "dormido"
    assert [manager.status(n) for n in ("aa", "cc", "dd")] == ["activo"] * 3


def test_max_running_with_all_busy_refuses(monkeypatch):
    monkeypatch.setattr(manager, "MAX_RUNNING", 1)
    busy = FakeLink(delay=0.3)
    other = FakeLink()
    install(monkeypatch, {"aa": busy, "bb": other})
    add("aa")
    add("bb")

    thread, out = in_thread(manager.call, "aa", "saludar", {})
    wait_for(lambda: busy.calls)
    with pytest.raises(manager.ConnectorError, match="trabajando"):
        manager.call("bb", "saludar", {})
    thread.join()

    assert out == {"result": "ok"}
    assert other.opens == []


def test_call_timeout_returns_spanish_error_and_stops_connector(monkeypatch):
    monkeypatch.setattr(manager, "CALL_TIMEOUT", 0.05)
    link = FakeLink(delay=1.0)
    install(monkeypatch, {"luces": link})
    add("luces")

    with pytest.raises(manager.ConnectorError, match="tardó"):
        manager.call("luces", "saludar", {})

    assert manager.status("luces") == "detenido"
    assert store.get("luces")["error"]
    assert link.closed


def test_crash_mid_call_marks_stopped_and_next_call_restarts(monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add("luces")
    manager.call("luces", "saludar", {})
    link.die_next = True

    with pytest.raises(manager.ConnectorError, match="dejó de responder"):
        manager.call("luces", "saludar", {})

    assert manager.status("luces") == "detenido"
    assert store.get("luces")["error"]
    assert manager.call("luces", "saludar", {}) == "ok"
    assert manager.status("luces") == "activo"
    assert store.get("luces")["error"] == ""
    assert len(link.opens) == 2


def test_process_that_died_while_idle_is_reopened(monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add("luces")
    manager.call("luces", "saludar", {})
    link.closed = True  # el proceso se cayó solo, sin que nadie lo cerrara

    assert manager.status("luces") == "dormido"
    assert manager.call("luces", "saludar", {}) == "ok"
    assert len(link.opens) == 2


def test_tool_error_keeps_the_connector_running(monkeypatch):
    link = FakeLink()
    install(monkeypatch, {"luces": link})
    add("luces")
    link.fail_next = "no existe la pieza"

    with pytest.raises(manager.ConnectorError, match="no existe la pieza"):
        manager.call("luces", "saludar", {})

    assert manager.status("luces") == "activo"
    assert not link.closed
    assert store.get("luces")["error"] == ""


def test_open_failure_marks_stopped_with_redacted_reason(monkeypatch):
    link = FakeLink(open_error=OSError("no pude arrancar con supersecreto123"))
    install(monkeypatch, {"luces": link})
    add("luces", {"TOKEN": "supersecreto123"})

    with pytest.raises(manager.ConnectorError) as caught:
        manager.call("luces", "saludar", {})

    assert "supersecreto123" not in str(caught.value)
    assert "no pude arrancar" in str(caught.value)
    assert manager.status("luces") == "detenido"
    error = store.get("luces")["error"]
    assert error and "supersecreto123" not in error


def test_results_never_show_env_values_and_are_trimmed(monkeypatch):
    link = FakeLink(result=lambda name, args: "token=supersecreto123 " + "x" * 10_000)
    install(monkeypatch, {"luces": link})
    add("luces", {"TOKEN": "supersecreto123"})

    text = manager.call("luces", "saludar", {})

    assert "supersecreto123" not in text
    assert len(text) <= manager.MAX_RESULT_CHARS


def test_result_limit_matches_core():
    assert manager.MAX_RESULT_CHARS == core.MAX_RESULT_CHARS


def test_concurrent_calls_both_complete(monkeypatch):
    link = FakeLink(delay=0.1, result=lambda name, args: f"hola {args['n']}")
    install(monkeypatch, {"luces": link})
    add("luces")

    first, out1 = in_thread(manager.call, "luces", "saludar", {"n": 1})
    second, out2 = in_thread(manager.call, "luces", "saludar", {"n": 2})
    first.join()
    second.join()

    assert out1 == {"result": "hola 1"} and out2 == {"result": "hola 2"}
    assert len(link.opens) == 1
    assert len(link.calls) == 2


def test_call_racing_with_idle_close_reopens(monkeypatch):
    link = FakeLink(close_delay=0.2)
    install(monkeypatch, {"luces": link})
    add("luces")
    manager.call("luces", "saludar", {})

    closer, _ = in_thread(manager.tick, time.monotonic() + manager.IDLE_CLOSE + 1)
    link.close_started.wait(2)
    caller, out = in_thread(manager.call, "luces", "saludar", {"otra": True})  # entra mientras se cierra
    closer.join()
    caller.join()

    assert out == {"result": "ok"}
    assert link.close_count == 1
    assert len(link.opens) == 2
    assert link.calls[-1] == ("saludar", {"otra": True})
    assert manager.status("luces") == "activo"


def test_stop_and_stop_all(monkeypatch):
    links = {"aa": FakeLink(), "bb": FakeLink()}
    install(monkeypatch, links)
    add("aa")
    add("bb")
    manager.call("aa", "saludar", {})
    manager.call("bb", "saludar", {})

    manager.stop("aa")
    assert links["aa"].closed and manager.status("aa") == "dormido"
    assert manager.status("bb") == "activo"

    manager.stop_all()
    assert links["bb"].closed and manager.status("bb") == "dormido"
    manager.stop("aa")  # ya cerrado: no pasa nada


def test_read_resource(monkeypatch):
    install(monkeypatch, {"luces": FakeLink(resources={"epsilon://buttons": "[]"})})
    add("luces")

    assert manager.read_resource("luces", "epsilon://buttons") == "[]"
    assert manager.read_resource("luces", "epsilon://otro") is None


def test_real_open_is_blocked_in_tests():
    add("luces")

    with pytest.raises(manager.ConnectorError):
        manager.call("luces", "saludar", {})

    assert manager.status("luces") == "detenido"


def test_text_of_joins_text_blocks_and_warns_about_the_rest():
    blocks = [mcp_types.TextContent(type="text", text="uno"),
              mcp_types.ImageContent(type="image", data="AAAA", mime_type="image/png"),
              mcp_types.TextContent(type="text", text="dos")]

    text = manager._text_of(blocks)

    assert text.startswith("uno\ndos")
    assert "no es texto" in text
    assert manager._text_of([mcp_types.TextContent(type="text", text="solo")]) == "solo"
