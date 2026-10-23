"""create_record surfaces a picklist miss as a structured error (ported from pyfsr)."""


from pyfsr.api.picklists import PicklistsAPI
from pyfsr.records import RecordSet

# --- canned server payloads ------------------------------------------------

# picklist_names now carries each picklist's IRI (@id) so bulk items can map
# their listName IRI back to a name.
_NAMES = {
    "hydra:member": [
        {"name": "Severity", "@id": "/api/3/picklist_names/sev"},
        {"name": "AlertStatus", "@id": "/api/3/picklist_names/st"},
    ]
}

# The bulk /api/3/picklists listing: every item across every picklist in one
# page, each tagged with the listName IRI of its owning picklist.
_ALL_ITEMS = {
    "hydra:member": [
        {"itemValue": "High", "uuid": "sev-high", "orderIndex": 1, "listName": "/api/3/picklist_names/sev"},
        {"itemValue": "Low", "uuid": "sev-low", "orderIndex": 2, "listName": "/api/3/picklist_names/sev"},
        {"itemValue": "Open", "uuid": "st-open", "orderIndex": 1, "listName": "/api/3/picklist_names/st"},
    ]
}

# staging_model_metadatas?$relationships=true shape
_META = {
    "hydra:member": [
        {
            "type": "alerts",
            "module": "alerts",
            "attributes": [
                {"name": "name"},
                {
                    "name": "severity",
                    "dataSource": {"query": {"filters": [{"field": "listName__name", "value": "Severity"}]}},
                },
                {
                    "name": "status",
                    "dataSource": {"query": {"filters": [{"field": "listName__name", "value": "AlertStatus"}]}},
                },
            ],
        }
    ]
}


class FakeClient:
    """Routes get() by endpoint substring; counts calls per endpoint."""

    def __init__(self):
        self.get_calls = []
        self.post_calls = []
        self.put_calls = []
        self.picklists = None  # set after construction for RecordSet tests

    def get(self, endpoint, params=None, **kwargs):
        self.get_calls.append((endpoint, params))
        if endpoint.startswith("/api/3/picklist_names"):
            return _NAMES
        if endpoint.startswith("/api/3/staging_model_metadatas"):
            return _META
        if endpoint.startswith("/api/3/picklists"):
            return _ALL_ITEMS
        return {"hydra:member": []}

    def post(self, endpoint, data=None, params=None, **kwargs):
        self.post_calls.append((endpoint, data))
        return data or {}

    def put(self, endpoint, data=None, **kwargs):
        self.put_calls.append((endpoint, data))
        return data or {}


def _api():
    client = FakeClient()
    api = PicklistsAPI(client)
    client.picklists = api
    return api, client


def test_dispatch_create_record_strict_returns_structured_error():
    """The MCP create_record tool surfaces a picklist miss as a structured error
    dict (field/value/picklist/valid_values) so an agent can pick a valid value
    programmatically - not as an opaque box 400."""
    from fsr_agentic_toolkit.verbs import tools

    _, base = _api()

    class _Client:
        """Wraps the _api() FakeClient so client.records(module) returns a real
        RecordSet bound to the canned picklist payloads."""

        def __init__(self, inner):
            self._inner = inner
            self.picklists = inner.picklists

        def records(self, module, **kw):
            return RecordSet(self._inner, module)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    client = _Client(base)
    out = tools.dispatch(
        client,
        "create_record",
        {"module": "alerts", "data": {"severity": "Nope", "name": "x"}},
    )
    err = out["error"]
    assert err["type"] == "PicklistResolutionError"
    assert err["field"] == "severity"
    assert err["value"] == "Nope"
    assert err["picklist"] == "Severity"
    assert "High" in err["valid_values"]
    assert "valid" in err["message"].lower()
