from datetime import datetime, timedelta
import re
import unicodedata

from epsilon.routines import steps as steps_mod
from epsilon.utils import clock, multimedia

# Las rutinas: reglas «cuándo | qué» que Epsilon cumple solo. Este módulo es PURO: entiende el texto, lo valida y calcula
# la próxima ejecución; no tiene hilos, reloj propio, archivos ni procesos. Todo lo que ejecuta una rutina sale de una
# lista fija (`ACTIONS`) y sus argumentos se validan acá, al crearla, con las mismas reglas que los comandos. Las
# horas son locales y sin zona («naive»): una rutina de las 23:00 es a las 23:00 del reloj de la pared.

ACTIONS = ("avisar", "volumen", "musica")  # las acciones de las rutinas de antes (hoy son los primeros pasos posibles)
DEFAULT_KNOWN = frozenset({"volumen", "musica"})  # herramientas que `build` acepta si quien llama no dice cuáles hay
MAX_NAME = 60
MAX_COORD = 5000
MAX_TEXT = 200  # largo de un aviso
MAX_IN_MINUTES = 7 * 24 * 60  # «en 30m» como mucho una semana adelante
COOLDOWN = timedelta(hours=1)  # una condición no se cumple dos veces dentro de esta hora, aunque el valor suba y baje
METRICS = {"disco": "el disco", "ram": "la memoria"}  # lo que se puede vigilar -> cómo se nombra en un aviso
GRACE = timedelta(minutes=2)  # pasado esto de la hora, la rutina se considera perdida (la PC estaba apagada)
USAGE = (
    "Se escribe «cuándo | qué». Cuándo: `23:00` (todos los días), `lun-vie 09:00`, `sab,dom 10:30`, `en 30m` / `en 2h` (una sola vez) "
    "o `disco>90` / `ram>85` (cuando el uso pasa de ese %). "
    "Qué: `avisar texto`, `volumen 20`, `musica pausa`, `abrir <sitio o link>`, `esperar 5` y las demás herramientas; se pueden encadenar con « > » "
    "y un paso usa lo que devolvió uno anterior con `{{paso 1}}`. Ejemplos: /rutina 23:00 | volumen 20   ·   /rutina 08:00 | volumen 15 > abrir <link> > esperar 5 > musica play"
)

