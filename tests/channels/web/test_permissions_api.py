from epsilon import tools
from epsilon.ai import permissions


def current(client):
    return {t["id"]: t["state"] for t in client.get("/api/permissions").json()["tools"]}


def test_get_lists_every_tool_with_defaults(client):
    data = client.get("/api/permissions").json()
    assert data["states"] == ["allow", "ask", "deny"]
    assert [t["id"] for t in data["tools"]] == [tools.ai_id(t) for t in tools.ai_tools()]
    assert len(data["tools"]) == 32
    for item, tool in zip(data["tools"], tools.ai_tools()):
        assert item["label"] == tool.label
        assert item["kind"] == tool.ai
        assert item["desc"] == (tool.ai_help or tool.help or tool.panel or "")
        assert item["default"] == permissions.default_state(tool)
        assert item["state"] == item["default"]


def test_put_saves_and_get_returns_it(client):
    states = current(client)
    target = next(iter(states))
    states[target] = "deny"
    reply = client.put("/api/permissions", json={"states": states})
    assert reply.status_code == 200
    assert {t["id"]: t["state"] for t in reply.json()["tools"]} == states
    assert current(client)[target] == "deny"


def test_put_rejects_bad_state_unknown_tool_and_missing_tool(client):
    before = current(client)
    first = next(iter(before))
    bad_state = {**before, first: "maybe"}
    unknown = {**before, "no_existe": "allow"}
    missing = {k: v for k, v in before.items() if k != first}
    for states in (bad_state, unknown, missing):
        reply = client.put("/api/permissions", json={"states": states})
        assert reply.status_code == 422
        assert reply.json()["detail"]
        assert current(client) == before


def test_put_with_foreign_origin_is_403(client):
    before = current(client)
    first = next(iter(before))
    reply = client.put(
        "/api/permissions",
        json={"states": {**before, first: "deny"}},
        headers={"Origin": "http://evil.com"},
    )
    assert reply.status_code == 403
    assert current(client) == before


def test_the_response_never_contains_the_data_dir_path(client, tmp_path):
    states = current(client)
    for reply in (
        client.get("/api/permissions"),
        client.put("/api/permissions", json={"states": states}),
        client.put("/api/permissions", json={"states": {**states, "x": "allow"}}),
    ):
        assert str(tmp_path) not in reply.text
