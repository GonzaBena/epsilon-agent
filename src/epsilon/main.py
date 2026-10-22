import importlib
import logging
import os
import threading

from epsilon import state, tools
from epsilon.connectors import manager as connectors, registry as connector_registry, store as connector_store
from epsilon.plugins import channels as plugin_channels
from epsilon.routines import engine
from epsilon.settings import config

# Canales disponibles: nombre -> módulo. Para sumar uno incluido se agrega una línea acá; los de
# terceros (plugins, ver plugins/channels.py y docs/plan-plugins.md) los suma refresh_plugins con
# el valor «plugin:<nombre>». El módulo tiene que exponer is_configured(env) y run(), y puede
# exponer explain(error) para traducir sus errores a un mensaje claro (un plugin además tiene
# que exponer stop()).
# El ORDEN importa: el primero es el canal principal (la web) y corre en el
# hilo principal; los demás son opcionales y corren cada uno en su hilo.
CHANNELS = {
    "web": "epsilon.channels.web",
    "telegram": "epsilon.channels.telegram",
}


PLUGIN_PREFIX = "plugin:"
_plugins: dict[str, plugin_channels.Plugin] = {}  # los plugins que conocemos (manifiesto leído)
_modules: dict[str, object] = {}  # el código de los que ya se importaron (recién al prenderlos)
INVALID: dict[str, str] = {}  # carpetas de plugins con manifiesto inválido -> motivo


def is_plugin(name: str) -> bool:
    return CHANNELS.get(name, "").startswith(PLUGIN_PREFIX)


def not_configured_message(name: str) -> str:
    if is_plugin(name):
        return "Falta configurarlo: cargá sus ajustes en Configuración."
    return "Falta configurarlo (token y tu ID de usuario)."


def plugin_info(name: str):
    """El manifiesto leído de un plugin (label, about…), o None si no es un plugin."""
    return _plugins.get(name) if is_plugin(name) else None


def refresh_plugins() -> None:
    """Pone CHANNELS y los ajustes al día con la carpeta de plugins. Solo lee manifiestos: no
    importa ni ejecuta código de ningún plugin. Un plugin nuevo queda apagado."""
    found = plugin_channels.discover()
    INVALID.clear()
    INVALID.update(found.invalid)
    for name in [n for n in CHANNELS if is_plugin(n) and n not in found.plugins]:
        thread = _threads.get(name)
        if thread is None or not thread.is_alive():  # uno que sigue corriendo se queda hasta apagarlo
            del CHANNELS[name]
            _plugins.pop(name, None)
            _modules.pop(name, None)
    for name, plugin in found.plugins.items():
        _plugins[name] = plugin
        if name not in CHANNELS:
            CHANNELS[name] = PLUGIN_PREFIX + name
            if name not in state.snapshot():
                state.set(name, "off", "Apagado.")
    config.set_plugin_settings([s for plugin in _plugins.values() for s in plugin_channels.settings_for(plugin)])


def load(name: str):
    """Importa el módulo de un canal. El de un plugin se importa una sola vez, y si falla lanza
    ChannelError con el motivo."""
    if is_plugin(name):
        module = _modules.get(name)
        if module is None:
            try:
                module = plugin_channels.load_module(_plugins[name])
            except plugin_channels.PluginError as e:
                raise ChannelError(str(e)) from None
            _modules[name] = module
        return module
    return importlib.import_module(CHANNELS[name])


def switched_off(name: str) -> bool:
    """¿Lo apagaste desde la web? Un canal tiene interruptor si existe el ajuste <NOMBRE>_ENABLED."""
    key = f"{name.upper()}_ENABLED"
    return key in config.BY_KEY and not config.enabled(key)


def check_configured(name: str, env) -> bool:
    """¿Tiene su configuración completa? Carga el canal; si es un plugin y su código falla (al
    cargar o dentro de is_configured) lanza ChannelError con el motivo, en vez de tumbar a Epsilon."""
    channel = load(name)
    if not is_plugin(name):
        return channel.is_configured(env)
    try:
        return bool(channel.is_configured(env))
    except (Exception, SystemExit) as e:  # código ajeno: nada de lo que haga puede tumbar el arranque
        raise ChannelError(describe(channel, e)) from None


def _usable(name: str, env) -> bool:
    """¿Está prendido y con su configuración completa? Un canal apagado ni se carga (así un plugin
    apagado no corre código). Si un plugin prendido no carga, queda «detenido» con el motivo."""
    if switched_off(name):
        return False
    try:
        return check_configured(name, env)
    except ChannelError as e:
        state.set(name, "stopped", str(e))
        return False


