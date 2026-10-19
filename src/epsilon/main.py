import importlib
import os

# Canales disponibles: nombre -> módulo. Para sumar uno (web, Slack...) se agrega
# una línea acá; el módulo tiene que exponer is_configured(env) y run().
CHANNELS = {
    "telegram": "epsilon.channels.telegram",
}


def load(name: str):
    """Importa el módulo de un canal."""
    return importlib.import_module(CHANNELS[name])


def enabled_channels(env) -> list[str]:
    """Nombres de los canales que tienen su configuración completa."""
    return [name for name in CHANNELS if load(name).is_configured(env)]


def main():
    """Arranca los canales configurados. Uno sin configurar se omite, no tumba al resto."""
    enabled = enabled_channels(os.environ)

    for name in CHANNELS:
        if name not in enabled:
            print(f"Canal {name}: sin configurar, se omite.")

    if not enabled:
        raise SystemExit(
            "No hay ningún canal configurado. "
            "Completá TELEGRAM_TOKEN y ALLOWED_USER_ID en el .env."
        )

    # Por ahora hay un solo canal y es bloqueante. Cuando exista la web habrá
    # que decidir cómo conviven dos canales en el mismo proceso.
    load(enabled[0]).run()


if __name__ == "__main__":
    main()
