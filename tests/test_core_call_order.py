from epsilon import core, tools


def test_reads_go_before_writes_and_deletes_keeping_the_models_order_among_equals():
    calls = [
        ("pc.workspace", "3"),
        ("musica.musica_playlists", ""),
        ("notas.guardar", "x"),
        ("pc.sonando", ""),
    ]

    ordered = [c[0] for c in core._reads_first(calls)]

    assert ordered[:2] == ["musica.musica_playlists", "pc.sonando"]
    assert ordered[2:] == ["pc.workspace", "notas.guardar"]


def test_unknown_calls_do_not_jump_the_queue_of_writes():
    ordered = core._reads_first([("pc.workspace", "3"), ("inventada.cosa", "")])

    assert [c[0] for c in ordered] == ["inventada.cosa", "pc.workspace"]  # no ejecuta nada, así que no mueve a la persona


def test_workspace_is_a_write_and_its_tip_says_to_go_last():
    tool = tools.route("pc.workspace")

    assert tool.ai == "write"
    assert "al final" in tool.ai_tips.lower() or "siempre al final" in tool.ai_tips.lower()