def enabled_channels(env) -> list[str]:
    """Nombres de los canales que tienen su configuración completa y no están apagados."""
    return [name for name in CHANNELS if _usable(name, env)]


class ChannelError(Exception):
    """No se pudo prender o apagar un canal. El mensaje está listo para mostrar."""


_threads: dict[str, threading.Thread] = {}  # los canales con hilo propio que prendimos
_stopping: set[str] = set()  # los que estamos apagando a propósito (no es un error)
_control = threading.Lock()  # prender y apagar de a uno


def switchable(name: str) -> bool:
    """¿Se puede apagar y prender desde la web? Solo los que tienen interruptor y saben detenerse
    (un plugin siempre: el contrato le exige stop(), y no lo cargamos solo para preguntar)."""
    if is_plugin(name):
        return True
    return f"{name.upper()}_ENABLED" in config.BY_KEY and hasattr(load(name), "stop")


def _reset(name: str, channel) -> None:
    """Que no quede un pedido de apagado viejo. El reset de un plugin que falla no tumba nada."""
    try:
        getattr(channel, "reset", lambda: None)()
    except (Exception, SystemExit) as e:
        if not is_plugin(name):
            raise
        raise ChannelError(describe(channel, e)) from None


def start_channel(name: str) -> None:
    """Prende un canal opcional en su propio hilo (si ya está corriendo, no hace nada)."""
    with _control:
        try:
            channel = load(name)
            configured = check_configured(name, os.environ)
        except ChannelError as e:
            state.set(name, "stopped", str(e))
            raise
        if not configured:
            raise ChannelError(not_configured_message(name))
        thread = _threads.get(name)
        if thread is not None and thread.is_alive():
            return
        _stopping.discard(name)
        try:
            _reset(name, channel)
        except ChannelError as e:
            state.set(name, "stopped", str(e))
            raise
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
        try:
            channel.stop()
        except (Exception, SystemExit) as e:
            _stopping.discard(name)
            if not is_plugin(name):
                raise
            raise ChannelError(describe(channel, e)) from None
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


def setup_connectors() -> None:
    """Las herramientas de los conectores MCP llegan a la IA (proveedor de tools.py) y, si una cambia, vuelve a
    «preguntar». No arranca ningún proceso: eso lo hace `autostart_connectors`, o el primer uso."""
    tools.add_provider(connector_registry.provide)
    connectors.set_changed_hook(connector_registry.reset_changed)


def autostart_connectors() -> None:
    """Arranca los conectores prendidos con «arrancar con Epsilon». Uno que falla queda «detenido» con el motivo
    (lo anota el manager) y no frena a los demás ni a Epsilon."""
    for entry in connector_store.list_all():
        if entry.get("enabled") is True and entry.get("autostart") is True:
            try:
                connectors.start(entry["name"])
            except Exception as e:  # noqa: BLE001 - un conector roto no tumba el arranque
                print(f"No pude arrancar el conector {entry['name']}: {config.redact(str(e))}")


def main():
    """Arranca la web (siempre) y los demás canales que estén configurados.

    La web va en el hilo principal: es el punto de control por defecto y maneja
    las señales de apagado. Telegram y compañía van en hilos aparte, así que si
    uno falla (token inválido, se cae Telegram) la web sigue funcionando."""
    install_log_redaction()
    refresh_plugins()
    enabled = enabled_channels(os.environ)

    try:
        engine.start()  # las rutinas; si no arrancan, la web y los canales siguen igual
    except Exception as e:
        print(f"No pude prender el motor de rutinas: {config.redact(repr(e))}")

    try:
        setup_connectors()
        # En su hilo: arrancar uno puede tardar (uvx/npx bajan el paquete) y la web no tiene que esperarlo.
        threading.Thread(target=autostart_connectors, name="conectores-arranque", daemon=True).start()
    except Exception as e:
        print(f"No pude preparar los conectores: {config.redact(repr(e))}")

    for name in CHANNELS:
        if name not in enabled:
            if is_plugin(name):
                # Apagado: refresh_plugins ya lo dejó «off». Prendido: sin configurar, salvo que
                # no haya cargado (enabled_channels ya anotó el motivo).
                if not switched_off(name) and state.snapshot().get(name, {}).get("state") != "stopped":
                    state.set(name, "skipped")
                continue
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
    finally:
        try:  # al cerrar, que ningún conector quede corriendo suelto
            connectors.stop_all()
        except Exception as e:  # noqa: BLE001
            print(f"No pude cerrar los conectores: {config.redact(repr(e))}")


if __name__ == "__main__":
    main()