DAY_SHORT = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
DAY_LONG = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _plain(text: str) -> str:
    """Minúsculas y sin acentos: «Mié» -> «mie»."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


DAY_INDEX = {_plain(name): i for names in (DAY_SHORT, DAY_LONG) for i, name in enumerate(names)}
CONDITION = re.compile(r"(disco|ram)\s*>\s*(\d{1,3})\s*%?")
TIME = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
IN = re.compile(r"en\s+(?:(\d{1,3})\s*h)?\s*(?:(\d{1,4})\s*m)?")


def _days(spec: str) -> list[int]:
    """«lun-vie», «lun,mie,vie» o «finde» -> días de la semana (0 = lunes), sin repetir y en orden."""
    spec = _plain(spec)
    if spec == "finde":
        return [5, 6]
    found: set[int] = set()
    for part in spec.split(","):
        first, dash, last = part.partition("-")
        if first not in DAY_INDEX or (dash and last not in DAY_INDEX):
            raise ValueError(f"No entiendo los días «{spec[:30]}». {USAGE}")
        start = DAY_INDEX[first]
        end = DAY_INDEX[last] if dash else start
        found.update(range(start, end + 1) if start <= end else [*range(start, 7), *range(0, end + 1)])  # «vie-lun» da la vuelta
    return sorted(found)


def parse_when(text: str, now: datetime) -> dict:
    """«23:00», «lun-vie 09:00», «en 30m» o «disco>90» -> {"kind": "daily", "time": "23:00", "days": [...]},
    {"kind": "once", "at": iso} o {"kind": "cond", "metric": "disco", "value": 90}."""
    text = " ".join(text.split())
    condition = CONDITION.fullmatch(text.lower())
    if condition:
        value = int(condition.group(2))
        if not 1 <= value <= 99:
            raise ValueError("El umbral va de 1 a 99 %.")
        return {"kind": "cond", "metric": condition.group(1), "value": value}
    inside = IN.fullmatch(text.lower())
    if inside and (inside.group(1) or inside.group(2)):
        minutes = int(inside.group(1) or 0) * 60 + int(inside.group(2) or 0)
        if not 1 <= minutes <= MAX_IN_MINUTES:
            raise ValueError("«en» admite entre 1 minuto y 7 días.")
        return {"kind": "once", "at": (now + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()}
    *days, clock = text.split(" ")
    match = TIME.fullmatch(clock)
    if not match or len(days) > 1:
        raise ValueError(f"No entiendo cuándo («{text[:40]}»). {USAGE}")
    return {"kind": "daily", "time": f"{int(match.group(1)):02d}:{match.group(2)}", "days": _days(days[0]) if days else list(range(7))}


def parse_do(text: str) -> dict:
    """«volumen 20», «musica pausa» o «avisar sacar la pizza» -> {"kind": ..., "arg": ...}. ValueError si no vale."""
    kind, _, arg = " ".join(text.split()).partition(" ")
    kind = kind.lower()
    if kind not in ACTIONS:
        raise ValueError(f"Una rutina puede hacer: {', '.join(ACTIONS)}. {USAGE}")
    if kind == "avisar":
        if not arg:
            raise ValueError("¿Qué querés que te avise? Ejemplo: /rutina en 30m | avisar sacar la pizza")
        if len(arg) > MAX_TEXT:
            raise ValueError(f"El aviso es largo: como mucho {MAX_TEXT} caracteres.")
    elif kind == "volumen":
        multimedia.parse_volume(arg)  # ValueError con su propio mensaje
    elif not multimedia.is_music_action(arg):
        raise ValueError("Opciones de música: pausa, play, siguiente, anterior, " + multimedia.MODES_USAGE + " (sin opción, alterna).")
    return {"kind": kind, "arg": arg if kind == "avisar" else " ".join(arg.lower().split())}


def next_after(when: dict, after: datetime) -> datetime | None:
    """La primera vez que le toca estrictamente después de `after`; None si ya no le toca (una sola vez, vencida) o si no
    es de horario (una condición se mira en cada pasada: no tiene «próxima vez»)."""
    if when["kind"] == "cond":
        return None
    if when["kind"] == "once":
        at = datetime.fromisoformat(when["at"])
        return at if at > after else None
    hour, minute = map(int, when["time"].split(":"))
    for offset in range(8):
        candidate = (after + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > after and candidate.weekday() in when["days"]:
            return candidate
    return None  # inalcanzable: `days` nunca está vacío


def _check_step(step: dict) -> dict:
    """Valida los argumentos de los pasos que Epsilon conoce de antemano (con las mismas reglas de siempre); el resto
    se valida al ejecutarse. Devuelve el paso normalizado."""
    tool, args = step["tool"], str(step.get("args", "")).strip()
    if tool in ACTIONS:
        args = parse_do(f"{tool} {args}")["arg"]
    elif tool == "esperar":
        if not args.isdigit() or not 1 <= int(args) <= steps_mod.max_wait():
            raise ValueError(f"«esperar» lleva los segundos a esperar, de 1 a {steps_mod.max_wait()}.")
    kept = {"tool": tool, "args": args}
    if isinstance(step.get("version"), str) and len(step["version"]) <= 80:
        kept["version"] = step["version"]  # la versión del conector que la persona vio al confirmar (la anota el núcleo)
    return kept


def _layout(layout: dict) -> dict:
    """{"1": {"x": 10, "y": 20}} -> lo mismo, o ValueError si no vale (solo posiciones de la pizarra)."""
    if not isinstance(layout, dict) or len(layout) > steps_mod.MAX_STEPS + 1:
        raise ValueError("La posición de los nodos no es válida.")
    clean = {}
    for key, pos in layout.items():
        if (not isinstance(key, str) or not key.isdigit() or not isinstance(pos, dict) or set(pos) != {"x", "y"}
                or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_COORD for v in pos.values())):
            raise ValueError("La posición de los nodos no es válida.")
        clean[key] = {"x": pos["x"], "y": pos["y"]}
    return clean


def _assemble(name: str, when: dict, chain: list[dict], now: datetime, known: set[str] | frozenset[str],
              layout: dict | None) -> dict:
    steps_mod.validate_chain(chain, set(known))
    chain = [_check_step(step) for step in chain]
    name = " ".join(name.split()) or describe_chain(chain)[:MAX_NAME]
    if len(name) > MAX_NAME:
        raise ValueError(f"El nombre es largo: como mucho {MAX_NAME} caracteres.")
    rule = {"when": when, "name": name, "steps": chain, "enabled": True}
    if when["kind"] == "cond":
        rule.update(tripped=False, last_run=None)
    else:
        rule["next_run"] = next_after(when, now).isoformat()
    if layout:
        rule["layout"] = _layout(layout)
    return rule


def build(text: str, now: datetime, known: set[str] | frozenset[str] = DEFAULT_KNOWN) -> dict:
    """El texto «cuándo | paso > paso» -> una regla lista para guardar (sin id). ValueError con el motivo si no se entiende."""
    when_text, bar, do_text = text.partition("|")
    if not bar or not when_text.strip() or not do_text.strip():
        raise ValueError(USAGE)
    return _assemble("", parse_when(when_text.strip(), now), steps_mod.parse_chain(do_text.strip(), set(known)), now, known, None)


def build_from(name: str, when_text: str, chain: list[dict], now: datetime, known: set[str] | frozenset[str] = DEFAULT_KNOWN,
               layout: dict | None = None) -> dict:
    """La forma estructurada (la que manda la web): nombre, «cuándo» en texto y lista de pasos."""
    if not isinstance(chain, list) or not all(isinstance(s, dict) and isinstance(s.get("tool"), str) for s in chain):
        raise ValueError("Los pasos no son válidos.")
    if not name.strip():
        raise ValueError("Ponele un nombre a la rutina.")
    return _assemble(name, parse_when(when_text.strip(), now), [{**s, "tool": s["tool"], "args": str(s.get("args", ""))} for s in chain],
                     now, known, layout)


def migrate(rule: dict) -> dict:
    """Una regla de antes (`do`) -> la de ahora (`steps`). Idempotente; una regla rota se devuelve tal cual."""
    if "steps" in rule or "do" not in rule:
        return rule
    try:
        step = {"tool": rule["do"]["kind"], "args": rule["do"].get("arg", "")}
        return {**{k: v for k, v in rule.items() if k != "do"}, "name": describe_step(step), "steps": [step]}
    except (KeyError, TypeError, AttributeError):
        return rule


def check_condition(rule: dict, reading: float | None, now: datetime) -> bool:
    """¿Hay que cumplir esta rutina de condición ahora? Se cumple al CRUZAR el umbral (no en cada pasada): `tripped`
    queda en la regla hasta que el valor vuelve a estar por debajo, y entre dos cumplimientos pasa al menos COOLDOWN.
    Cambia la regla en el lugar (`tripped`, `last_run`); sin lectura no hace nada."""
    if reading is None:
        return False
    if reading <= rule["when"]["value"]:
        rule["tripped"] = False  # volvió a la normalidad: se rearma
        return False
    if rule.get("tripped"):
        return False
    last = rule.get("last_run")
    if last and now - datetime.fromisoformat(last) < COOLDOWN:
        return False  # sigue alto, pero se avisó hace poco: se mira de nuevo en la pasada siguiente
    rule["tripped"], rule["last_run"] = True, now.isoformat()
    return True


def describe_when(when: dict) -> str:
    if when["kind"] == "cond":
        return f"cuando {METRICS[when['metric']]} pase del {when['value']} %"
    if when["kind"] == "once":
        return f"una vez, {datetime.fromisoformat(when['at']):%d/%m a las %H:%M}"
    days = when["days"]
    if days == list(range(7)):
        label = "todos los días"
    elif days == [5, 6]:
        label = "sábado y domingo"
    elif days == list(range(days[0], days[-1] + 1)) and len(days) > 2:
        label = f"de {DAY_LONG[days[0]]} a {DAY_LONG[days[-1]]}"
    else:
        label = ", ".join(DAY_LONG[d] for d in days)
    return f"{label} a las {when['time']}"


describe_step = steps_mod.describe_step


def describe_chain(chain: list[dict]) -> str:
    """«volumen 15 > nav_abrir https://… > avisar: hola»."""
    return " > ".join(describe_step(step) for step in chain)


