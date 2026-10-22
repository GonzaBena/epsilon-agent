import pytest

from epsilon.utils import navegador_reglas as reglas


def test_host_of_lowercases_the_site():
    assert reglas.host_of("https://Music.YouTube.com/x") == "music.youtube.com"


def test_host_of_uses_the_site_after_the_at_sign():
    assert reglas.host_of("https://music.youtube.com@malo.com/") == "malo.com"


def test_host_of_returns_empty_for_garbage():
    assert reglas.host_of("no es una url") == ""


def test_matches_accepts_the_site_and_its_subdomains():
    assert reglas.matches("youtube.com", ["youtube.com"])
    assert reglas.matches("music.youtube.com", ["youtube.com"])


def test_matches_rejects_lookalikes_and_empty_hosts():
    assert not reglas.matches("eviltyoutube.com", ["youtube.com"])
    assert not reglas.matches("", ["youtube.com"])


def test_url_problem_accepts_a_public_https_site():
    assert reglas.url_problem("https://music.youtube.com/", ["142.250.0.1"]) is None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "chrome://settings",
        "javascript:alert(1)",
        "data:text/html,x",
        "view-source:https://a.com",
        "http://localhost:8765",
        "http://127.0.0.1:9222",
        "http://[::1]/",
        "http://impresora.local/",
        "https://a.com@b.com/",
    ],
)
def test_url_problem_rejects_dangerous_or_local_urls(url):
    assert reglas.url_problem(url, ["127.0.0.1"] if "127" in url else ["142.250.0.1"])


@pytest.mark.parametrize("addresses", [["192.168.1.1"], ["10.0.0.5"], ["169.254.1.1"], []])
def test_url_problem_rejects_private_or_unresolved_sites(addresses):
    assert reglas.url_problem("https://ok.com/", addresses)


@pytest.mark.parametrize("label", ["Enviar", "Publicar comentario", "Eliminar lista", "Comprar ahora", "Subscribe", "Delete", ""])
def test_sensitive_labels(label):
    assert reglas.is_sensitive_label(label)


@pytest.mark.parametrize("label", ["Reproducir", "Play", "Mi Supermix", "Buscar"])
def test_harmless_labels(label):
    assert not reglas.is_sensitive_label(label)


@pytest.mark.parametrize("url", ["http://127.0.0.1:9222/json/list", "http://192.168.1.1/", "file:///x", "http://localhost/", "http://a.local/", "https://a.com@b.com/", "https://evil.com%2F.github.io/", "about:blank"])
def test_local_problem_rejects_without_any_dns(url):
    assert reglas.local_problem(url)


def test_local_problem_accepts_a_public_name_or_ip():
    assert reglas.local_problem("https://music.youtube.com/") is None
    assert reglas.local_problem("http://8.8.8.8/") is None


def test_url_problem_rejects_percent_escapes_in_the_site():
    assert reglas.url_problem("https://evil.com%2F.github.io/", ["1.2.3.4"])


@pytest.mark.parametrize("url", ["https://www.youtube.com/redirect?q=https://evil.tld/x", "https://www.youtube.com/watch?next=http%3A%2F%2Fevil.tld", "https://www.youtube.com/redirect?event=1"])
def test_has_embedded_url_flags_redirects(url):
    assert reglas.has_embedded_url(url)


@pytest.mark.parametrize("url", ["https://music.youtube.com/watch?v=abc&list=RD123", "https://music.youtube.com/"])
def test_has_embedded_url_leaves_normal_addresses_alone(url):
    assert not reglas.has_embedded_url(url)


@pytest.mark.parametrize("label", ["Responder", "Reply", "Compartir", "Share", "Guardar", "Save", "Crear", "Subir", "Upload", "Denunciar", "Report", "Bloquear", "Cerrar sesión", "Sign out", "Unsubscribe", "Anular suscripción", "Suscripción", "Aceptar", "Accept"])
def test_more_sensitive_labels(label):
    assert reglas.is_sensitive_label(label)


@pytest.mark.parametrize("label", ["Siguiente", "Anteriores", "Más", "Iniciar búsqueda", "Reproducir", "Pausa"])
def test_navigation_labels_stay_harmless(label):
    assert not reglas.is_sensitive_label(label)


