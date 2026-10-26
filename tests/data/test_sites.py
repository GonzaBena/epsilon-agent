import pytest

from epsilon.data import notes, sites


def test_a_shortcut_is_saved_and_listed(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())

    assert sites.add("  Aula   Inglés ", "idiomas.campus.filo.uba.ar/course/5") == ("aula inglés", "https://idiomas.campus.filo.uba.ar/course/5")
    assert sites.all_sites() == {"aula inglés": "https://idiomas.campus.filo.uba.ar/course/5"}
    assert (tmp_path / "sites.json").is_file()


def test_a_shortcut_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    sites.add("inglés", "https://a.com/")

    with pytest.raises(sites.SitesError, match="Ya hay"):
        sites.add("ingles", "https://b.com/")  # sin acento es el mismo nombre
    assert sites.all_sites() == {"inglés": "https://a.com/"}


def test_removing_a_shortcut(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    sites.add("inglés", "https://a.com/")

    assert sites.remove("INGLES") == ("inglés", "https://a.com/")
    assert sites.remove("inglés") is None
    assert sites.all_sites() == {}


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://localhost:8765/",
        "http://192.168.0.1/",
        "https://a.com@evil.com/",
        "https://bueno.com/redirect?to=https://malo.com",
        "https://a.com/con espacio",
    ],
)
def test_a_shortcut_needs_an_address_the_browser_would_open(tmp_path, monkeypatch, url):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())

    with pytest.raises(sites.SitesError):
        sites.add("x", url)
    assert sites.all_sites() == {}


@pytest.mark.parametrize("name", ["", "   ", "a/b", "../x", "x" * 41, "¿qué?"])
def test_a_shortcut_name_is_a_few_plain_words(tmp_path, monkeypatch, name):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())

    with pytest.raises(sites.SitesError):
        sites.add(name, "https://a.com/")


def test_too_many_shortcuts(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    monkeypatch.setattr(sites, "MAX_SITES", 2)
    sites.add("uno", "https://uno.com/")
    sites.add("dos", "https://dos.com/")

    with pytest.raises(sites.SitesError, match="2 atajos"):
        sites.add("tres", "https://tres.com/")


def test_a_broken_file_reads_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    (tmp_path / "sites.json").write_text("{roto", encoding="utf-8")

    assert sites.all_sites() == {}
