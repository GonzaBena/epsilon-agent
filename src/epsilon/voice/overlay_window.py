#!/usr/bin/env python3
"""La ventanita del Voice Companion: una tarjeta anclada a una esquina (o al centro) con lo que va pasando.

Es un programa APARTE de Epsilon (lo lanza `epsilon/voice/overlay.py`, ver `Window`): corre con el Python del sistema, que trae
PyGObject, y usa GTK4 con gtk4-layer-shell, la forma estándar en Wayland de poner una ventana encima de todo, anclada, sin robar el
foco. Epsilon le escribe una orden JSON por línea en stdin; él contesta por stdout «ready» o «error: motivo».

    {"op": "show", "kind": "...", "title": "...", "heard": "...", "text": "...", "layout": {"anchors": [...], "margin": 24, "width": 460, "font": 15}}
    {"op": "hide"}
    {"op": "quit"}
    {"op": "input", "title": "...", "layout": {...}}      abre un campo de texto que toma el teclado (escribirle a Epsilon)
    {"op": "close_input"}                                 lo cierra sin enviar

Además de «ready» y «error: motivo», contesta por stdout `submit:<texto en JSON>` (Enter) y `closed` (Esc o close_input).

Para ver cómo queda SIN Epsilon (abre una ventana unos segundos; layer-shell exige cargarse antes que libwayland):

    LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so python3 -I overlay_window.py --demo abajo-derecha

Este archivo no importa nada de Epsilon. GTK se importa recién dentro de `main()`: cargarlo (o probarlo) nunca toca la pantalla.
"""

import json
import os
import sys

# Dónde puede aparecer. Es la misma tabla que `overlay.POSITIONS` de Epsilon (un test vigila que coincidan); acá solo la usa la demo,
# porque en el uso normal Epsilon manda los bordes ya resueltos.
POSITIONS = {
    "arriba-izquierda": ("top", "left"),
    "arriba-centro": ("top",),
    "arriba-derecha": ("top", "right"),
    "centro": (),
    "abajo-izquierda": ("bottom", "left"),
    "abajo-centro": ("bottom",),
    "abajo-derecha": ("bottom", "right"),
}
DEFAULT_POSITION = "abajo-derecha"
EDGES = ("top", "bottom", "left", "right")
KINDS = ("listening", "working", "talking", "done", "alert", "info", "asking")
OPS = ("show", "hide", "quit", "input", "close_input")

MAX_TITLE, MAX_HEARD, MAX_TEXT = 120, 400, 1500
MAX_INPUT = 500  # lo más largo que se puede escribir (Epsilon lo vuelve a validar: un test vigila que coincidan)
INPUT_TITLE = "Preguntale a Epsilon"
MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH = 240, 1200, 460
MAX_MARGIN, DEFAULT_MARGIN = 400, 24
MIN_FONT, MAX_FONT, DEFAULT_FONT = 10, 32, 15


# --- Las órdenes que llegan (todo se valida: lo que viene por la tubería es un dato, no código) ---------------------------------


def parse(line):
    """Una línea de stdin -> una orden conocida (dict) o None si no sirve."""
    try:
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        command = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(command, dict) or command.get("op") not in OPS:
        return None
    return command


def _text(value, limit):
    return value[:limit] if isinstance(value, str) else ""


