"""Conector de ejemplo para Epsilon: un servidor MCP mínimo (SDK `mcp`, por stdio) que «maneja luces».

NO toca ningún aparato: solo anota en stderr lo que haría. Sirve para probar la página Conectores.
Se agrega con el comando:  uv run python docs/ejemplos/luces_mcp.py
"""

import json
import sys
from typing import Literal

from mcp.server.mcpserver import MCPServer

server = MCPServer("luces")


@server.tool()
def luces(habitacion: str, estado: Literal["on", "off"]) -> str:
    """Prende o apaga las luces de una habitación."""
    print(
        f"[luces] {habitacion}: {estado}", file=sys.stderr, flush=True
    )  # stderr: stdout es del protocolo MCP
    return f"Luces de {habitacion}: {estado} (simulado)"


@server.resource("epsilon://buttons")
def buttons() -> str:
    """Botones que Epsilon puede ofrecer a la persona (nunca los crea solo). Cada `args` cuadra con el esquema de `luces`."""
    return json.dumps(
        [
            {
                "label": "Apagar el living",
                "tool": "luces",
                "args": {"habitacion": "living", "estado": "off"},
            },
            {
                "label": "Prender la cocina",
                "tool": "luces",
                "args": {"habitacion": "cocina", "estado": "on"},
            },
        ],
        ensure_ascii=False,
    )


if __name__ == "__main__":
    server.run()  # transporte stdio por defecto
