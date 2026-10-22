import asyncio
import threading

from epsilon.connectors import manager


def tool(name: str, description: str = "", schema: dict | None = None) -> dict:
    return {"name": name, "description": description, "schema": schema or {"type": "object", "properties": {}}}


class FakeLink:
    """Un conector falso: sin procesos. Se puede configurar para fallar, tardar o morir en medio de una llamada."""

    def __init__(self, tools=None, result="ok", delay=0.0, close_delay=0.0, open_error=None, resources=None,
                 open_delay=0.0):
        self.tools = list(tools or [tool("saludar")])
        self.result = result            # texto, o una función (nombre, args) -> texto
        self.delay = delay              # segundos que tarda cada llamada
        self.close_delay = close_delay  # segundos que tarda en cerrarse
        self.open_delay = open_delay    # segundos que tarda en abrirse (sin esto, abrir nunca cede el ciclo)
        self.open_error = open_error    # excepción que tira al abrirse
        self.resources = dict(resources or {})
        self.die_next = False           # la próxima llamada «muere» (como un proceso que se cae)
        self.fail_next = None           # la próxima llamada contesta este error de la herramienta
        self.calls: list[tuple[str, dict]] = []
        self.opens: list[tuple[list[str], dict]] = []
        self.closed = False
        self.close_count = 0
        self.close_started = threading.Event()  # para los tests de carreras: el cierre ya empezó

    @property
    def alive(self) -> bool:
        return not self.closed

    async def list_tools(self) -> list[dict]:
        return [dict(t) if isinstance(t, dict) else t for t in self.tools]

    async def call(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.die_next:
            self.die_next = False
            self.closed = True
            raise ConnectionError("Connection closed")
        if self.fail_next:
            message, self.fail_next = self.fail_next, None
            raise manager.ConnectorError(message)
        return self.result(name, args) if callable(self.result) else self.result

    async def read_resource(self, uri: str) -> str | None:
        return self.resources.get(uri)

    async def close(self) -> None:
        self.close_started.set()
        if self.close_delay:
            await asyncio.sleep(self.close_delay)
        self.closed = True
        self.close_count += 1


def install(monkeypatch, links: dict[str, FakeLink]) -> dict:
    """Reemplaza `manager._open`: la clave es el programa (argv[0]); en los tests es el nombre del conector.
    Devuelve `{"peak": n}`: cuántos llegaron a estar abiertos (o abriéndose) a la vez."""
    stats = {"peak": 0}
    opening: set[int] = set()  # ids de los que se están abriendo

    def busy() -> int:
        """Abiertos (y no cerrados) más los que se están abriendo."""
        return sum(1 for link in links.values() if (link.opens and not link.closed) or id(link) in opening)

    async def fake_open(argv: list[str], env: dict[str, str]):
        link = links[argv[0]]
        link.opens.append((list(argv), dict(env)))
        opening.add(id(link))
        link.closed = True  # mientras se abre no está listo
        stats["peak"] = max(stats["peak"], busy())
        try:
            if link.open_delay:
                await asyncio.sleep(link.open_delay)
            if link.open_error is not None:
                raise link.open_error
            link.closed = False
            return link
        finally:
            opening.discard(id(link))

    monkeypatch.setattr(manager, "_open", fake_open)
    return stats
