from epsilon.settings import checks, config


def test_a_passing_check_reports_ok_with_its_detail(monkeypatch):
    monkeypatch.setitem(checks.CHECKS, "algo", lambda: "Todo bien.")

    assert checks.run("algo") == {"ok": True, "detail": "Todo bien."}


def test_a_check_that_explains_itself_shows_its_message(monkeypatch):
    def fails():
        raise ValueError("Telegram rechazó el token.")

    monkeypatch.setitem(checks.CHECKS, "algo", fails)

    assert checks.run("algo") == {"ok": False, "detail": "Telegram rechazó el token."}


def test_an_unexpected_error_is_summarised_without_its_text(monkeypatch, capsys):
    def boom():
        raise RuntimeError("detalle interno con un secreto")

    monkeypatch.setitem(checks.CHECKS, "algo", boom)

    result = checks.run("algo")

    assert result["ok"] is False
    assert "secreto" not in result["detail"]
    # El tipo del error va a la consola, para poder investigar.
    assert "RuntimeError" in capsys.readouterr().out


def test_secrets_never_reach_the_message(monkeypatch):
    token = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"
    monkeypatch.setenv("TELEGRAM_TOKEN", token)

    def fails():
        raise ValueError(f"rechazado: {token}")

    monkeypatch.setitem(checks.CHECKS, "algo", fails)

    assert token not in checks.run("algo")["detail"]


def test_known_targets():
    assert set(checks.CHECKS) == {"gemini", "ollama", "anthropic", "telegram"}


def test_telegram_check_uses_the_saved_token_not_a_running_one(monkeypatch, tmp_path):
    seen = []
    env_file = tmp_path / "t.env"
    token = "123456789:AAH-ejemplo_de_token_de_telegram_vKjP"
    config.update({"TELEGRAM_TOKEN": token}, [], env_file)
    monkeypatch.setattr(config, "ENV_FILE", env_file)
    monkeypatch.setattr(checks.telegram, "check", lambda t: seen.append(t) or "ok")

    checks.CHECKS["telegram"]()

    assert seen == [token]


def test_the_ollama_check_names_the_default_model_when_it_is_local(monkeypatch):
    from epsilon.ai import llm

    monkeypatch.setenv("AI_MODEL", "ollama:qwen3")
    monkeypatch.setattr(llm, "check", lambda provider="gemini": "qwen3" if provider == "ollama" else "x")

    assert "qwen3" in checks.run("ollama")["detail"] and checks.run("ollama")["ok"]


def test_the_ollama_check_only_pings_when_the_default_model_is_not_local(monkeypatch):
    from epsilon.ai import llm

    monkeypatch.setenv("AI_MODEL", "gemini:x")
    monkeypatch.setattr(llm, "check", lambda provider="gemini": "3 modelos")

    assert checks.run("ollama") == {"ok": True, "detail": "Ollama responde (3 modelos)."}


def test_the_ollama_check_reports_a_server_that_is_off(monkeypatch):
    from epsilon.ai import llm

    def off(provider="gemini"):
        raise ValueError("No pude conectar con Ollama en 127.0.0.1:11434. ¿Está andando?")

    monkeypatch.setattr(llm, "check", off)

    result = checks.run("ollama")

    assert result["ok"] is False and "127.0.0.1:11434" in result["detail"]
