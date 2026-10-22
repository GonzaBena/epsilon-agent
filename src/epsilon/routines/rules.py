from datetime import datetime, timedelta
import re
import unicodedata

from epsilon.utils import multimedia

# Las rutinas: reglas «cuándo | qué» que Epsilon cumple solo. Este módulo es PURO: entiende el texto, lo valida y calcula
# la próxima ejecución; no tiene hilos, reloj propio, archivos ni procesos. Todo lo que ejecuta una rutina sale de una
# lista fija (`ACTIONS`) y sus argumentos se validan acá, al crearla, con las mismas reglas que los comandos. Las
# horas son locales y sin zona («naive»): una rutina de las 23:00 es a las 23:00 del reloj de la pared.

ACTIONS = ("avisar", "volumen", "musica")
MAX_TEXT = 200  # largo de un aviso
MAX_IN_MINUTES = 7 * 24 * 60  # «en 30m» como mucho una semana adelante
COOLDOWN = timedelta(hours=1)  # una condición no se cumple dos veces dentro de esta hora, aunque el valor suba y baje
METRICS = {"disco": "el disco", "ram": "la memoria"}  # lo que se puede vigilar -> cómo se nombra en un aviso
GRACE = timedelta(minutes=2)  # pasado esto de la hora, la rutina se considera perdida (la PC estaba apagada)
USAGE = (
    "Se escribe «cuándo | qué». Cuándo: `23:00` (todos los días), `lun-vie 09:00`, `sab,dom 10:30`, `en 30m` / `en 2h` (una sola vez) "
    "o `disco>90` / `ram>85` (cuando el uso pasa de ese %). "
    "Qué: `avisar texto`, `volumen 20` o `musica pausa`. Ejemplo: /rutina 23:00 | volumen 20"
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
        return {"kind": "once", "at": (now + timedelta(minutes=minutes)).replace(second=0, microsecond=0).isoformat()}
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
    elif arg.lower() not in multimedia.MUSIC_ACTIONS:
        raise ValueError("Opciones de música: pausa, play, siguiente, anterior (sin opción, alterna).")
    return {"kind": kind, "arg": arg if kind == "avisar" else arg.lower()}


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


def build(text: str, now: datetime) -> dict:
    """El texto «cuándo | qué» -> una regla lista para guardar (sin id). ValueError con el motivo si no se entiende."""
    when_text, bar, do_text = text.partition("|")
    if not bar or not when_text.strip() or not do_text.strip():
        raise ValueError(USAGE)
    when, do = parse_when(when_text.strip(), now), parse_do(do_text.strip())
    if when["kind"] == "cond":
        return {"when": when, "do": do, "enabled": True, "tripped": False, "last_run": None}
    return {"when": when, "do": do, "enabled": True, "next_run": next_after(when, now).isoformat()}


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


def describe_do(do: dict) -> str:
    return f"avisar: {do['arg']}" if do["kind"] == "avisar" else f"{do['kind']} {do['arg']}".strip()


def describe(rule: dict) -> str:
    """«todos los días a las 23:00 → volumen 20»."""
    return f"{describe_when(rule['when'])} → {describe_do(rule['do'])}"
