from datetime import date

from epsilon import notes, usage


def use_tmp_data(tmp_path, monkeypatch):
    # Todo el registro va a una carpeta temporal, nunca a tus datos reales.
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (1.0, 4.0)})  # USD por 1M tokens


def test_summary_without_records_is_zero(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    result = usage.summary()

    assert result["total"] == {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "unpriced": 0}
    assert result["today"] == result["total"]


def test_record_appends_and_summary_adds_up(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    usage.record("modelo-a", 1_000_000, 500_000)
    usage.record("modelo-a", 1_000_000, 0)

    total = usage.summary()["total"]
    assert total["calls"] == 2
    assert total["input"] == 2_000_000
    assert total["output"] == 500_000
    # 2M entrada * 1.0 + 0.5M salida * 4.0 = 4.0 USD
    assert total["cost"] == 4.0


def test_today_excludes_older_records(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    usage.record("modelo-a", 100, 10)
    # Un registro viejo escrito a mano en el archivo.
    old = '{"date": "2020-01-01", "model": "modelo-a", "input": 900, "output": 90}\n'
    with (tmp_path / usage.USAGE_FILE).open("a", encoding="utf-8") as f:
        f.write(old)

    result = usage.summary()

    assert result["total"]["input"] == 1000
    assert result["today"]["input"] == 100
    assert result["today"]["calls"] == 1


def test_unknown_model_counts_tokens_but_not_cost(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    usage.record("modelo-raro", 1_000_000, 1_000_000)

    total = usage.summary()["total"]
    assert total["input"] == 1_000_000
    assert total["cost"] == 0.0  # no inventamos un precio
    assert total["unpriced"] == 1  # pero avisamos que quedó sin precio
