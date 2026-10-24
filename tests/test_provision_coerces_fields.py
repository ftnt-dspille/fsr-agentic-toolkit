"""provision_module must honour the simple field shape agents pass
(``{name, type, required, picklist}``) by running it through the same
coercion the create_module tool uses, instead of handing the raw dicts to
create_module. A raw dict silently drops ``required``/``picklist`` and the
bare display ``type`` fails at publish, which is what made the model
delete-and-reprovision. All offline."""

from __future__ import annotations

import pytest

from fsr_agentic_toolkit.workflows import provision


class _Stop(Exception):
    pass


class _FakeAdmin:
    def __init__(self):
        self.created_fields = None

    def is_published(self, module):
        return False

    def get_staging(self, module):
        return None

    def create_module(self, module, *, label=None, plural=None, fields=None):
        self.created_fields = fields
        # stop the run once we have captured the create call
        raise _Stop()


class _FakeClient:
    def __init__(self, admin):
        self.modules_admin = admin
        self.roles = None


def test_provision_coerces_fields_before_create(monkeypatch):
    admin = _FakeAdmin()
    monkeypatch.setattr(
        provision._shared, "_live_client", lambda: _FakeClient(admin)
    )

    with pytest.raises(_Stop):
        provision.provision_module(
            "agentlab_assets",
            fields=[{"name": "hostname", "type": "text", "required": True}],
        )

    assert admin.created_fields, "create_module got no fields"
    f = admin.created_fields[0]
    # A raw passthrough leaves a plain dict with a bare display "type" and a
    # top-level "required" (dropped at publish). Coercion yields real
    # AttributeMetadata: storage type, a form type, and required under
    # validation. Discriminate on that.
    assert not isinstance(f, dict), f"fields handed to create_module raw: {f}"
    assert f.name == "hostname"
    assert f.type == "string" and f.form_type == "text"
    assert f.validation.required is True, f"required dropped: {f}"
