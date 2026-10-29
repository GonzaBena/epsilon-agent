from epsilon.routines import notify


def test_it_notifies_the_desktop_and_every_sink(monkeypatch):
    seen = []
    monkeypatch.setattr(notify, "_notify_send", lambda text: seen.append(("escritorio", text)))
    notify.set_sink("telegram", lambda text: seen.append(("telegram", text)))

    assert notify.send("hola\n  mundo") == []
    assert seen == [("escritorio", "hola mundo"), ("telegram", "hola mundo")]


def test_a_failing_medium_does_not_stop_the_others_and_is_reported(monkeypatch):
    seen = []

    def broken(text):
        raise RuntimeError("sin bus")

    monkeypatch.setattr(notify, "_notify_send", broken)
    notify.set_sink("telegram", seen.append)

    assert notify.send("hola") == ["escritorio: sin bus"]
    assert seen == ["hola"]


def test_a_removed_sink_is_not_called(monkeypatch):
    seen = []
    monkeypatch.setattr(notify, "_notify_send", lambda text: None)
    notify.set_sink("telegram", seen.append)
    notify.remove_sink("telegram")
    notify.remove_sink("telegram")  # dos veces no rompe

    notify.send("hola")

    assert seen == []


def test_the_text_is_redacted_and_cut(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "_notify_send", sent.append)
    monkeypatch.setenv("GEMINI_API_KEY", "k" * 30)

    notify.send("clave " + "k" * 30 + " y " + "x" * 1000)

    assert "k" * 30 not in sent[0] and len(sent[0]) == notify.MAX_CHARS


def test_the_real_notifier_passes_the_text_after_a_double_dash(monkeypatch):
    calls = []
    monkeypatch.undo()  # vuelve el `_notify_send` real (el conftest lo bloquea)
    monkeypatch.setattr(notify.subprocess, "run", lambda argv, **kw: calls.append(argv))

    notify._notify_send("--version")

    assert calls == [["notify-send", "-a", "Epsilon", "--", "Epsilon", "--version"]]


def test_desktop_only_notifies_the_desktop_and_never_the_sinks(monkeypatch):
    seen = []
    monkeypatch.setattr(notify, "_notify_send", lambda text: seen.append(("escritorio", text)))
    notify.set_sink("telegram", lambda text: seen.append(("telegram", text)))

    assert notify.desktop("Escuchando…") == ""
    assert seen == [("escritorio", "Escuchando…")]


def test_desktop_reports_a_failure_instead_of_raising(monkeypatch):
    def broken(text):
        raise RuntimeError("sin bus")

    monkeypatch.setattr(notify, "_notify_send", broken)

    assert notify.desktop("hola") == "sin bus"