def describe(rule: dict) -> str:
    """«todos los días a las 23:00 → volumen 20»."""
    return f"{describe_when(rule['when'])} → {describe_chain(rule['steps'])}"


def next_text(rule: dict) -> str:
    """Cuándo le toca a una rutina: «jue 22/10 23:00», o «vigilando» si es de condición (no tiene hora)."""
    if "next_run" not in rule:
        return "vigilando"
    when = datetime.fromisoformat(rule["next_run"])
    return f"{clock.DAYS[when.weekday()][:3]} {when:%d/%m %H:%M}"


def view(rule: dict) -> dict:
    """Una regla como la dibuja la página: frase de cuándo, qué hace, si está activa y su próxima vez. KeyError, ValueError
    o TypeError si la regla está rota (alguien editó routines.json a mano)."""
    when = describe_when(rule["when"])
    return {
        "id": rule["id"],
        "kind": rule["when"]["kind"],
        "when": when[:1].upper() + when[1:],
        "name": rule["name"],
        "steps": [{"tool": s["tool"], "args": s["args"], "text": describe_step(s)} for s in rule["steps"]],
        "do": {"kind": rule["steps"][0]["tool"], "arg": rule["steps"][0]["args"]},  # compatibilidad: el primer paso
        "layout": rule.get("layout", {}),
        "trigger": rule["when"],  # el activador tal cual, para que el editor lo vuelva a armar
        "active": bool(rule.get("enabled", True)),
        "next": rule.get("next_run"),  # ISO sin zona (hora de la pared), para que la página cuente cuánto falta
        "next_text": next_text(rule),
    }