def _number(value, low, high, default):
    """Un entero dentro de [low, high]; lo que no es un número entero vuelve al valor por defecto."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(low, min(high, value))


def _layout(command):
    """El `layout` de una orden, con todo en su sitio y dentro de límites, venga lo que venga."""
    layout = command.get("layout") if isinstance(command.get("layout"), dict) else {}
    if "anchors" in layout and isinstance(layout["anchors"], list):
        anchors = list(dict.fromkeys(a for a in layout["anchors"] if a in EDGES))  # sin repetidos y solo bordes que existen
    else:
        anchors = list(POSITIONS[DEFAULT_POSITION])
    return {
        "anchors": anchors,
        "margin": _number(layout.get("margin"), 0, MAX_MARGIN, DEFAULT_MARGIN),
        "width": _number(layout.get("width"), MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH),
        "font": _number(layout.get("font"), MIN_FONT, MAX_FONT, DEFAULT_FONT),
    }


def clean_show(command):
    """Una orden `show` con todo en su sitio y dentro de límites, venga lo que venga."""
    return {
        "op": "show",
        "kind": command.get("kind") if command.get("kind") in KINDS else "info",
        "title": _text(command.get("title"), MAX_TITLE),
        "heard": _text(command.get("heard"), MAX_HEARD),
        "text": _text(command.get("text"), MAX_TEXT),
        "layout": _layout(command),
    }


def clean_input(command):
    """Una orden `input` con todo en su sitio y dentro de límites."""
    return {"op": "input", "title": _text(command.get("title"), MAX_TITLE) or INPUT_TITLE, "layout": _layout(command)}


def accepts(command, typing):
    """¿Se obedece esta orden? Mientras la persona escribe, un `show` o un `hide` (p. ej. el temporizador del turno anterior)
    no pueden cerrarle ni pisarle la tarjeta: solo valen abrir/cerrar el campo y salir."""
    return not typing or command.get("op") in ("input", "close_input", "quit")


def submitted(text):
    """Lo que se escribió, sin espacios de más; None si no hay nada o es más largo que `MAX_INPUT`."""
    text = text.strip() if isinstance(text, str) else ""
    return text if 0 < len(text) <= MAX_INPUT else None


def submit_line(text):
    """La línea que le avisa a Epsilon lo que se escribió (JSON: los saltos de línea y las comillas no la rompen)."""
    return "submit:" + json.dumps(text, ensure_ascii=False)


# --- Cómo se ve -------------------------------------------------------------------------------------------------------------


def css(font):
    """El estilo: una tarjeta oscura y translúcida con esquinas redondeadas; el título cambia de color según lo que pasa."""
    small = max(9, round(font * 0.72))
    return f"""
window.epsilon-overlay {{ background: transparent; }}
.card {{ background-color: rgba(20, 21, 26, 0.93); border-radius: 16px; border: 1px solid rgba(255, 255, 255, 0.10);
         padding: 14px 18px; }}
