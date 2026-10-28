from datetime import datetime, timedelta
import json
import random
import re

import pytest

from epsilon.data import music_index, notes

NOW = datetime(2026, 10, 28, 10, 0)


def playlist(list_id="PLabcdefghij", **extra):
    return {"id": list_id, "title": "pepito 123", "count": 48, "sig": "x", "artists": ["Rammstein", "Metallica"], **extra}


def test_load_without_the_file_or_broken_is_empty(tmp_path):
    assert music_index.load() == music_index.empty()
    notes.safe_path(music_index.FILE).write_text("{no es json", encoding="utf-8")
    assert music_index.load() == music_index.empty()
    notes.safe_path(music_index.FILE).write_text(json.dumps({"playlists": "x"}), encoding="utf-8")
    assert music_index.load() == music_index.empty()


def test_save_then_load_round_trips_and_leaves_no_temp_file():
    index = music_index.stamp({"playlists": [playlist(style="rock", uses=["gym"], energy=5)]}, NOW)

    music_index.save(index)

    loaded = music_index.load()
    assert loaded["playlists"] == index["playlists"] and loaded["updated"] == "2026-10-28T10:00"
    assert not notes.safe_path(music_index.FILE + ".tmp").exists()


def test_load_drops_entries_that_are_not_playlists():
    notes.safe_path(music_index.FILE).write_text(json.dumps({"playlists": [playlist(), "x", {"title": "sin id"}]}), encoding="utf-8")

    assert [p["id"] for p in music_index.load()["playlists"]] == ["PLabcdefghij"]


def test_sig_changes_with_the_title_or_the_count():
    assert music_index.sig("gym", 10) == music_index.sig("gym", 10)
    assert music_index.sig("gym", 10) != music_index.sig("gym", 11)
    assert music_index.sig("gym", 10) != music_index.sig("gym2", 10)


def test_is_stale_when_missing_or_older_than_a_day():
    assert music_index.is_stale(music_index.empty(), NOW)
    fresh = music_index.stamp({"playlists": [playlist()]}, NOW - timedelta(hours=23))
    old = music_index.stamp({"playlists": [playlist()]}, NOW - timedelta(hours=25))
    assert not music_index.is_stale(fresh, NOW) and music_index.is_stale(old, NOW)
    assert music_index.is_stale({"updated": "basura", "playlists": [playlist()]}, NOW)


def test_clean_label_keeps_only_known_uses_and_a_short_single_line_style():
    label = music_index.clean_label({"estilo": "rock\nmetal " + "x" * 200, "usos": ["Gym", "gym", "robar claves", "correr"], "energia": 5})

    assert label["uses"] == ["gym", "correr"] and label["energy"] == 5
    assert "\n" not in label["style"] and len(label["style"]) <= music_index.MAX_STYLE


@pytest.mark.parametrize("raw", [None, "x", {}, {"estilo": "", "usos": [], "energia": 3}, {"estilo": "a", "usos": "gym", "energia": 3},
                                 {"estilo": "a", "usos": [], "energia": 0}, {"estilo": "a", "usos": [], "energia": 6},
                                 {"estilo": "a", "usos": [], "energia": "5"}, {"estilo": "a", "usos": [], "energia": True}])
def test_clean_label_rejects_what_does_not_make_sense(raw):
    assert music_index.clean_label(raw) is None


def test_parse_labels_ignores_broken_json_unknown_ids_and_bad_labels():
    good = {"estilo": "rock", "usos": ["gym"], "energia": 4}
    text = json.dumps({"PLabcdefghij": good, "PLinventada1": good, "PLotra123456": {"estilo": "", "usos": [], "energia": 9}})

    assert music_index.parse_labels(text, {"PLabcdefghij", "PLotra123456"}) == {"PLabcdefghij": {"style": "rock", "uses": ["gym"], "energy": 4}}
    assert music_index.parse_labels("```json\n" + json.dumps({"PLabcdefghij": good}) + "\n```", {"PLabcdefghij"})
    assert music_index.parse_labels("no es json", {"PLabcdefghij"}) == {}
    assert music_index.parse_labels("[1, 2]", {"PLabcdefghij"}) == {}


