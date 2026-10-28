from collections.abc import Callable
import json
from dataclasses import dataclass
from datetime import datetime
import threading
import time

from epsilon.data import activity, notes
from epsilon.routines import notify, steps, store
from epsilon.settings import config

# Ejecuta la cadena de pasos de UNA rutina, sin modelo. Corre en su propio hilo (nunca en el del reloj), una a la vez por
# rutina (si una sigue en curso, el segundo disparo se omite y se avisa), con un tiempo máximo por ejecución. Se corta en
# el primer paso que falla y avisa cuál. Las herramientas las ejecuta un «ejecutor» que registra el núcleo
# (`set_executor`), así este paquete no importa el núcleo; `avisar` y `esperar` son propios de las rutinas.
# Cada paso queda en DATA_DIR/routines.log. Lo que un paso devuelve es dato: redactado y recortado antes de pasar al siguiente.

LOG_FILE = "routines.log"
LOG_CHARS = 200

_stop = threading.Event()
_guard = threading.Lock()
_running: set[object] = set()
_executor: Callable[[str, str], "StepResult"] | None = None
_ai_executor: Callable[[dict, dict, dict[int, str]], "StepResult"] | None = None
NOTICE_CHARS = 200
_clock = time.monotonic  # aparte para que los tests lo reemplacen


@dataclass
class StepResult:
    ok: bool
    text: str
    fatal: bool = False  # falló por algo que repetir con la IA no arregla (sin precio del modelo, tope diario): no hay respaldo


def set_executor(fn: Callable[[str, str], StepResult] | None) -> None:
    """Quien sabe correr herramientas (el núcleo) se registra acá."""
    global _executor
    _executor = fn


def set_ai_executor(fn: Callable[[dict, dict, dict[int, str]], StepResult] | None) -> None:
    """Quien sabe correr un paso con IA (el núcleo) se registra acá: `fn(rule, step, outputs)`."""
    global _ai_executor
    _ai_executor = fn


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


def _not_green(rule: dict, tool: str) -> str | None:
    """En una rutina con IA solo corre lo que la persona dejó en verde, aunque la receta (que alguien pudo editar a mano en
    routines.json) diga otra cosa. None si puede correr."""
    if "ai" not in rule or tool in steps.LOCAL:
        return None
    states = rule["ai"].get("tools") if isinstance(rule["ai"], dict) else None
    if isinstance(states, dict) and states.get(tool) == "green":
        return None
    return f"«{tool}» no está en verde en esta rutina: no la corro."


def _step(rule: dict, step: dict, outputs: dict[int, str], trigger: str, cap: int | None = None) -> StepResult:
    rule_id = rule.get("id", "prueba")
    tool = step["tool"]
    if tool in steps.AI_STEPS:  # un paso con IA: lo corre el núcleo, que sabe de modelos, permisos y gasto
        if _ai_executor is None:
            return StepResult(False, "No hay un ejecutor de IA registrado.")
        try:
            return _ai_executor(rule, step, outputs)
        except Exception as e:
            log(rule_id, f"error en {tool}", repr(e))
            return StepResult(False, "No pude ejecutar el paso con IA.")
    args = steps.render_args(step["args"], outputs, trigger)
    if tool == "avisar":
        message = _notice(step["args"], args, trigger)
        _tell(message, rule_id)
        return StepResult(True, message)
    if tool == "esperar":
        if not args.strip().isdigit() or not 1 <= int(args) <= steps.max_wait():
            return StepResult(False, f"«esperar» lleva segundos, de 1 a {steps.max_wait()}.")
        if _stop.wait(int(args)):
            return StepResult(False, "Epsilon se está apagando.")
        return StepResult(True, f"Esperé {int(args)} s.")
    if (blocked := _not_green(rule, tool)) is not None:
        return StepResult(False, blocked)
    if _executor is None:
        return StepResult(False, "No hay un ejecutor de herramientas registrado.")
    try:
        extra = {} if step.get("version") is None else {"version": step["version"]}
        if cap:  # lo que devuelve es la lista de la que un paso `elegir` va a decidir: tiene que verse entera
            extra["cap"] = cap
        return _executor(tool, args, **extra)
    except Exception as e:  # un paso con problemas corta la cadena, no al programa
        log(rule_id, f"error en {tool}", repr(e))
        return StepResult(False, f"No pude ejecutar {tool}.")