.card .title {{ font-size: {small}px; font-weight: 700; letter-spacing: 0.6px; color: #9aa1b2; }}
.card .heard {{ font-size: {small + 1}px; font-style: italic; color: #b9bfcd; }}
.card .text {{ font-size: {font}px; color: #f2f3f6; }}
.card.listening .title {{ color: #7fd1ff; }}
.card.working .title {{ color: #ffd37f; }}
.card.talking .title {{ color: #c4a7ff; }}
.card.done .title {{ color: #9be28f; }}
.card.alert .title {{ color: #ff9b8a; }}
.card.info .title {{ color: #9aa1b2; }}
.card.asking .title {{ color: #f5a6d3; }}
.card entry {{ background-color: rgba(255, 255, 255, 0.08); color: #f2f3f6; border-radius: 8px; padding: 6px 10px;
              font-size: {font}px; min-height: 0; }}
"""


def wrap_chars(width, font):
    """Tope de caracteres por renglón para el ancho NATURAL del texto. Es una cota por abajo a propósito (letras anchas
    como la W): si midiera de más, la tarjeta se ensancharía con los textos largos; el renglón se parte igual al ancho real."""
    return max(10, int((width - 36) / (font * 0.8)))


def demo_commands(position):
    """Una charla de ejemplo, para ver cómo queda la ventanita en una esquina sin que corra Epsilon."""
    anchors = list(POSITIONS.get(position, POSITIONS[DEFAULT_POSITION]))
    layout = {"anchors": anchors, "margin": DEFAULT_MARGIN, "width": DEFAULT_WIDTH, "font": DEFAULT_FONT}

    def show(kind, title, heard="", text=""):
        return {"op": "show", "kind": kind, "title": title, "heard": heard, "text": text, "layout": layout}

    asked = "¿Qué hora es y qué clima hace?"
    return [
        show("listening", "Escuchando…"),
        show("working", "Transcribiendo…"),
        show("working", "Pensando…", asked),
        show("working", "Usando Consultar la fecha…", asked),
        show("talking", "Respondiendo…", asked, "Son las diez de la noche"),
        show("talking", "Respondiendo…", asked, "Son las diez de la noche. Afuera hay 18 grados y"),
        show("done", "Listo", asked, "Son las diez de la noche. Afuera hay 18 grados y el cielo está despejado."),
        {"op": "hide"},
    ]


# --- La ventana (GTK4 + layer-shell) ----------------------------------------------------------------------------------------


def main(argv):
    demo = "--demo" in argv
    demo_input = "--demo-input" in argv  # abre solo el campo de texto (para probar que toma el teclado); Enter o Esc lo cierran
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        from gi.repository import Gdk, Gio, GLib, Gtk, Pango
        from gi.repository import Gtk4LayerShell as LayerShell
    except Exception as e:  # noqa: BLE001 - falta PyGObject, GTK4 o gtk4-layer-shell: Epsilon lo muestra y sigue con avisos
        print(f"error: no pude cargar GTK4 con layer-shell ({e.__class__.__name__})", flush=True)
        return 2
    if not Gtk.init_check():
        print("error: no pude conectar con la pantalla", flush=True)
        return 2
    if not LayerShell.is_supported():
        print("error: el compositor no soporta layer-shell (o falta cargar gtk4-layer-shell con LD_PRELOAD)", flush=True)
        return 2

    # Cuánto teclado pide el campo: EXCLUSIVE lo recibe apenas aparece. Si Hyprland no devolviera bien el foco a tu ventana de
    # trabajo al cerrarlo, probar LayerShell.KeyboardMode.ON_DEMAND.
    input_keyboard_mode = LayerShell.KeyboardMode.EXCLUSIVE

    edge_of = {"top": LayerShell.Edge.TOP, "bottom": LayerShell.Edge.BOTTOM, "left": LayerShell.Edge.LEFT, "right": LayerShell.Edge.RIGHT}

    class Card:
        def __init__(self, app):
            self.win = Gtk.Window(application=app)
            self.win.set_decorated(False)
            self.win.set_resizable(False)
            self.win.add_css_class("epsilon-overlay")
            LayerShell.init_for_window(self.win)
            LayerShell.set_namespace(self.win, "epsilon-voice")
            LayerShell.set_layer(self.win, LayerShell.Layer.OVERLAY)  # encima de todo, también de lo que está en pantalla completa
            LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.NONE)  # nunca roba el teclado ni el foco
            LayerShell.set_exclusive_zone(self.win, 0)  # no empuja a las otras ventanas
            self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            self.card.add_css_class("card")
            self.title, self.heard, self.text = Gtk.Label(), Gtk.Label(), Gtk.Label()
            for label, name in ((self.title, "title"), (self.heard, "heard"), (self.text, "text")):
                label.add_css_class(name)
                label.set_xalign(0.0)
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                self.card.append(label)
            self.entry = Gtk.Entry()
            self.entry.set_max_length(MAX_INPUT)
            self.entry.set_visible(False)
            self.entry.connect("activate", self._on_enter)
            self.card.append(self.entry)
            keys = Gtk.EventControllerKey()
            keys.connect("key-pressed", self._on_key)
            self.win.add_controller(keys)
            self.typing = False
            self.on_done = None  # solo la demo: qué hacer cuando se envió o se cerró el campo
            self.win.set_child(self.card)
            self.win.connect("map", self._click_through)
            self.provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.kind = ""
            self.layout = None

        def _click_through(self, *_):
            """Los clics pasan de largo por la tarjeta: es para mirar, no para tocar (si no se puede, queda clickeable)."""
            try:
                import cairo

                gi.require_foreign("cairo")
                self.win.get_surface().set_input_region(cairo.Region())
            except Exception:  # noqa: BLE001
                pass

        def apply_layout(self, layout):
            if layout == self.layout:
                return
            self.layout = layout
            for name, edge in edge_of.items():
                LayerShell.set_anchor(self.win, edge, name in layout["anchors"])
                LayerShell.set_margin(self.win, edge, layout["margin"])
            self.card.set_size_request(layout["width"], -1)
            chars = wrap_chars(layout["width"], layout["font"])
            for label in (self.title, self.heard, self.text):
                label.set_max_width_chars(chars)
            style = css(layout["font"])
            if hasattr(self.provider, "load_from_string"):
                self.provider.load_from_string(style)
            else:
                self.provider.load_from_data(style.encode(), -1)

        def show(self, command):
            self.apply_layout(command["layout"])
            if self.kind:
                self.card.remove_css_class(self.kind)
            self.kind = command["kind"]
            self.card.add_css_class(self.kind)
            for label, value in ((self.title, command["title"]), (self.heard, command["heard"]), (self.text, command["text"])):
                label.set_text(value)
                label.set_visible(bool(value))
            self.win.set_visible(True)

        def hide(self):
            self.win.set_visible(False)

        def enter_input(self, command):
            """Abre el campo de texto: pide el teclado SOLO mientras esté abierto."""
            self.apply_layout(command["layout"])
            if self.kind:
                self.card.remove_css_class(self.kind)
            self.kind = "asking"
            self.card.add_css_class(self.kind)
            self.title.set_text(command["title"])
            self.title.set_visible(True)
            self.heard.set_visible(False)
            self.text.set_visible(False)
            self.entry.set_text("")
            self.entry.set_visible(True)
            LayerShell.set_keyboard_mode(self.win, input_keyboard_mode)
            self.win.set_visible(True)
            self.entry.grab_focus()
            self.typing = True

        def leave_input(self):
            """Cierra el campo y devuelve el teclado: la ventana de trabajo recupera el foco."""
            self.typing = False
            self.entry.set_visible(False)
            LayerShell.set_keyboard_mode(self.win, LayerShell.KeyboardMode.NONE)
            self.win.set_visible(False)
            if self.on_done:
                self.on_done()

        def close_input(self):
            if self.typing:
                print("closed", flush=True)
                self.leave_input()

        def _on_enter(self, entry):
            text = submitted(entry.get_text())
            if text is None or not self.typing:
                return  # vacío o demasiado largo: el campo sigue abierto
            print(submit_line(text), flush=True)
            self.leave_input()

        def _on_key(self, _controller, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape and self.typing:
                self.close_input()
                return True
            return False

    app = Gtk.Application(application_id="dev.epsilon.VoiceOverlay", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def on_activate(app):
        app.hold()  # sin ventanas visibles igual sigue vivo, esperando órdenes
        card = Card(app)

        def handle(command):
            try:
                if not accepts(command, card.typing):
                    return False
                if command["op"] == "show":
                    card.show(clean_show(command))
                elif command["op"] == "hide":
                    card.hide()
                elif command["op"] == "input":
                    card.enter_input(clean_input(command))
                elif command["op"] == "close_input":
                    card.close_input()
                else:
                    app.quit()
            except Exception as e:  # noqa: BLE001 - si dibujar falla, mejor que Epsilon vuelva a las notificaciones
                print(f"error: {e.__class__.__name__}: {str(e)[:120]}", flush=True)
                app.quit()
            return False

        if demo_input:
            card.on_done = app.quit
            GLib.idle_add(handle, {"op": "input", "layout": {"anchors": list(POSITIONS[DEFAULT_POSITION])}})
        elif demo:
            position = next((a for a in argv[1:] if not a.startswith("--")), DEFAULT_POSITION)
            delay = 0
            for step in demo_commands(position):
                GLib.timeout_add(delay, handle, step)
                delay += 1400
            GLib.timeout_add(delay + 1200, lambda: app.quit() or False)
        else:
            fd = sys.stdin.fileno()
            os.set_blocking(fd, False)
            pending = [b""]

            def readable(_fd, _condition):
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    return True
                except OSError:
                    chunk = b""
                if not chunk:  # Epsilon cerró la tubería: se terminó
                    app.quit()
                    return False
                *lines, pending[0] = (pending[0] + chunk).split(b"\n")
                for line in lines:
                    if command := parse(line):
                        handle(command)
                return True

            GLib.io_add_watch(fd, GLib.PRIORITY_DEFAULT, GLib.IOCondition.IN | GLib.IOCondition.HUP, readable)
        print("ready", flush=True)

    app.connect("activate", on_activate)
    return app.run(None)  # None: los argumentos propios (--demo) no son de GApplication


if __name__ == "__main__":
    sys.exit(main(sys.argv))
