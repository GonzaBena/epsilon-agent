from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import threading
import time

from epsilon.data import notes
from epsilon.routines import notify, steps
from epsilon.settings import config

# Ejecuta la cadena de pasos de UNA rutina, sin modelo. Corre en su propio hilo (nunca en el del reloj), una a la vez por
# rutina (si una sigue en curso, el segundo disparo se omite y se avisa), con un tiempo máximo por ejecución. Se corta en
# el primer paso que falla y avisa cuál. Las herramientas las ejecuta un «ejecutor» que registra el núcleo
# (`set_executor`), así este paquete no importa el núcleo; `avisar` y `esperar` son propios de las rutinas.
# Cada paso queda en DATA_DIR/routines.log. Lo que un paso devuelve es dato: redactado y recortado antes de pasar al siguiente.

LOG_FILE = "routines.log"
LOG_CHARS = 200
RUN_BUDGET = 300  # segundos máximos por ejecución

_stop = threading.Event()
_guard = threading.Lock()
_running: set[object] = set()
_executor: Callable[[str, str], "StepResult"] | None = None
_clock = time.monotonic  # aparte para que los tests lo reemplacen


@dataclass
class StepResult:
    ok: bool
    text: str


def set_executor(fn: Callable[[str, str], StepResult] | None) -> None:
    """Quien sabe correr herramientas (el núcleo) se registra acá."""
    global _executor
    _executor = fn


def stop() -> None:
    """Corta cualquier `esperar` en curso (Epsilon se está apagando)."""
    _stop.set()


def log(rule_id: object, what: str, result: str) -> None:
    """Agrega una línea al registro: hora, rutina, qué pasó, resultado (una línea, redactada y corta). No rompe nada."""
    short = " ".join(config.redact(result).split())[:LOG_CHARS]
    fields = [datetime.now().isoformat(timespec="seconds"), str(rule_id), what, short]
    try:
        with notes.safe_path(LOG_FILE).open("a", encoding="utf-8") as out:
            out.write("\t".join(fields) + "\n")
    except OSError:
        pass


def _tell(text: str, rule_id: object) -> None:
    failed = notify.send(text)
    if failed:
        log(rule_id, "aviso falló", "; ".join(failed))


def _notice(raw: str, text: str, trigger: str) -> str:
    """El texto de un `avisar`: una condición dice qué la disparó; una hora, solo el reloj."""
    if not trigger:
        return text if text.startswith("⚠") else f"⏰ {text}"
    return f"⚠ {text}" if "{{disparador}}" in raw else f"⚠ {text} ({trigger})"


def _step(rule_id: object, step: dict, outputs: dict[int, str], trigger: str) -> StepResult:
    tool = step["tool"]
    args = steps.render_args(step["args"], outputs, trigger)
    if tool == "avisar":
        message = _notice(step["args"], args, trigger)
        _tell(message, rule_id)
        return StepResult(True, message)
    if tool == "esperar":
        if not args.strip().isdigit() or not 1 <= int(args) <= steps.MAX_WAIT:
            return StepResult(False, f"«esperar» lleva segundos, de 1 a {steps.MAX_WAIT}.")
        if _stop.wait(int(args)):
            return StepResult(False, "Epsilon se está apagando.")
        return StepResult(True, f"Esperé {int(args)} s.")
    if _executor is None:
        return StepResult(False, "No hay un ejecutor de herramientas registrado.")
    try:
        version = step.get("version")
        return _executor(tool, args) if version is None else _executor(tool, args, version=version)
    except Exception as e:  # un paso con problemas corta la cadena, no al programa
        log(rule_id, f"error en {tool}", repr(e))
        return StepResult(False, f"No pude ejecutar {tool}.")


def run_chain(rule: dict, trigger: str = "", on_step: Callable[[int, StepResult], None] | None = None) -> list[StepResult]:
    """Ejecuta los pasos en orden, en el hilo que llama. Corta en el primero que falla. Devuelve un resultado por paso hecho."""
    rule_id = rule.get("id", "prueba")
    started = _clock()
    outputs: dict[int, str] = {}
    results: list[StepResult] = []
    for number, step in enumerate(rule["steps"], 1):
        if _clock() - started > RUN_BUDGET:
            result = StepResult(False, "Se pasó del tiempo máximo de la rutina.")
        else:
            result = _step(rule_id, step, outputs, trigger)
        result = StepResult(result.ok, config.redact(result.text)[:steps.MAX_OUTPUT])
        outputs[number] = result.text
        results.append(result)
        log(rule_id, f"paso {number} {step['tool']} {'ok' if result.ok else 'falló'}", result.text)
        if on_step:
            on_step(number, result)
        if not result.ok:
            break
    return results


def describe_failure(rule: dict, number: int, result: StepResult) -> str:
    step = rule["steps"][number - 1]
    return f"No pude cumplir la rutina {rule.get('id', '')} «{rule.get('name', '')}» (paso {number}, {step['tool']}): {result.text}"


def run_job(rule: dict, trigger: str = "") -> None:
    """Una ejecución completa: la cadena y, si falló, el aviso. Libera la marca de «en curso» al terminar."""
    try:
        results = run_chain(rule, trigger)
        if results and not results[-1].ok:
            _tell(describe_failure(rule, len(results), results[-1]), rule.get("id"))
    finally:
        with _guard:
            _running.discard(rule.get("id"))


def _launch(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="rutina", daemon=True).start()


def start(rule: dict, trigger: str = "") -> bool:
    """Lanza la rutina en su propio hilo. False (y un aviso) si esa misma rutina todavía está corriendo."""
    rule_id = rule.get("id")
    with _guard:
        busy = rule_id in _running
        if not busy:
            _running.add(rule_id)
    if busy:
        log(rule_id, "omitida", "sigue en curso")
        _tell(f"La rutina {rule_id} «{rule.get('name', '')}» sigue en curso: omití esta vez.", rule_id)
        return False
    _launch(lambda: run_job(rule, trigger))
    return True