def test_view_shows_the_id_and_the_label_or_says_it_is_missing():
    index = {"playlists": [playlist(style="rock rápido", uses=["gym", "correr"], energy=5), playlist("PLotra123456", title="sin etiqueta")]}

    text = music_index.view(index, NOW)

    assert "pepito 123 (id: PLabcdefghij, 48 canciones): rock rápido; energía 5/5; sirve para: gym, correr; artistas: Rammstein, Metallica; nunca la pusiste" in text
    assert "sin etiqueta (id: PLotra123456, 48 canciones): sin etiquetar" in text


def test_view_orders_by_how_long_ago_each_was_played_and_says_when():
    index = {"playlists": [
        playlist("PLhoy1234567", title="hoy", played="2026-10-28T08:00"),
        playlist("PLayer123456", title="ayer", played="2026-10-27T08:00"),
        playlist("PLhace5dias1", title="viejo", played="2026-10-23T08:00"),
    ]}

    text = music_index.view(index, NOW, random.Random(1))

    assert text.index("viejo") < text.index("ayer") < text.index("hoy")
    assert "la pusiste hoy" in text and "la pusiste ayer" in text and "la pusiste hace 5 días" in text


def test_never_played_come_first_in_a_random_order_that_varies():
    index = {"playlists": [playlist(f"PLnunca{n:05d}", title=f"nunca{n}") for n in range(6)] + [playlist("PLhoy1234567", title="hoy", played="2026-10-28T08:00")]}

    orders = {tuple(re.findall(r"nunca\d", music_index.view(index, NOW, random.Random(seed)))) for seed in range(8)}

    assert len(orders) > 1  # no siempre la misma primera
    assert all(music_index.view(index, NOW, random.Random(s)).index("hoy (") > music_index.view(index, NOW, random.Random(s)).index("nunca") for s in range(3))


def test_mark_played_stamps_only_that_playlist_and_ignores_unknown_ids():
    music_index.save({"playlists": [playlist(), playlist("PLotra123456")]})

    music_index.mark_played("PLabcdefghij", NOW)
    music_index.mark_played("PLdesconocida", NOW)  # no está en el índice: no pasa nada

    by_id = {p["id"]: p for p in music_index.load()["playlists"]}
    assert by_id["PLabcdefghij"]["played"] == "2026-10-28T10:00" and "played" not in by_id["PLotra123456"]


def test_mark_played_without_an_index_does_nothing():
    music_index.mark_played("PLabcdefghij", NOW)

    assert music_index.load() == music_index.empty()


def big_index(count=24):
    return {"playlists": [
        playlist(f"PL{n:010d}", title=f"playlist número {n}", style="rock rápido y bastante largo de describir", uses=["gym", "correr"], energy=4,
                 artists=["Rammstein", "Metallica", "Slayer", "Megadeth"], played=f"2026-10-{10 + n % 18:02d}T08:00")
        for n in range(count)
    ]}


def test_view_with_a_limit_drops_artists_then_whole_playlists_but_never_cuts_a_line():
    index = big_index()
    full = music_index.view(index, NOW, random.Random(1))

    no_artists = music_index.view(index, NOW, random.Random(1), limit=len(full) - 1)
    tight = music_index.view(index, NOW, random.Random(1), limit=2000)

    assert "artistas" in full and "artistas" not in no_artists and len(no_artists.splitlines()) == 24  # primero sacó los artistas
    assert len(tight) <= 2000 and 0 < len(tight.splitlines()) < 24
    assert all(re.search(r"la pusiste (hoy|ayer|hace \d+ días)$|nunca la pusiste$", line) and "(id: PL" in line for line in tight.splitlines())


def test_view_without_a_limit_or_within_it_is_unchanged():
    index = {"playlists": [playlist(style="rock", uses=["gym"], energy=5)]}

    assert music_index.view(index, NOW) == music_index.view(index, NOW, limit=10_000)
    assert "artistas: Rammstein" in music_index.view(index, NOW, limit=10_000)


def test_view_with_a_limit_keeps_the_ones_that_have_gone_longest_without_playing():
    index = big_index()

    text = music_index.view(index, NOW, random.Random(1), limit=900)

    full = music_index.view(index, NOW, random.Random(1))
    ids = lambda t: re.findall(r"\(id: (PL\d+)", t)  # noqa: E731
    assert 0 < len(ids(text)) < 24 and ids(full)[: len(ids(text))] == ids(text)  # lo que queda es el comienzo: las que hace más que no suenan
