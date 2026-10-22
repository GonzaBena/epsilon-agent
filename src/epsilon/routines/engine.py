from datetime import datetime
import threading

from epsilon.data import notes
from epsilon.routines import notify, rules, store
from epsilon.settings import config
from epsilon.utils import clock, multimedia

# El motor de las rutinas: un hilo que cada TICK segundos mira el reloj y cumple lo que toca. Sin modelo: costo cero.
# `tick(now)` es una pasada completa y no depende del hilo (los tests la llaman con la hora que quieran). Primero
# adelanta la próxima vez de cada regla y recién después ejecuta, así que una falla nunca repite una rutina (a lo sumo
# se pierde una). Lo que toca pero ya pasó más de GRACE se considera perdido (la PC estaba apagada): NO se ejecuta, se
# avisa de una vez. Cada ejecución queda en DATA_DIR/routines.log. Un fallo nunca tumba la web ni al resto de rutinas.

TICK = 30  # segundos entre pasadas
LOG_FILE = "routines.log"
LOG_CHARS = 200

_stop = threading.Event()
_thread: threading.Thread | None = None


def _log(rule_id: object, what: str, result: str) -> None:
    """Agrega una línea al registro: hora, rutina, qué pasó, resultado (una línea, redactada y corta). No rompe nada."""
    short = " ".join(config.redact(result).split())[:LOG_CHARS]
    fields = [datetime.now().isoformat(timespec="seconds"), str(rule_id), what, short]
    try:
        with notes.safe_path(LOG_FILE).open("a", encoding="utf-8") as log:
            log.write("\t".join(fields) + "\n")
    except OSError:
        pass


def _tell(text: str, rule_id: object) -> None:
    failed = notify.send(text)
    if failed:
        _log(rule_id, "aviso falló", "; ".join(failed))


def _run(rule: dict) -> None:
    """Cumple UNA rutina con las funciones de siempre (heredan sus topes) y avisa lo que hizo."""
    do = rule["do"]
    try:
        if do["kind"] == "avisar":
            result = do["arg"]
            _tell(f"⏰ {result}", rule["id"])
        else:
            result = multimedia.volume(do["arg"]) if do["kind"] == "volumen" else multimedia.music(do["arg"])
            _tell(f"Rutina {rule['id']}: {result}", rule["id"])
        _log(rule["id"], "ejecutada", result)
    except (ValueError, multimedia.MultimediaError) as e:
        _log(rule["id"], "falló", str(e))
        _tell(f"No pude cumplir la rutina {rule['id']} ({rules.describe(rule)}): {e}", rule["id"])


def tick(now: datetime) -> None:
    """Una pasada: ¿qué rutinas tocan a esta hora (local, sin zona)?"""
    if not config.enabled("ROUTINES_ENABLED"):
        return
    fired: list[dict] = []
    missed: list[tuple[dict, datetime]] = []

    def advance(all_rules: list[dict]) -> None:
        for rule in list(all_rules):
            if not rule.get("enabled", True):
                continue
            try:
                due = datetime.fromisoformat(rule["next_run"])
                if due > now:
                    continue
                if now - due <= rules.GRACE:
                    fired.append(dict(rule))
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
    for rule in fired:
        try:
            _run(rule)
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
    _thread = threading.Thread(target=_loop, name="rutinas", daemon=True)
    _thread.start()


def stop(timeout: float = 5) -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout)
