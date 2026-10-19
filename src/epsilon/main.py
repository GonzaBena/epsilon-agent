import importlib
import logging
import os
import threading

from epsilon import config, state

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


def enabled_channels(env) -> list[str]:
    """Nombres de los canales que tienen su configuración completa."""
    return [name for name in CHANNELS if load(name).is_configured(env)]


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
            state.set(name, "skipped")

    primary, *others = enabled

    for name in others:
        state.set(name, "starting")
        # daemon=True: estos hilos no impiden que el proceso se cierre cuando
        # termina la web.
        threading.Thread(
            target=run_isolated,
            args=(name, load(name)),
            name=f"canal-{name}",
            daemon=True,
        ).start()

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
