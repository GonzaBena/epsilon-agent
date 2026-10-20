import importlib
import logging
import os
import threading

from epsilon import state
from epsilon.settings import config

# Canales disponibles: nombre -> módulo. Para sumar uno (Slack...) se agrega una
# línea acá; el módulo tiene que exponer is_configured(env) y run(), y puede
# exponer explain(error) para traducir sus errores a un mensaje claro.
# El ORDEN importa: el primero es el canal principal (la web) y corre en el
# hilo principal; los demás son opcionales y corren cada uno en su hilo.
CHANNELS = {
    "web": "epsilon.channels.web",
    "telegram": "epsilon.channels.telegram",
}


def load(name: str):
    """Importa el módulo de un canal."""
    return importlib.import_module(CHANNELS[name])


def switched_off(name: str) -> bool:
    """¿Lo apagaste desde la web? Un canal tiene interruptor si existe el ajuste <NOMBRE>_ENABLED."""
    key = f"{name.upper()}_ENABLED"
    return key in config.BY_KEY and not config.enabled(key)


def enabled_channels(env) -> list[str]:
    """Nombres de los canales que tienen su configuración completa y no están apagados."""
    return [name for name in CHANNELS if load(name).is_configured(env) and not switched_off(name)]


class ChannelError(Exception):
    """No se pudo prender o apagar un canal. El mensaje está listo para mostrar."""


_threads: dict[str, threading.Thread] = {}  # los canales con hilo propio que prendimos
_stopping: set[str] = set()  # los que estamos apagando a propósito (no es un error)
_control = threading.Lock()  # prender y apagar de a uno


def switchable(name: str) -> bool:
    """¿Se puede apagar y prender desde la web? Solo los que tienen interruptor y saben detenerse."""
    return f"{name.upper()}_ENABLED" in config.BY_KEY and hasattr(load(name), "stop")


def start_channel(name: str) -> None:
    """Prende un canal opcional en su propio hilo (si ya está corriendo, no hace nada)."""
    with _control:
        channel = load(name)
        if not channel.is_configured(os.environ):
            raise ChannelError("Falta configurarlo (token y tu ID de usuario).")
        thread = _threads.get(name)
        if thread is not None and thread.is_alive():
            return
        _stopping.discard(name)
        getattr(channel, "reset", lambda: None)()  # que no quede un pedido de apagado viejo
        state.set(name, "starting")
        thread = threading.Thread(target=run_isolated, args=(name, channel), name=f"canal-{name}", daemon=True)
        _threads[name] = thread
        thread.start()


def stop_channel(name: str, timeout: float = 15) -> None:
    """Apaga un canal opcional sin cortar el proceso y espera a que termine de verdad."""
    with _control:
        channel = load(name)
        if not hasattr(channel, "stop"):
            raise ChannelError("Ese canal no se puede apagar desde acá.")
        thread = _threads.get(name)
        if thread is None or not thread.is_alive():
            state.set(name, "off", "Apagado.")
            return
        _stopping.add(name)
        channel.stop()
        thread.join(timeout)
        if thread.is_alive():
            _stopping.discard(name)
            raise ChannelError("No terminó de apagarse a tiempo; probá de nuevo en un momento.")
        state.set(name, "off", "Apagado.")


def install_log_redaction():
    """Hace que ninguna línea de log pueda llevar una clave.

    Las librerías a veces las incluyen (el error «Invalid token» de Telegram
    trae el token entero) y los logs terminan en el journal."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=logging.WARNING)
    for handler in root.handlers:
        if not any(isinstance(f, config.RedactingFilter) for f in handler.filters):
            handler.addFilter(config.RedactingFilter())


def describe(channel, error: BaseException) -> str:
    """Por qué se detuvo un canal, en palabras claras y sin secretos."""
    if isinstance(error, SystemExit) and isinstance(error.code, str):
        text = error.code  # ya viene con un mensaje pensado para el usuario
    else:
        technical = f"{type(error).__name__}: {error}"
        explain = getattr(channel, "explain", None)
        friendly = explain(error) if explain else None
        # El motivo claro primero; el tipo técnico al final, para investigar.
        text = f"{friendly} (detalle técnico: {type(error).__name__})" if friendly else technical
    return config.redact(text)


def run_isolated(name: str, channel):
    """Corre un canal opcional; si falla, anota por qué y muere solo ese canal."""
    try:
        channel.run()
    except (Exception, SystemExit) as e:  # SystemExit: un canal que corta con un mensaje
        state.set(name, "stopped", describe(channel, e))
    else:
        if name not in _stopping:  # si lo apagamos a propósito, stop_channel anota «apagado»
            state.set(name, "stopped", "El canal terminó.")


def main():
    """Arranca la web (siempre) y los demás canales que estén configurados.

    La web va en el hilo principal: es el punto de control por defecto y maneja
    las señales de apagado. Telegram y compañía van en hilos aparte, así que si
    uno falla (token inválido, se cae Telegram) la web sigue funcionando."""
    install_log_redaction()
    enabled = enabled_channels(os.environ)

    for name in CHANNELS:
        if name not in enabled:
            # Apagado a propósito (lo dejaste así desde la web) o sin configurar.
            state.set(name, "off" if switched_off(name) and load(name).is_configured(os.environ) else "skipped")

    primary, *others = enabled

    for name in others:
        # daemon=True (en start_channel): estos hilos no impiden que el proceso se cierre
        # cuando termina la web.
        start_channel(name)

    channel = load(primary)
    try:
        channel.run()
    except (Exception, SystemExit) as e:
        # Si cae el canal principal no hay nada que mantener: dejamos el motivo
        # a la vista y cortamos con código de error (systemd lo reinicia).
        state.set(primary, "stopped", describe(channel, e))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
