"""A truncated list result names the offset that reads the rest, and offset reads it."""
from types import SimpleNamespace

from fsr_agentic_toolkit.verbs import tools as T


def _client(n):
    names = [f"connector-{i:03d} -- A connector with a long enough label v1.0.0" for i in range(n)]
    cn = [SimpleNamespace(name=x) for x in names]
    return SimpleNamespace(connectors=SimpleNamespace(list_configured=lambda: cn)), names


def test_every_item_is_reachable_by_following_next_offset(monkeypatch):
    monkeypatch.setattr(T, "_h_list_connectors", lambda client: {"connectors": client.items})
    monkeypatch.setitem(T.REGISTRY, "list_connectors",
                        T.REGISTRY["list_connectors"].__class__(**{**T.REGISTRY["list_connectors"].__dict__,
                                                                   "handler": T._h_list_connectors}))
    c = SimpleNamespace(items=[f"item-{i:03d} " + "x" * 60 for i in range(150)])
    seen, offset = [], 0
    while True:
        out = T.dispatch(c, "list_connectors", {"offset": offset} if offset else {})
        seen += out["connectors"]
        if "_truncated" not in out:
            break
        assert out["_truncated"]["total"] == 150
        offset = out["_truncated"]["next_offset"]
    assert seen == c.items


def test_read_tools_advertise_offset_and_write_tools_do_not():
    assert "offset" in T.REGISTRY["list_connectors"].rendered_schema()["properties"]
    assert "offset" not in T.REGISTRY["create_record"].rendered_schema()["properties"]
