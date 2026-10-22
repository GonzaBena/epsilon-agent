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