# --- Nombres de sitio sacados del historial ---

HISTORY = [
    ("https://www.youtube.com/", "YouTube", 400, 30),
    ("https://music.youtube.com/", "YouTube Music", 50, 2),
    ("https://github.com/x/y", "x/y", 120, 5),
    ("https://www.mercadolibre.com.ar/", "Mercado Libre", 90, 8),
    ("https://mail.google.com/", "Gmail", 900, 50),
    ("https://youtubeclone.example/", "otro", 1, 0),
]


@pytest.mark.parametrize(
    "query, site",
    [
        ("yt", "youtube.com"),  # letras en orden dentro del nombre (gana el sitio más usado: www.youtube.com)
        ("you", "youtube.com"),  # el nombre empieza igual; gana el más usado
        ("gh", "github.com"),
        ("mercado", "mercadolibre.com.ar"),  # sitio de tres partes
        ("libre", "mercadolibre.com.ar"),  # palabra del título
        ("YT", "youtube.com"),
    ],
)
def test_pick_site_resolves_short_names(query, site):
    assert reglas.pick_site(query, HISTORY, []) == site


def test_pick_site_prefers_the_name_over_a_looser_match():
    rows = [("https://xyzzy.com/", "x", 1, 0), ("https://abc.com/", "yzzy", 500, 9)]
    assert reglas.pick_site("xy", rows, []) == "xyzzy.com"


def test_pick_site_never_returns_a_blocked_site():
    assert reglas.pick_site("gm", HISTORY, ["mail.google.com"]) is None
    assert reglas.pick_site("goo", HISTORY, ["google.com"]) is None


@pytest.mark.parametrize("query", ["", "y", "yt yt", "a/b", "javascript:x", "zzz"])
def test_pick_site_returns_none_for_what_does_not_match(query):
    assert reglas.pick_site(query, HISTORY, []) is None


def test_pick_site_ignores_ip_addresses():
    assert reglas.pick_site("19", [("http://192.168.0.1/", "router", 99, 9)], []) is None


@pytest.mark.parametrize(
    "host, site",
    [("music.youtube.com", "youtube.com"), ("a.b.mercadolibre.com.ar", "mercadolibre.com.ar"), ("x.com", "x.com")],
)
def test_site_of_keeps_the_registrable_part(host, site):
    assert reglas.site_of(host) == site


UNI = [
    ("https://aulasvirtuales.frba.utn.edu.ar/my/", "Aulas Virtuales UTN.BA", 80, 6),
    ("https://auth.frba.utn.edu.ar/realms/FRBA/x", "Acceder a tu cuenta", 40, 2),
    ("https://www.utnianos.com.ar/", "UTNianos", 30, 1),
    ("https://bambulab.com/", "Aula de impresión", 60, 3),
]


@pytest.mark.parametrize(
    "query, host",
    [
        ("aula virtual utn", "aulasvirtuales.frba.utn.edu.ar"),  # una frase: cada palabra tiene que aparecer
        ("aula", "aulasvirtuales.frba.utn.edu.ar"),  # el nombre del sitio gana a una palabra de otro título
        ("aulas virtuales", "aulasvirtuales.frba.utn.edu.ar"),
        ("utn", "aulasvirtuales.frba.utn.edu.ar"),  # entre los que coinciden, el más usado
        ("entrá al aula virtual de la utn", "aulasvirtuales.frba.utn.edu.ar"),  # las palabras cortas no cuentan
    ],
)
def test_pick_site_keeps_the_subdomain_and_accepts_phrases(query, host):
    assert reglas.pick_site(query, UNI, []) == host


def test_pick_site_phrase_needs_its_words():
    assert reglas.pick_site("banco galicia tarjeta", UNI, []) is None
    assert reglas.pick_site("aula banco", UNI, []) is None  # con dos palabras, las dos


def test_known_site_accepts_the_host_or_a_parent_in_the_history():
    assert reglas.known_site("aulasvirtuales.frba.utn.edu.ar", UNI)
    assert reglas.known_site("frba.utn.edu.ar", UNI)  # un padre de un sitio visitado
    assert not reglas.known_site("aulavirtualutn.com", UNI)
