from datetime import datetime, timedelta
import json

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

    text = music_index.view(index)

    assert "pepito 123 (id: PLabcdefghij, 48 canciones): rock rápido; energía 5/5; sirve para: gym, correr; artistas: Rammstein, Metallica" in text
    assert "sin etiqueta (id: PLotra123456, 48 canciones): sin etiquetar" in text
