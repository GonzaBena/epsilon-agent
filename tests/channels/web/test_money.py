import json
import shutil
import subprocess

import pytest

from tests.channels.web.helpers import js, page, static_path

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="hace falta node para probar el JS")


def format_usd(values):
    """Corre js/money.js en node (sin navegador) y devuelve formatUsd de cada valor."""
    script = (
        "const w = {}; global.window = w; "
        f"eval(require('fs').readFileSync({json.dumps(str(static_path('money.js')))}, 'utf8')); "
        f"console.log(JSON.stringify({json.dumps(values)}.map((v) => w.formatUsd(v))));"
    )
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


@needs_node
def test_tiny_costs_do_not_show_as_zero():
    # El «hola» del chat: 65 tokens de entrada y 11 de salida cuestan US$ 0,0000328.
    assert format_usd([0.0000328]) == ["US$ 0,0000328"]
    assert format_usd([0.00123]) == ["US$ 0,00123"]


@needs_node
def test_usual_amounts_keep_their_format():
    assert format_usd([0, 0.5, 2, 1234.5]) == ["US$ 0,0000", "US$ 0,5000", "US$ 2,00", "US$ 1.234,50"]


@needs_node
def test_absurdly_small_costs_are_capped():
    assert format_usd([1e-12]) == ["US$ 0,00000000"]


def test_the_chat_and_the_usage_page_use_the_shared_format():
    assert '<script src="/static/js/money.js" defer></script>' in page("index.html")
    assert '<script src="/static/js/money.js" defer></script>' in page("uso.html")
    assert "window.formatUsd" in js("app.js") and "window.formatUsd" in js("uso.js")
    assert "toFixed(4)" not in js("app.js")
