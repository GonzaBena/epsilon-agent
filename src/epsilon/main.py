import importlib
import os
import threading

# Canales disponibles: nombre -> módulo. Para sumar uno (Slack...) se agrega una
# línea acá; el módulo tiene que exponer is_configured(env) y run().
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


def run_isolated(name: str, channel):
    """Corre un canal opcional; si falla, avisa y muere solo ese canal."""
    try:
        channel.run()
    except (Exception, SystemExit) as e:  # SystemExit: un canal que corta con un mensaje
        print(f"Canal {name} se detuvo: {e!r}")


def main():
    """Arranca la web (siempre) y los demás canales que estén configurados.

    La web va en el hilo principal: es el punto de control por defecto y maneja
    las señales de apagado. Telegram y compañía van en hilos aparte, así que si
    uno falla (token inválido, se cae Telegram) la web sigue funcionando."""
    enabled = enabled_channels(os.environ)

    for name in CHANNELS:
        if name not in enabled:
            print(f"Canal {name}: sin configurar, se omite.")

    primary, *others = enabled

    for name in others:
        # daemon=True: estos hilos no impiden que el proceso se cierre cuando
        # termina la web.
        threading.Thread(
            target=run_isolated,
            args=(name, load(name)),
            name=f"canal-{name}",
            daemon=True,
        ).start()

    load(primary).run()


if __name__ == "__main__":
    main()
