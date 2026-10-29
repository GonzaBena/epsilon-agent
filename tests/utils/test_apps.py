from pathlib import Path

import pytest

from epsilon.utils import apps


def write_app(folder: Path, file_name: str, name: str, extra: str = "", exec_line: str = "true") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name
    path.write_text(f"[Desktop Entry]\nType=Application\nName={name}\nExec={exec_line}\n{extra}", encoding="utf-8")
    return path


@pytest.fixture
def catalog(tmp_path):
    system, user = tmp_path / "usr", tmp_path / "home"
    write_app(system, "firefox.desktop", "Firefox", "GenericName=Navegador web\nKeywords=internet;browser;\n")
    write_app(system, "chromium.desktop", "Chromium", "GenericName=Navegador web\n")
    write_app(system, "org.gnome.Nautilus.desktop", "Files", "Comment=Access and organize files\n")
    write_app(system, "spotify.desktop", "Spotify")
    write_app(user, "spotify.desktop", "Spotify (mi versión)")  # misma id: la de la persona pisa a la del sistema
    write_app(user, "Youtube Music.desktop", "Youtube Music")  # un nombre de archivo con espacio, como los de Omarchy
    write_app(system, "kitty.desktop", "kitty")
    write_app(system, "kitty-open.desktop", "kitty URL Launcher", "NoDisplay=true\n")
    write_app(system, "hidden.desktop", "Oculta", "Hidden=true\n")
    write_app(system, "link.desktop", "Un enlace", "").write_text("[Desktop Entry]\nType=Link\nName=Un enlace\nURL=http://x\n")
    (system / "roto.desktop").write_bytes(b"\xff\xfe\x00 esto no es un .desktop \x00")
    (system / "sinexec.desktop").write_text("[Desktop Entry]\nType=Application\nName=Sin Exec\n")
    return apps.installed([user, system])


def names(found):
    return sorted(app.name for app in found)


# --- Leer las apps instaladas ---------------------------------------------------------------------------------------------


def test_only_real_visible_applications_are_listed(catalog):
    assert names(catalog) == ["Chromium", "Files", "Firefox", "Spotify (mi versión)", "Youtube Music", "kitty"]


def test_the_persons_own_entry_replaces_the_systems_one_with_the_same_id(catalog):
    spotify = [app for app in catalog if app.id == "spotify"]

    assert [app.name for app in spotify] == ["Spotify (mi versión)"]


def test_a_broken_file_never_breaks_the_scan(tmp_path):
    (tmp_path / "x.desktop").write_bytes(b"\xff\xfe\x00\x01")
    write_app(tmp_path, "bien.desktop", "Bien")

    assert names(apps.installed([tmp_path])) == ["Bien"]


def test_a_folder_that_does_not_exist_is_skipped(tmp_path):
    assert apps.installed([tmp_path / "nada"]) == []


def test_the_default_folders_follow_the_xdg_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_DIRS", f"{tmp_path / 'a'}:{tmp_path / 'b'}")

    assert apps.data_dirs() == [tmp_path / "home" / "applications", tmp_path / "a" / "applications", tmp_path / "b" / "applications"]


# --- Encontrar una app por su nombre -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query, expected",
    [
        ("firefox", "Firefox"),
        ("FIREFOX", "Firefox"),
        ("youtube music", "Youtube Music"),
        ("Youtube   Music", "Youtube Music"),
        ("nautilus", "Files"),  # por la id (org.gnome.Nautilus)
        ("files", "Files"),
        ("spot", "Spotify (mi versión)"),  # por el comienzo del nombre
        ("music", "Youtube Music"),  # una palabra del nombre
        ("kit", "kitty"),
    ],
)
def test_an_app_is_found_by_name_id_or_part_of_the_name(catalog, query, expected):
    match = apps.find(query, {}, catalog)

    assert match.kind == "app" and match.name == expected


def test_accents_and_case_do_not_matter(tmp_path):
    write_app(tmp_path, "calc.desktop", "Calculadora científica")

    match = apps.find("CIENTIFICA", {}, apps.installed([tmp_path]))

    assert match.kind == "app" and match.name == "Calculadora científica"


def test_several_equally_good_matches_are_listed_instead_of_guessing(catalog):
    match = apps.find("navegador", {}, catalog)  # lo dicen el Firefox y el Chromium

    assert match.kind == "many"
    assert sorted(match.options) == ["Chromium", "Firefox"]


def test_an_exact_name_wins_over_longer_names_that_contain_it(tmp_path):
    write_app(tmp_path, "a.desktop", "Code")
    write_app(tmp_path, "b.desktop", "Code Editor Pro")
    write_app(tmp_path, "c.desktop", "Barcode Scanner")

    match = apps.find("code", {}, apps.installed([tmp_path]))

    assert match.kind == "app" and match.name == "Code"


def test_the_persons_alias_beats_any_installed_app(catalog):
    aliases = {"navegador": "omarchy-launch-browser", "firefox": "firefox --private-window"}

    by_alias = apps.find("Navegador", aliases, catalog)
    over_app = apps.find("firefox", aliases, catalog)

    assert (by_alias.kind, by_alias.command, by_alias.name) == ("alias", "omarchy-launch-browser", "navegador")
    assert (over_app.kind, over_app.command) == ("alias", "firefox --private-window")


def test_an_app_that_does_not_exist_comes_back_empty_handed(catalog):
    match = apps.find("photoshop", {}, catalog)

    assert match.kind == "none"


@pytest.mark.parametrize("query", ["", " ", "a", "  x  "])
def test_a_query_too_short_to_mean_anything_matches_nothing(catalog, query):
    assert apps.find(query, {}, catalog).kind == "none"


def test_two_entries_with_the_same_name_are_not_ambiguous(tmp_path):
    write_app(tmp_path / "a", "x.desktop", "Telegram")
    write_app(tmp_path / "b", "y.desktop", "Telegram")

    match = apps.find("telegram", {}, apps.installed([tmp_path / "a", tmp_path / "b"]))

    assert match.kind == "app"


def test_a_sample_of_names_helps_the_model_say_what_it_can_open(catalog):
    sample = apps.sample(catalog, {"navegador": "x"}, 4)

    assert len(sample) == 4 and sample[0] == "navegador"
    assert all(isinstance(name, str) for name in sample)


# --- Cómo se lanza -----------------------------------------------------------------------------------------------------------


def test_an_installed_app_is_launched_with_gio_by_its_file_path_quoted(catalog):
    app = next(app for app in catalog if app.name == "Youtube Music")

    command = apps.launch_command(app)

    assert command.startswith("gio launch '") and command.endswith("Youtube Music.desktop'")


def test_a_path_with_anything_unusual_is_never_turned_into_a_command(tmp_path):
    evil = apps.App(id="x", name="x", path=tmp_path / 'a"; reboot; "b.desktop', hints="")

    with pytest.raises(ValueError):
        apps.launch_command(evil)


# --- Los alias de la persona (ajuste PC_APPS) --------------------------------------------------------------------------------


def test_aliases_come_from_alias_equals_command_entries():
    found = apps.parse_aliases(["Navegador = omarchy-launch-browser", "notas=obsidian --new-window", "roto", "=x", "y=", "ok=ls;reboot"])

    assert found == {"navegador": "omarchy-launch-browser", "notas": "obsidian --new-window"}


@pytest.mark.parametrize("command", ["ls | grep x", "a && b", "echo $HOME", "run `x`", "a > b", 'say "hola"', "a\\b", "uno\ndos"])
def test_an_alias_command_cannot_carry_shell_tricks(command):
    assert apps.parse_aliases([f"x={command}"]) == {}
