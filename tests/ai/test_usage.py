import pytest
import json
from datetime import date

from epsilon.ai import usage
from epsilon.data import notes


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


def test_format_summary_shows_today_total_and_cost():
    period = {"calls": 2, "input": 1_234_000, "output": 5_000, "cost": 0.5, "unpriced": 0}
    today = {"calls": 1, "input": 100, "output": 10, "cost": 0.0001, "unpriced": 0}

    text = usage.format_summary({"total": period, "today": today})

    assert "Hoy" in text and "Total" in text
    assert "1.234.000" in text  # miles con punto, como se lee en español
    assert "US$ 0.5000" in text
    assert "US$ 0.0001" in text


def test_format_summary_warns_about_unpriced_calls():
    empty = {"calls": 0, "input": 0, "output": 0, "cost": 0.0, "unpriced": 0}
    total = {"calls": 3, "input": 10, "output": 5, "cost": 0.0, "unpriced": 3}

    text = usage.format_summary({"total": total, "today": empty})

    assert "sin precio" in text
    assert "US$ 0.0000" not in text.split("Total")[1]  # no muestra un costo falso


def test_unknown_model_counts_tokens_but_not_cost(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    usage.record("modelo-raro", 1_000_000, 1_000_000)

    total = usage.summary()["total"]
    assert total["input"] == 1_000_000
    assert total["cost"] == 0.0  # no inventamos un precio
    assert total["unpriced"] == 1  # pero avisamos que quedó sin precio


# --- Quién pidió cada llamada: canal, acción y hora ---------------------------


def read_lines(tmp_path):
    import json

    return [json.loads(l) for l in (tmp_path / usage.USAGE_FILE).read_text(encoding="utf-8").splitlines()]


def test_a_record_carries_time_and_who_asked(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    with usage.channel("Telegram"), usage.action("Resumen"):
        usage.record("modelo-a", 100, 10)

    [line] = read_lines(tmp_path)
    assert line["channel"] == "Telegram" and line["action"] == "Resumen"
    assert len(line["time"]) == 8 and line["time"][2] == ":"  # HH:MM:SS


def test_without_context_the_record_has_no_channel_or_action(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    usage.record("modelo-a", 100, 10)

    [line] = read_lines(tmp_path)
    assert "channel" not in line and "action" not in line


def test_the_context_ends_with_the_block_and_nests(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    with usage.channel("Web"):
        with usage.action("Imagen a CSV"):
            usage.record("modelo-a", 1, 1)
        usage.record("modelo-a", 2, 2)
    usage.record("modelo-a", 3, 3)

    a, b, c = read_lines(tmp_path)
    assert (a["channel"], a["action"]) == ("Web", "Imagen a CSV")
    assert (b["channel"], "action" in b) == ("Web", False)
    assert "channel" not in c


def test_two_threads_do_not_mix_their_channels(tmp_path, monkeypatch):
    import threading

    use_tmp_data(tmp_path, monkeypatch)
    gate = threading.Barrier(2)

    def work(name):
        with usage.channel(name):
            gate.wait()  # los dos adentro del bloque a la vez
            usage.record("modelo-a", 1, 1)

    threads = [threading.Thread(target=work, args=(n,)) for n in ("Web", "Telegram")]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert sorted(l["channel"] for l in read_lines(tmp_path)) == ["Telegram", "Web"]


def test_core_notes_the_channel_from_the_session(tmp_path, monkeypatch):
    from epsilon import core

    use_tmp_data(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(core, "_dispatch", lambda msg: seen.append(usage._channel.get()) or core.Reply("ok"))

    core.handle(core.Message(text="x", session_id="telegram:7"))
    core.handle(core.Message(text="x", session_id="web:20261019-153000-ab12"))
    core.handle(core.Message(text="x"))

    assert seen == ["Telegram", "Web", "Web"] and usage._channel.get() is None


def test_extract_and_summarize_note_their_action(tmp_path, monkeypatch):
    from epsilon.ai import extract, llm, summarize

    use_tmp_data(tmp_path, monkeypatch)
    actions = []
    monkeypatch.setattr(llm, "generate", lambda *a, **k: actions.append(usage._action.get()) or '[{"a": "1"}]')
    monkeypatch.setattr(summarize, "build_part", lambda path: "parte")

    extract.img_to_rows(b"x", "image/png", ["a"])
    summarize.summarize(tmp_path / "x.md")

    assert actions == ["Imagen a CSV", "Resumen"]


# --- El informe de la página Uso ----------------------------------------------


def write(tmp_path, *entries):
    import json

    with (tmp_path / usage.USAGE_FILE).open("a", encoding="utf-8") as f:
        for e in entries:
            f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")


def test_report_splits_today_week_month_and_total(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    today = date(2026, 10, 21)  # miércoles: la semana empieza el lunes 19
    base = {"model": "modelo-a", "input": 1_000_000, "output": 0}
    write(
        tmp_path,
        {**base, "date": "2026-10-21"},  # hoy
        {**base, "date": "2026-10-19"},  # lunes: esta semana
        {**base, "date": "2026-10-18"},  # domingo: semana pasada, mismo mes
        {**base, "date": "2026-09-30"},  # el mes pasado
    )

    periods = {p["key"]: p for p in usage.report(today)["periods"]}

    assert [periods[k]["calls"] for k in ("today", "week", "month", "total")] == [1, 2, 3, 4]
    assert periods["month"]["cost"] == 3.0 and periods["total"]["cost"] == 4.0
    assert [p["label"] for p in usage.report(today)["periods"]] == ["Hoy", "Esta semana", "Este mes", "Total"]


def test_report_lists_todays_calls_newest_first_with_their_price(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    today = date(2026, 10, 21)
    write(
        tmp_path,
        {"date": "2026-10-21", "time": "09:12:03", "model": "modelo-a", "input": 1_000_000, "output": 250_000,
         "action": "Resumen", "channel": "Web"},
        {"date": "2026-10-21", "time": "10:42:40", "model": "otro-modelo", "input": 10, "output": 5,
         "action": "Imagen a CSV", "channel": "Telegram"},
        {"date": "2026-10-20", "time": "23:00:00", "model": "modelo-a", "input": 9, "output": 9},  # ayer: no va
    )

    report = usage.report(today)

    assert [r["time"] for r in report["rows"]] == ["10:42", "09:12"]
    first, second = report["rows"]
    assert first["cost"] is None  # modelo sin precio: no se inventa
    assert second["cost"] == 2.0 and second["action"] == "Resumen" and second["channel"] == "Web"
    assert report["models"] == ["modelo-a", "otro-modelo"]


def test_old_records_without_time_action_or_channel_still_count(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    write(tmp_path, {"date": "2026-10-21", "model": "modelo-a", "input": 100, "output": 10})

    report = usage.report(date(2026, 10, 21))

    [row] = report["rows"]
    assert row["time"] == "" and row["action"] is None and row["channel"] is None
    assert report["periods"][0]["calls"] == 1


def test_report_skips_broken_lines_instead_of_failing(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    write(
        tmp_path,
        "esto no es json",
        '{"date": "ayer", "model": "m", "input": 1, "output": 1}',
        '{"date": "2026-10-21", "model": "modelo-a"}',  # faltan tokens
        {"date": "2026-10-21", "model": "modelo-a", "input": 5, "output": 5},
    )

    assert usage.report(date(2026, 10, 21))["periods"][3]["calls"] == 1


def test_report_without_a_file_is_all_zeros(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    report = usage.report(date(2026, 10, 21))

    assert [p["calls"] for p in report["periods"]] == [0, 0, 0, 0] and report["rows"] == []


# --- Gasto por conversación ----------------------------------------------------

CID = "20261020-100000-aaaa"
OTHER = "20261020-100001-bbbb"


def test_record_tags_conversation(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)

    with usage.conversation(CID):
        usage.record("modelo-a", 10, 1)
    usage.record("modelo-a", 10, 1)

    lines = [json.loads(l) for l in (tmp_path / usage.USAGE_FILE).read_text().splitlines()]
    assert lines[0]["conversation"] == CID
    assert "conversation" not in lines[1]


def test_for_conversation_sums_only_that_conversation(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    with usage.conversation(CID):
        usage.record("modelo-a", 1_000_000, 0)  # 1.0 USD
    with usage.conversation(OTHER):
        usage.record("modelo-a", 5_000_000, 0)
    usage.record("modelo-a", 9_000_000, 0)  # sin conversación

    result = usage.for_conversation(CID)

    assert result["cost"] == 1.0
    assert result["calls"] == 1


def test_for_conversation_context_is_last_chat_input(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    with usage.conversation(CID):
        with usage.action("Chat"):
            usage.record("modelo-a", 100, 5)
            usage.record("modelo-a", 250, 5)
        with usage.action("Resumen"):
            usage.record("modelo-a", 9999, 5)

    assert usage.for_conversation(CID)["context"] == 250


def test_for_conversation_new_is_zero_even_with_global_spend(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    usage.record("modelo-a", 2_000_000, 0)

    assert usage.for_conversation(CID) == {"cost": 0.0, "calls": 0, "context": 0}


def test_for_conversation_skips_broken_lines(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    with usage.conversation(CID):
        usage.record("modelo-a", 1_000_000, 0)
    with (tmp_path / usage.USAGE_FILE).open("a", encoding="utf-8") as f:
        f.write("esto no es json\n")

    assert usage.for_conversation(CID)["cost"] == 1.0


def test_price_known(monkeypatch):
    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (1.0, 4.0)})

    assert usage.price_known("modelo-a")
    assert not usage.price_known("modelo-b")


# --- for_conversation no relee todo el registro en cada consulta ------------------------


def counting_parser(monkeypatch):
    """Cuenta cuántas líneas del registro se interpretan (para saber si se relee todo)."""
    real = usage._parse_line
    seen = []

    def counting(line):
        seen.append(line)
        return real(line)

    monkeypatch.setattr(usage, "_parse_line", counting)
    return seen


def test_for_conversation_only_parses_lines_added_since_the_last_call(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    with usage.conversation(CID):
        for _ in range(3):
            usage.record("modelo-a", 1_000_000, 0)
    usage.for_conversation(CID)  # primera vez: lee lo que hay
    seen = counting_parser(monkeypatch)

    with usage.conversation(CID):
        usage.record("modelo-a", 1_000_000, 0)
    result = usage.for_conversation(CID)

    assert len(seen) == 1
    assert result["calls"] == 4 and result["cost"] == 4.0
    usage.for_conversation(CID)
    assert len(seen) == 1  # sin líneas nuevas, no se interpreta nada


def test_for_conversation_rebuilds_when_the_file_is_replaced_by_a_smaller_one(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    with usage.conversation(CID):
        usage.record("modelo-a", 1_000_000, 0)
        usage.record("modelo-a", 1_000_000, 0)
    assert usage.for_conversation(CID)["calls"] == 2

    only_one = (tmp_path / usage.USAGE_FILE).read_text().splitlines()[0] + "\n"
    (tmp_path / usage.USAGE_FILE).write_text(only_one)

    assert usage.for_conversation(CID)["calls"] == 1


def test_for_conversation_waits_for_a_half_written_last_line(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    line = json.dumps(
        {"date": "2026-10-20", "model": "modelo-a", "input": 1_000_000, "output": 0, "action": "Chat", "conversation": CID}
    )
    path = tmp_path / usage.USAGE_FILE
    path.write_text(line[:40])  # se está escribiendo: todavía sin terminar ni salto de línea

    assert usage.for_conversation(CID)["calls"] == 0

    with path.open("a") as f:
        f.write(line[40:] + "\n")

    assert usage.for_conversation(CID)["calls"] == 1


def test_for_conversation_applies_the_current_prices(tmp_path, monkeypatch):
    use_tmp_data(tmp_path, monkeypatch)
    with usage.conversation(CID):
        usage.record("modelo-a", 1_000_000, 0)
    assert usage.for_conversation(CID)["cost"] == 1.0

    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (3.0, 4.0)})

    assert usage.for_conversation(CID)["cost"] == 3.0


def test_for_conversation_follows_a_change_of_data_folder(tmp_path, monkeypatch):
    first, second = tmp_path / "uno", tmp_path / "dos"
    first.mkdir(), second.mkdir()
    use_tmp_data(first, monkeypatch)
    with usage.conversation(CID):
        usage.record("modelo-a", 1_000_000, 0)
    assert usage.for_conversation(CID)["calls"] == 1

    monkeypatch.setattr(notes, "DATA_DIR", second)

    assert usage.for_conversation(CID)["calls"] == 0


# --- precio por proveedor ---------------------------------------------------------


def test_price_by_provider(monkeypatch):
    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (1.0, 4.0)})

    assert usage.price("modelo-a") == usage.price("gemini:modelo-a") == (1.0, 4.0)
    assert usage.price("ollama:qwen3") == (0.0, 0.0)  # local: gratis, y de precio conocido
    assert usage.price("otro") is None
    assert usage.price_known("ollama:qwen3") and usage.price_known("gemini:modelo-a")
    assert not usage.price_known("gemini:otro")


def test_stored_name_drops_only_the_gemini_prefix():
    assert usage.stored_name("gemini:modelo-a") == "modelo-a"
    assert usage.stored_name("ollama:qwen3") == "ollama:qwen3"
    assert usage.stored_name("modelo-a") == "modelo-a"


def test_a_conversation_with_a_local_model_costs_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "DATA_DIR", tmp_path.resolve())
    monkeypatch.setattr(usage, "PRICES", {"modelo-a": (1.0, 4.0)})
    with usage.conversation("20261021-120000-aaaa"):
        usage.record("ollama:qwen3", 1_000_000, 1_000_000)
        usage.record("modelo-a", 1_000_000, 0)

    spent = usage.for_conversation("20261021-120000-aaaa")

    assert spent["cost"] == pytest.approx(1.0)  # solo cuenta la de Gemini
    assert usage.summary()["total"]["unpriced"] == 0  # la de Ollama tiene precio conocido
