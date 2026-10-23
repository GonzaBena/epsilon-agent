import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from epsilon.connectors import buttons, manager, store
from epsilon.data import notes

EXAMPLE = Path(__file__).resolve().parents[2] / "docs" / "ejemplos" / "luces_mcp.py"


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    manager._reset()
    yield
    manager._reset()


def load():
    spec = importlib.util.spec_from_file_location("luces_mcp_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # el servidor no arranca: eso es solo con __main__
    return module


def test_example_server_defines_the_tool_and_the_buttons_resource(monkeypatch):
    module = load()
    found = asyncio.run(module.server.list_tools())
    assert [t.name for t in found] == ["luces"]
    schema = found[0].input_schema
    raw = json.loads(module.buttons())
    assert isinstance(raw, list) and len(raw) == 2

    store.add("luces", ["luces"], {})
    store.update("luces", enabled=True, tools=[{"name": "luces", "description": "", "schema": schema, "hash": "h"}])
    monkeypatch.setattr(manager, "read_resource", lambda name, uri: module.buttons())
    shown = buttons.suggestions("luces")
    assert [s["label"] for s in shown] == [item["label"] for item in raw]  # ninguna se descartó
    assert shown[0]["args"] == {"habitacion": "living", "estado": "off"}
