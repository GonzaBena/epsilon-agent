import contextvars
import json
from datetime import datetime

from epsilon.data import notes
from epsilon.settings import config

# Actividad: lo que Epsilon HIZO por su cuenta, una línea JSON por acción, dentro de DATA_DIR.
# La escriben dos lugares: core._run_calls (cada herramienta que ejecuta la IA) y core.run_step (cada
# paso de una rutina). Lo que la persona hace con comandos no va acá. Solo se agrega, nunca se pisa;
# todo pasa por config.redact y va recortado. Sin modelo: cuesta cero.
ACTIVITY_FILE = "activity.jsonl"
MAX_TEXT = 160  # caracteres del argumento y del resultado
WHO = ("ia", "rutina")


# Lo que la herramienta en curso quiere sumar a su línea (hoy: «change», el id del cambio de una nota, para poder
# deshacerlo). Variable de contexto: vale para la tarea en curso; begin() la limpia antes de cada herramienta y
# record() la usa y la limpia.
_extra: contextvars.ContextVar[dict | None] = contextvars.ContextVar("activity_extra", default=None)
EXTRA_KEYS = ("change",)


def begin() -> None:
    """Antes de ejecutar una herramienta: nada de una acción anterior se pega a esta."""
    _extra.set(None)


def attach(**fields: str) -> None:
    """Desde una herramienta: datos para la línea de esta acción (solo las claves de EXTRA_KEYS)."""
    _extra.set({**(_extra.get() or {}), **{k: v for k, v in fields.items() if k in EXTRA_KEYS}})


def _path():
    return notes.safe_path(ACTIVITY_FILE)  # toda ruta pasa por safe_path (los tests lo reemplazan)


def _short(text: str) -> str:
    """Una línea, redactada y corta: lo que se guarda es para leer de un vistazo, no el resultado entero."""
    return " ".join(config.redact(str(text)).split())[:MAX_TEXT]


def record(
    who: str,
    tool: str,
    arg: str,
    result: str,
    ok: bool,
    *,
    asked: bool | None = None,
    conversation: str | None = None,
    routine: str | None = None,
) -> None:
    """Anota una acción. `who` es «ia» o «rutina»; `asked`, si la persona la aprobó en ese momento (IA);
    `conversation` o `routine`, de dónde vino. Un fallo al escribir nunca rompe la acción que se anota."""
    if who not in WHO:
        raise ValueError(f"who: {who!r}")
    line = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "who": who,
        "tool": tool,
        "arg": _short(arg),
        "result": _short(result),
        "ok": bool(ok),
    }
    if asked is not None:
        line["asked"] = asked
    if conversation:
        line["conversation"] = conversation
    if routine:
        line["routine"] = _short(routine)
    line.update(_extra.get() or {})
    _extra.set(None)
    try:
        with _path().open("a", encoding="utf-8") as out:
            out.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read(
    offset: int = 0, limit: int = 50, who: str | None = None, failed: bool = False, skip: frozenset[str] = frozenset()
) -> dict:
    """Lo más nuevo primero, de a páginas: {"items": [...], "total": n} (total = las que pasan el filtro).
    `who` filtra por «ia» o «rutina»; `failed`, solo lo que salió mal; `skip`, herramientas que no se muestran
    (la web pasa las de solo lectura). Una línea rota se saltea."""
    path = _path()
    if not path.exists():
        return {"items": [], "total": 0}
    entries = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("who") not in WHO:
            continue
        if who and entry["who"] != who:
            continue
        if failed and entry.get("ok", True):
            continue
        if entry.get("tool") in skip:
            continue
        entries.append(entry)
    entries.reverse()
    return {"items": entries[offset : offset + limit], "total": len(entries)}
