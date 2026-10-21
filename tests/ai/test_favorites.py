import json

import pytest

from epsilon.ai import favorites
from epsilon.data import notes


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())


def test_no_file_means_no_favorites():
    assert favorites.load() == []


def test_save_keeps_the_order_and_drops_repeats():
    saved = favorites.save(["ollama:qwen3:8b", "gemini:gemini-a", "ollama:qwen3:8b"])

    assert saved == ["ollama:qwen3:8b", "gemini:gemini-a"]
    assert favorites.load() == saved


def test_save_can_empty_the_list():
    favorites.save(["gemini:gemini-a"])

    assert favorites.save([]) == [] and favorites.load() == []


@pytest.mark.parametrize("bad", ["gemini", "gemini:", "Gemini:x", "gemini:con espacio", "gemini:a\nb", "x" * 300 + ":m"])
def test_save_rejects_anything_that_is_not_provider_colon_model(bad):
    with pytest.raises(ValueError):
        favorites.save(["gemini:gemini-a", bad])
    assert favorites.load() == []  # no se escribió nada


def test_a_broken_or_edited_file_never_breaks_the_list():
    path = notes.DATA_DIR / favorites.FILE
    path.write_text("{no es json", encoding="utf-8")
    assert favorites.load() == []
    path.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert favorites.load() == []
    path.write_text(json.dumps(["gemini:gemini-a", 3, "raro", "gemini:gemini-a"]), encoding="utf-8")
    assert favorites.load() == ["gemini:gemini-a"]
