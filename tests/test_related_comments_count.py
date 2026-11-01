"""count_records, link/unlink/list_related_records, add/list_comments handlers."""
from __future__ import annotations

from fsr_agentic_toolkit.verbs import tools

_FIELDS = [
    {"name": "name", "type": "text"},
    {"name": "status", "type": "picklists"},
    {"name": "severity", "type": "picklists"},
]


class _Records:
    def __init__(self, store, *, count=0, related=None, comments=None):
        self.store = store
        self._count = count
        self._related = related or []
        self._comments = comments or []

    def count(self, body=None, *, show_deleted=False):
        self.store["count_body"] = body
        return self._count

    def link(self, ref, field, related):
        self.store["link"] = (ref, field, list(related))
        return {"@id": f"/api/3/alerts/{ref}"}

    def unlink(self, ref, field, related):
        self.store["unlink"] = (ref, field, list(related))
        return {"@id": f"/api/3/alerts/{ref}"}

    def comments(self, ref, *, limit=30, page=1, orderby=None, params=None):
        self.store["comments_ref"] = ref
        return self._comments


class _Comments:
    def __init__(self, store):
        self.store = store

    def create(self, content, *, record=None, **data):
        self.store["comment_create"] = {"content": content, "record": record}
        return {"uuid": "c-new", "comment": content}


class _Client:
    def __init__(self, *, count=0, related=None, comments=None):
        self.store = {}
        self._count, self._related, self._comments = count, related, comments
        self.comments = _Comments(self.store)

    def records(self, module):
        return _Records(self.store, count=self._count, related=self._related, comments=self._comments)

    def describe_module(self, module):
        return {"module": module, "fields": _FIELDS}

    def get(self, path, params=None):
        self.store["get_path"] = path
        self.store["get_params"] = params
        return {"hydra:member": self._related or [], "hydra:totalItems": len(self._related or [])}


# -- count_records -------------------------------------------------------------

def test_count_lowers_predicate_and_returns_total():
    c = _Client(count=7)
    out = tools.dispatch(c, "count_records", {"module": "alerts", "where": "status=Open and severity=Critical"})
    body = c.store["count_body"]
    assert {"field": "status.itemValue", "operator": "eq", "value": "Open"} in body["filters"]
    assert out == {"module": "alerts", "where": "status=Open and severity=Critical", "count": 7}


def test_count_empty_where_counts_the_whole_module():
    c = _Client(count=42)
    out = tools.dispatch(c, "count_records", {"module": "alerts"})
    assert c.store["count_body"] is None  # no filter body -> count all
    assert out["count"] == 42 and out["where"] is None


# -- link / unlink -------------------------------------------------------------

def test_link_qualifies_a_bare_uuid_with_related_module():
    c = _Client()
    out = tools.dispatch(c, "link_records", {
        "module": "alerts", "ref": "a1", "field": "indicators",
        "related": "i1", "related_module": "indicators",
    })
    assert c.store["link"] == ("a1", "indicators", ["indicators:i1"])
    assert out["count"] == 1


def test_link_accepts_iris_and_module_uuid_and_a_list():
    c = _Client()
    tools.dispatch(c, "link_records", {
        "module": "alerts", "ref": "a1", "field": "assets",
        "related": ["assets:x", "/api/3/assets/y"],
    })
    assert c.store["link"] == ("a1", "assets", ["assets:x", "/api/3/assets/y"])


def test_link_refuses_a_bare_uuid_without_related_module():
    c = _Client()
    out = tools.dispatch(c, "link_records", {
        "module": "alerts", "ref": "a1", "field": "indicators", "related": "i1",
    })
    assert "bare uuid" in out["error"]["message"]
    assert "link" not in c.store


def test_unlink_passes_through():
    c = _Client()
    tools.dispatch(c, "unlink_records", {
        "module": "alerts", "ref": "a1", "field": "indicators",
        "related": "i1", "related_module": "indicators",
    })
    assert c.store["unlink"] == ("a1", "indicators", ["indicators:i1"])


# -- list_related_records ------------------------------------------------------

def test_list_related_hits_the_relationship_path():
    related = [{"@id": "/api/3/indicators/i1", "uuid": "i1", "value": "1.2.3.4"}]
    c = _Client(related=related)
    out = tools.dispatch(c, "list_related_records", {"module": "alerts", "ref": "a1", "field": "indicators"})
    assert c.store["get_path"] == "/api/3/alerts/a1/indicators"
    assert out["count"] == 1 and out["field"] == "indicators"


# -- comments ------------------------------------------------------------------

def test_add_comment_links_to_the_record_iri():
    c = _Client()
    out = tools.dispatch(c, "add_comment", {"module": "incidents", "ref": "x1", "text": "Escalating."})
    assert c.store["comment_create"] == {"content": "Escalating.", "record": "/api/3/incidents/x1"}
    assert out["comment_id"] == "c-new"


def test_list_comments_compacts_each_to_text_by_at():
    comments = [{"comment": "Looks bad", "createUser": {"name": "CS Admin"}, "createDate": "2026-10-30"}]
    c = _Client(comments=comments)
    out = tools.dispatch(c, "list_comments", {"module": "alerts", "ref": "a1"})
    assert out["comments"] == [{"text": "Looks bad", "by": "CS Admin", "at": "2026-10-30"}]
    assert out["count"] == 1
