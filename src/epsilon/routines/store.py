from datetime import datetime
import json
import os
import threading

from epsilon.data import notes
from epsilon.routines import rules as rules_mod
from epsilon.settings import config

# Las rutinas guardadas: DATA_DIR/routines.json (nombre fijo, siempre por safe_path). Cada cambio reescribe el archivo
# por uno temporal (excepción a «solo agregar», como la memoria: una regla se pausa, se borra y avanza). El motor y la
# web usan hilos distintos, por eso todo cambio pasa por `update`, que toma el candado, lee, cambia y guarda.

FILE = "routines.json"
MAX_RULES = 50

_lock = threading.Lock()


class StoreError(Exception):
    """No se pudo guardar la regla; el texto va para la persona."""


def _read() -> dict:
    try:
        data = json.loads(notes.safe_path(FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"rules": []}
    found = data.get("rules", []) if isinstance(data, dict) else []
    return {"rules": [r for r in found if isinstance(r, dict) and isinstance(r.get("id"), int)]}


def _write(data: dict) -> None:
    path = notes.safe_path(FILE)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def rules() -> list[dict]:
    with _lock:
        return _read()["rules"]


def update(change) -> object:
    """Lee las reglas, llama a `change(rules)` (que las modifica en el lugar y puede devolver algo) y guarda."""
    with _lock:
        data = _read()
        result = change(data["rules"])
        try:
            _write(data)
        except OSError:
            raise StoreError("No pude guardar las rutinas.") from None
        return result


def add(rule: dict) -> dict:
    """Guarda una regla nueva (de `rules.build`) y le pone el id. StoreError si ya hay demasiadas."""
    rule = {**rule, "do": {**rule["do"], "arg": config.redact(rule["do"]["arg"])}}

    def change(all_rules: list[dict]) -> dict:
        if len(all_rules) >= MAX_RULES:
            raise StoreError(f"Ya hay {MAX_RULES} rutinas: borrá alguna antes de crear otra.")
        rule["id"] = max([r["id"] for r in all_rules] + [0]) + 1
        all_rules.append(rule)
        return rule

    return update(change)


def remove(rule_id: int) -> bool:
    def change(all_rules: list[dict]) -> bool:
        keep = [r for r in all_rules if r["id"] != rule_id]
        found = len(keep) != len(all_rules)
        all_rules[:] = keep
        return found

    return update(change)


def set_enabled(rule_id: int, enabled: bool, now: datetime) -> dict | None:
    """Pausa o reanuda una regla; None si no existe. Al reanudar se recalcula su próxima vez desde `now` (si no, quedaría
    vencida y se contaría como perdida). Una «sola vez» que ya pasó se borra y se avisa."""
    def change(all_rules: list[dict]) -> tuple[dict | None, bool]:
        rule = next((r for r in all_rules if r["id"] == rule_id), None)
        if rule is None:
            return None, False
        rule["enabled"] = enabled
        if enabled:
            following = rules_mod.next_after(rule["when"], now)
            if following is None:
                all_rules.remove(rule)  # se guarda el borrado: por eso el error se lanza DESPUÉS de `update`
                return rule, True
            rule["next_run"] = following.isoformat()
        return rule, False

    rule, expired = update(change)
    if expired:
        raise StoreError("Esa rutina era de una sola vez y ya pasó; la borré.")
    return rule