def run_chain(rule: dict, trigger: str = "", on_step: Callable[[int, StepResult], None] | None = None) -> list[StepResult]:
    """Ejecuta los pasos en orden, en el hilo que llama. Corta en el primero que falla. Devuelve un resultado por paso hecho."""
    rule_id = rule.get("id", "prueba")
    started = _clock()
    outputs: dict[int, str] = {}
    results: list[StepResult] = []
    sources = {s.get("source") for s in rule["steps"] if s["tool"] == "elegir"}  # pasos de los que otro elige
    for number, step in enumerate(rule["steps"], 1):
        if _clock() - started > config.number("ROUTINES_MAX_RUN_SECONDS"):
            result = StepResult(False, "Se pasó del tiempo máximo de la rutina.")
        else:
            activity.begin()
            result = _step(rule, step, outputs, trigger, steps.MAX_SOURCE_OUTPUT if number in sources else None)
        result = StepResult(result.ok, config.redact(result.text)[:steps.MAX_SOURCE_OUTPUT if number in sources else steps.MAX_OUTPUT], result.fatal)
        outputs[number] = result.text
        results.append(result)
        log(rule_id, f"paso {number} {step['tool']} {'ok' if result.ok else 'falló'}", result.text)
        raw = step["args"] if isinstance(step["args"], str) else json.dumps(step["args"], ensure_ascii=False)
        activity.record("rutina", step["tool"], raw, result.text, result.ok, routine=_routine_label(rule))
        if on_step:
            on_step(number, result)
        if not result.ok:
            break
    return results


def _routine_label(rule: dict) -> str:
    """Cómo se nombra la rutina en la actividad: su nombre (o su número), y si fue una prueba desde el editor."""
    name = rule.get("name") or f"#{rule.get('id', '')}"
    return f"{name} (prueba)" if "id" not in rule or rule.get("id") == "prueba" else name


def describe_failure(rule: dict, number: int, result: StepResult) -> str:
    step = rule["steps"][number - 1]
    return f"No pude cumplir la rutina {rule.get('id', '')} «{rule.get('name', '')}» (paso {number}, {step['tool']}): {result.text}"


def _live_recipe(rule: dict) -> bool:
    """¿Esta rutina corre una receta (confirmada y no vieja)? Una vieja o sin receta corre la IA completa."""
    ai = rule.get("ai")
    return isinstance(ai, dict) and bool(ai.get("recipe")) and not ai.get("stale")


def _full_ai(rule: dict) -> dict:
    """La misma rutina corriendo la IA completa (un solo paso `ia` con la instrucción), sin su receta."""
    return {**rule, "steps": [{"tool": "ia", "args": rule["ai"]["instruction"]}]}


def _summary(rule: dict, result: StepResult) -> str:
    return " ".join(result.text.split())[:NOTICE_CHARS]


def run_job(rule: dict, trigger: str = "") -> None:
    """Una ejecución completa: la cadena y, si falló, el aviso. Libera la marca de «en curso» al terminar.

    Una rutina con IA: si su receta falla en un paso, esa vez se cae UNA sola vez a la IA completa, la receta queda marcada
    como vieja (hasta que la persona confirme una nueva corre la IA completa) y se avisa. Si la IA completa también falla,
    se avisa el error y no se reintenta."""
    try:
        if isinstance(rule.get("ai"), dict) and rule["ai"].get("recipe") and rule["ai"].get("stale"):
            rule = _full_ai(rule)
        results = run_chain(rule, trigger)
        if results and not results[-1].ok:
            if _live_recipe(rule) and not results[-1].fatal:
                _fall_back(rule, trigger, len(results), results[-1])
            else:
                _tell(describe_failure(rule, len(results), results[-1]), rule.get("id"))
        elif results and rule.get("ai"):  # una rutina con IA cuenta en una línea qué hizo (nadie la está mirando)
            _tell(f"🤖 {rule.get('name') or rule.get('id')}: {_summary(rule, results[-1])}", rule.get("id"))
    finally:
        with _guard:
            _running.discard(rule.get("id"))


def _fall_back(rule: dict, trigger: str, number: int, failed: StepResult) -> None:
    """Falló un paso de la receta: marca la receta como vieja y esta vez lo hace la IA completa (una sola vez)."""
    name, rule_id = rule.get("name") or rule.get("id"), rule.get("id")
    if isinstance(rule_id, int):
        store.mark_stale(rule_id, True)
    log(rule_id, "receta vieja", f"paso {number} falló: {failed.text}")
    full = _full_ai(rule)
    results = run_chain(full, trigger)
    if not results or not results[-1].ok:
        _tell(describe_failure(full, len(results) or 1, results[-1] if results else failed), rule_id)
        return
    reason = " ".join(failed.text.split())[:NOTICE_CHARS]
    _tell(
        f"La receta de «{name}» quedó vieja (paso {number} falló: {reason}); esta vez lo hizo la IA: {_summary(rule, results[-1])}. "
        "Volvé a aprenderla desde Rutinas.",
        rule_id,
    )


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
