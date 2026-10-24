from datetime import datetime
import threading

from epsilon.data import notes
from epsilon.routines import notify, rules, runner, store
from epsilon.settings import config
from epsilon.utils import clock, sistema

# El motor de las rutinas: un hilo que cada TICK segundos mira el reloj y cumple lo que toca. Sin modelo: costo cero.
# `tick(now)` es una pasada completa y no depende del hilo (los tests la llaman con la hora que quieran). Primero
# adelanta la próxima vez de cada regla y recién después las lanza (`runner.start`: cada cadena en su hilo), así que una falla nunca repite una rutina (a lo sumo
# se pierde una). Lo que toca pero ya pasó más de GRACE se considera perdido (la PC estaba apagada): NO se ejecuta, se
# avisa de una vez. Cada ejecución queda en DATA_DIR/routines.log. Un fallo nunca tumba la web ni al resto de rutinas.

TICK = 30  # segundos entre pasadas
LOG_FILE = runner.LOG_FILE

_stop = threading.Event()
_thread: threading.Thread | None = None


_log = runner.log  # el registro es el mismo que el de las cadenas


def _read(metric: str) -> float | None:
    """El valor actual (0-100) de lo que vigila una condición. Aparte para que los tests lo reemplacen."""
    return sistema.disk_percent(notes.DATA_DIR) if metric == "disco" else sistema.ram_percent()


def _readings() -> dict[str, float | None]:
    """Lo que hay que medir esta pasada: solo las cosas que alguna rutina de condición activa vigila (sin condiciones, nada)."""
    wanted = {r["when"]["metric"] for r in store.rules() if r.get("enabled", True) and isinstance(r.get("when"), dict) and r["when"].get("kind") == "cond"
              and r["when"].get("metric") in rules.METRICS}
    return {metric: _read(metric) for metric in wanted}


def _tell(text: str, rule_id: object) -> None:
    failed = notify.send(text)
    if failed:
        _log(rule_id, "aviso falló", "; ".join(failed))


def tick(now: datetime) -> None:
    """Una pasada: ¿qué rutinas tocan a esta hora (local, sin zona)?"""
    if not config.enabled("ROUTINES_ENABLED"):
        return
    readings = _readings()
    fired: list[tuple[dict, float | None]] = []
    missed: list[tuple[dict, datetime]] = []

    def advance(all_rules: list[dict]) -> None:
        for rule in list(all_rules):
            if not rule.get("enabled", True):
                continue
            try:
                if rule["when"]["kind"] == "cond":  # se mira en cada pasada, no tiene hora
                    reading = readings.get(rule["when"]["metric"])
                    if rules.check_condition(rule, reading, now):
                        fired.append((dict(rule), reading))
                    continue
                due = datetime.fromisoformat(rule["next_run"])
                if due > now:
                    continue
                if now - due <= rules.GRACE:
                    fired.append((dict(rule), None))
                else:
                    missed.append((dict(rule), due))
                following = rules.next_after(rule["when"], now)
            except (KeyError, ValueError, TypeError):
                rule["enabled"] = False  # una regla rota se pausa: no puede repetir el error en cada pasada
                _log(rule.get("id", "?"), "regla inválida", "pausada")
                continue
            if following is None:
                all_rules.remove(rule)
            else:
                rule["next_run"] = following.isoformat()

    try:
        store.update(advance)
    except store.StoreError as e:
        _log("-", "no se pudo guardar", str(e))
        return  # sin guardar el avance repetiría todo en la pasada siguiente
    for rule, reading in fired:
        try:
            runner.start(rule, f"{rules.METRICS[rule['when']['metric']]} al {reading:.0f} %" if reading is not None else "")
        except Exception as e:  # una rutina con problemas no frena a las otras
            _log(rule.get("id", "?"), "error", repr(e))
    if missed:
        lines = [f"{rules.describe(rule)} (era {due:%d/%m %H:%M})" for rule, due in missed]
        _log("-", "perdidas", "; ".join(lines))
        _tell(f"Me perdí {len(missed)} rutina{'s' if len(missed) > 1 else ''} mientras Epsilon estaba apagado: " + "; ".join(lines), "-")


def _loop() -> None:
    while True:
        try:
            tick(clock.now().replace(tzinfo=None))
        except Exception as e:  # el motor sigue aunque una pasada falle
            _log("-", "error", repr(e))
        if _stop.wait(TICK):
            return


def start() -> None:
    """Prende el motor (una sola vez). Si ya estaba corriendo no hace nada."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    runner._stop.clear()
    _thread = threading.Thread(target=_loop, name="rutinas", daemon=True)
    _thread.start()


def stop(timeout: float = 5) -> None:
    _stop.set()
    runner.stop()
    if _thread is not None:
        _thread.join(timeout)
