"""setup_connector: configure a connector without a secret ever passing through the model.

The property under test is not only that it works, but that no secret VALUE appears anywhere
in what comes back: not in findings, evidence, unknowns, data, or an error the service
echoes. The fakes shape a connector whose schema marks api_key as a password.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pyfsr.models._integration import ConfigSchemaField

from fsr_agentic_toolkit.verbs import tools as T
from fsr_agentic_toolkit.workflows import MemoryResultStore, set_result_store
from fsr_agentic_toolkit.workflows import connector_setup as CS

SECRET = "s3cr3t-VALUE-9f2c"
# The real model, not dicts: pyfsr's config_schema returns these, and treating them as dicts
# silently emptied the schema on a live box while a dict-based fake kept the tests green.
SCHEMA = [
    ConfigSchemaField(name="server", type="text", title="Server URL", required=True, value=None),
    ConfigSchemaField(name="api_key", type="password", title="API Key", required=True, value=None),
    ConfigSchemaField(name="verify_ssl", type="checkbox", title="Verify SSL", required=False, value=True),
]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    set_result_store(MemoryResultStore())
    monkeypatch.setenv("FSR_SECRET_VIRUSTOTAL_API_KEY", SECRET)


class FakeConnectors:
    def __init__(self, *, installed=("virustotal",), existing=(), health=("Available", "ok"),
                 save_error=None):
        self.installed = list(installed)
        self.existing = list(existing)
        self.health, self.save_error = health, save_error
        self.saved, self.installs = [], []

    def list_configured(self, refresh=False):
        return [SimpleNamespace(name=n) for n in self.installed]

    def install(self, name, version=None, wait=True):
        self.installs.append(name)
        self.installed.append(name)

    def config_schema(self, connector, version=None):
        return SCHEMA

    def configurations(self, connector):
        return [SimpleNamespace(name=n) for n in self.existing]

    def upsert_configuration(self, connector, config, *, name, version=None, default=False):
        if self.save_error:
            raise RuntimeError(self.save_error)
        self.saved.append((connector, dict(config), name, default))

    def healthcheck(self, connector, config=None):
        return SimpleNamespace(status=self.health[0], message=self.health[1])


def _go(monkeypatch, fake, **kw):
    monkeypatch.setattr(CS._shared, "_live_client", lambda: SimpleNamespace(connectors=fake))
    kw.setdefault("fields", {"server": "www.virustotal.com"})
    kw.setdefault("secrets", {"api_key": {"env": "FSR_SECRET_VIRUSTOTAL_API_KEY"}})
    return CS.setup_connector("virustotal", **kw)


def _everything(result) -> str:
    return json.dumps(result.to_tool(), default=str)


def test_happy_path_saves_the_real_value_but_never_reports_it(monkeypatch):
    fake = FakeConnectors()
    r = _go(monkeypatch, fake, config_name="Lab")
    assert r.status == "answered"
    assert fake.saved == [("virustotal", {"server": "www.virustotal.com", "api_key": SECRET}, "Lab", False)]
    assert SECRET not in _everything(r)
    assert r.data["secrets"] == {"api_key": "from env FSR_SECRET_VIRUSTOTAL_API_KEY"}
    assert any("reachable" in f.claim for f in r.findings)


def test_a_literal_secret_is_refused_and_not_echoed(monkeypatch):
    fake = FakeConnectors()
    r = _go(monkeypatch, fake, fields={"server": "x", "api_key": "literal-LEAK-123"}, secrets={})
    assert r.status == "blocked" and not fake.saved
    assert "literal-LEAK-123" not in _everything(r)
    assert any("secret field" in u and "environment variable" in u for u in r.unknowns)


def test_a_credential_shaped_name_is_a_secret_even_if_the_schema_does_not_say_so(monkeypatch):
    fake = FakeConnectors()
    r = _go(monkeypatch, fake, fields={"server": "x", "client_secret": "LEAK-456"}, secrets={})
    assert r.status == "blocked" and "LEAK-456" not in _everything(r)


def test_a_secret_must_be_a_reference_not_a_value(monkeypatch):
    r = _go(monkeypatch, FakeConnectors(), secrets={"api_key": "plain-LEAK-789"})
    assert r.status == "blocked" and "plain-LEAK-789" not in _everything(r)
    assert any("reference" in f.claim for f in r.findings)


def test_only_fsr_secret_variables_resolve(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "host-LEAK-000")
    r = _go(monkeypatch, FakeConnectors(), secrets={"api_key": {"env": "AWS_SECRET_ACCESS_KEY"}})
    assert r.status == "blocked" and "host-LEAK-000" not in _everything(r)
    assert any("FSR_SECRET_" in f.claim for f in r.findings)


def test_an_unset_variable_says_where_to_set_it(monkeypatch):
    monkeypatch.delenv("FSR_SECRET_VIRUSTOTAL_API_KEY")
    r = _go(monkeypatch, FakeConnectors())
    assert r.status == "blocked"
    assert any("not set in the environment" in f.claim and "restart" in f.claim for f in r.findings)


def test_file_references_stay_inside_the_secrets_directory(monkeypatch, tmp_path):
    (tmp_path / "virustotal").mkdir()
    (tmp_path / "virustotal" / "key").write_text(SECRET + "\n")
    outside = tmp_path.parent / "outside-secret"
    outside.write_text("outside-LEAK")
    monkeypatch.setenv("FSR_SECRETS_DIR", str(tmp_path))
    fake = FakeConnectors()
    ok = _go(monkeypatch, fake, secrets={"api_key": {"file": "virustotal/key"}})
    assert ok.status == "answered" and fake.saved[0][1]["api_key"] == SECRET  # newline stripped
    bad = _go(monkeypatch, FakeConnectors(), secrets={"api_key": {"file": "../outside-secret"}})
    assert bad.status == "blocked" and "outside-LEAK" not in _everything(bad)


def test_file_references_are_off_without_the_directory(monkeypatch):
    monkeypatch.delenv("FSR_SECRETS_DIR", raising=False)
    r = _go(monkeypatch, FakeConnectors(), secrets={"api_key": {"file": "virustotal/key"}})
    assert r.status == "blocked" and any("FSR_SECRETS_DIR" in f.claim for f in r.findings)


def test_a_service_error_that_echoes_the_secret_is_scrubbed(monkeypatch):
    fake = FakeConnectors(health=("Disconnected", f"401 for key {SECRET}"))
    r = _go(monkeypatch, fake)
    assert r.status == "partial" and SECRET not in _everything(r)
    assert "***" in r.data["health"]["message"]


def test_a_failed_save_that_echoes_the_secret_is_scrubbed(monkeypatch):
    r = _go(monkeypatch, FakeConnectors(save_error=f"bad body api_key={SECRET}"))
    assert r.status == "blocked" and SECRET not in _everything(r)


def test_check_only_resolves_but_writes_nothing(monkeypatch):
    fake = FakeConnectors()
    r = _go(monkeypatch, fake, check_only=True)
    assert r.status == "answered" and not fake.saved and SECRET not in _everything(r)
    assert any("1 secret(s) resolved" in f.claim for f in r.findings)


def test_missing_required_fields_say_how_to_pass_each(monkeypatch):
    r = _go(monkeypatch, FakeConnectors(), fields={}, secrets={})
    claims = " ".join(f.claim for f in r.findings)
    assert "'server'" in claims and "fields={'server'" in claims
    assert "'api_key'" in claims and "secrets={'api_key'" in claims


def test_unknown_field_gets_a_did_you_mean(monkeypatch):
    r = _go(monkeypatch, FakeConnectors(), fields={"server": "x", "verify_sl": True})
    assert r.status == "blocked" and any("did you mean 'verify_ssl'" in f.claim for f in r.findings)


def test_not_installed_asks_before_installing(monkeypatch):
    fake = FakeConnectors(installed=())
    r = _go(monkeypatch, fake)
    assert r.status == "blocked" and not fake.installs
    assert any("install=true" in n.action for n in r.next)
    r1 = _go(monkeypatch, fake, install=True)  # no confirm: code would run on the box unasked
    assert r1.status == "blocked" and not fake.installs
    assert any("confirm_install=true" in n.action for n in r1.next)
    r2 = _go(monkeypatch, fake, install=True, confirm_install=True)
    assert fake.installs == ["virustotal"] and r2.status == "answered"


def test_an_existing_configuration_is_called_out_as_overwritten(monkeypatch):
    r = _go(monkeypatch, FakeConnectors(existing=["Default"]))
    assert any("overwritten" in f.claim for f in r.findings)


def test_spec_is_a_tier2_write_and_tells_the_model_not_to_pass_secrets():
    spec = T.REGISTRY["setup_connector"]
    assert spec.workflow and spec.mutating and not spec.destructive
    assert "SECRETS NEVER GO THROUGH YOU" in spec.description


def test_an_unreadable_schema_is_a_read_failure_not_unknown_fields(monkeypatch):
    fake = FakeConnectors()
    fake.config_schema = lambda connector, version=None: []
    r = _go(monkeypatch, fake)
    assert r.status == "blocked" and not fake.saved
    assert any("could not read" in u and "nothing was written" in u for u in r.unknowns)
    assert not any("has no field" in f.claim for f in r.findings)


def test_a_secret_is_bound_to_its_own_connector(monkeypatch):
    monkeypatch.setenv("FSR_SECRET_JIRA_TOKEN", "jira-LEAK")
    fake = FakeConnectors()
    r = _go(monkeypatch, fake, secrets={"api_key": {"env": "FSR_SECRET_JIRA_TOKEN"}})
    assert r.status == "blocked" and not fake.saved
    assert any("not bound to 'virustotal'" in f.claim for f in r.findings)
    assert "jira-LEAK" not in _everything(r)


def test_a_file_secret_must_sit_under_its_connector(monkeypatch, tmp_path):
    (tmp_path / "other").write_text("other-LEAK")
    monkeypatch.setenv("FSR_SECRETS_DIR", str(tmp_path))
    r = _go(monkeypatch, FakeConnectors(), secrets={"api_key": {"file": "other"}})
    assert r.status == "blocked" and "other-LEAK" not in _everything(r)


def test_allowed_hosts_stop_a_secret_going_to_another_server(monkeypatch):
    monkeypatch.setenv("FSR_SECRET_ALLOWED_HOSTS", "virustotal.com")
    fake = FakeConnectors()
    bad = _go(monkeypatch, fake, fields={"server": "https://evil.example.net/v3"})
    assert bad.status == "blocked" and not fake.saved
    assert any("FSR_SECRET_ALLOWED_HOSTS" in f.claim for f in bad.findings)
    ok = _go(monkeypatch, fake, fields={"server": "https://www.virustotal.com/api"})
    assert ok.status == "answered"


def test_the_destination_host_is_reported(monkeypatch):
    r = _go(monkeypatch, FakeConnectors(), check_only=True)
    assert r.data["destination_hosts"] == ["www.virustotal.com"]
    assert any("www.virustotal.com" in f.claim for f in r.findings)
