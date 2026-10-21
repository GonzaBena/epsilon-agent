from datetime import datetime

# Nombres en español a mano: `locale` depende de cómo esté configurada la máquina.
DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def now() -> datetime:
    """La hora actual en la zona del sistema. Aparte para que los tests puedan reemplazarla."""
    return datetime.now().astimezone()


def today_text(moment: datetime | None = None) -> str:
    """«miércoles 21 de octubre de 2026»: solo el día, así el prompt cambia una vez al día."""
    moment = moment or now()
    return f"{DAYS[moment.weekday()]} {moment.day} de {MONTHS[moment.month - 1]} de {moment.year}"


def now_text(moment: datetime | None = None) -> str:
    """«miércoles 21 de octubre de 2026, 14:32 (UTC-03:00)»: fecha y hora exactas."""
    moment = moment or now()
    offset = moment.strftime("%z")
    zone = f"UTC{offset[:3]}:{offset[3:]}" if offset else "hora local"
    return f"{today_text(moment)}, {moment:%H:%M} ({zone})"
