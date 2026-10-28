from epsilon.routines import rules


def test_suggest_endpoint_returns_ids(client):
    reply = client.get("/api/routines/suggest", params={"text": "poné música para despertarme"})

    assert reply.status_code == 200
    assert {"musica_poner", "fecha"} <= set(reply.json()["tools"])
    assert "yt_historial" not in reply.json()["tools"]


def test_suggest_endpoint_validates_the_length(client):
    reply = client.get("/api/routines/suggest", params={"text": "x" * (rules.MAX_INSTRUCTION + 1)})

    assert reply.status_code == 422 and "xxxx" not in reply.text


def test_suggest_without_text_is_empty(client):
    assert client.get("/api/routines/suggest").json() == {"tools": []}
