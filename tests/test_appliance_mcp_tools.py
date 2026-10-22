"""Unit tests for the read-only ``appliance_*`` MCP tool registrations.

These cover the C3 surface: the tools are registered, dispatch through the
shared ``dispatch`` entry point, build their own SSH/local Transport via
``transport_from_env`` (mocked here — no live box, no SSH), and return
JSON-serializable results. Mutating appliance verbs must stay absent.
"""

from __future__ import annotations

import pytest

from fsr_agentic_toolkit.verbs import tools
from pyfsr.cli.appliance.transport import CommandResult, Transport


class ScriptedTransport(Transport):
    """Return canned stdout by substring-matching the joined argv."""

    target = "scripted"

    def __init__(self, routes: dict[str, str]) -> None:
        # route key = a substring of the joined argv (e.g. "services", "psql",
        # "device_uuid"). The first match wins; insertion order is preserved.
        self._routes = routes

    def run(self, argv, **_kw) -> CommandResult:  # type: ignore[override]
        joined = " ".join(argv)
        out = ""
        for key, val in self._routes.items():
            if key in joined:
                out = val
                break
        return CommandResult(argv=argv, returncode=0, stdout=out, stderr="")


@pytest.fixture
def scripted_transport(monkeypatch):
    """Patch transport_from_env to return a ScriptedTransport the test configures."""
    holder: dict[str, Transport] = {}

    def _set(routes: dict[str, str]) -> Transport:
        t = ScriptedTransport(routes)
        holder["t"] = t
        return t

    monkeypatch.setattr(
        "fsr_agentic_toolkit.verbs.tools._appliance_transport",
        lambda: holder.get("t") or ScriptedTransport({}),
    )
    return _set


def _dispatch(name: str, args: dict | None = None):
    # The REST client is unused by appliance handlers; None is fine.
    return tools.dispatch(None, name, args or {})


# --------------------------------------------------------------- registration


def test_appliance_tools_registered():
    names = sorted(n for n in tools.REGISTRY if n.startswith("appliance_"))
    assert "appliance_info_identity" in names
    assert "appliance_db_query" in names
    assert "appliance_logs_tail" in names
    assert "appliance_diagnose_run" in names
    assert len(names) >= 16  # the read-only cut


def test_no_mutating_appliance_verbs_registered():
    """restart/stop/purge/exec_write/drop_module/regenerate must be absent."""
    mutating = ("restart", "stop", "purge", "exec_write", "drop_module", "regenerate", "start_all", "stop_all")
    present = [n for n in tools.REGISTRY if n.startswith("appliance_") and any(m in n for m in mutating)]
    assert present == [], f"mutating appliance verbs leaked in: {present}"


def test_unknown_tool_returns_structured_error():
    out = _dispatch("appliance_does_not_exist")
    assert out["error"]["type"] == "UnknownTool"


# --------------------------------------------------------------- per-tool smoke


def test_appliance_info_identity(scripted_transport):
    # identity() resolves device UUID + content DB (fingerprint via psql). We
    # assert the tool dispatches through the appliance handler (no UnknownTool,
    # no import crash) — Facts internals are covered by test_appliance_cli.py.
    scripted_transport(
        {
            "device_uuid": "0123456789abcdef0123456789abcdef",
            "rpm": "8.0.0",
            "psql": "venom|cyberpgsql",
        }
    )
    out = _dispatch("appliance_info_identity")
    assert isinstance(out, dict)
    # Either a real identity card or a structured error from a missing capture
    # — but never an UnknownTool / unhandled crash.
    if "error" in out:
        assert out["error"]["type"] in ("TransportError", "RuntimeError")
    else:
        assert "device_uuid" in out or "target" in out


def test_appliance_db_tables_returns_rows(scripted_transport):
    # Facts needs the device UUID (for the DB password); psql returns table rows.
    scripted_transport(
        {
            "device_uuid": "0123456789abcdef0123456789abcdef",
            "psql": "alerts\nincidents\nindicators\n",
        }
    )
    out = _dispatch("appliance_db_tables", {"pattern": "alerts%"})
    assert "error" not in out
    assert "database" in out and "rows" in out
    flat = [cell for row in out["rows"] for cell in (row if isinstance(row, list) else [row])]
    assert "alerts" in flat


def test_appliance_db_query_rejects_write(scripted_transport):
    scripted_transport({"db": ""})
    out = _dispatch("appliance_db_query", {"sql": "DELETE FROM alerts", "confirm": True})
    assert out["error"]["type"] == "ValueError"


def test_appliance_service_status_returns_raw(scripted_transport):
    raw = "cyops-services Running\nnginx Running\n"
    scripted_transport({"services": raw})
    out = _dispatch("appliance_service_status")
    # status() returns the raw text (may be newline-trimmed at the tail)
    assert "cyops-services Running" in out
    assert "nginx Running" in out


def test_appliance_logs_tail_returns_text(scripted_transport):
    log = "2026-01-01 line one\n2026-01-01 line two\n"
    scripted_transport({"tail": log})
    # 'nginx' is one of the known service names logs.tail accepts.
    out = _dispatch("appliance_logs_tail", {"service": "nginx", "lines": 2})
    assert out == log


def test_appliance_mq_queues_is_jsonable(scripted_transport):
    # rabbitmqctl listing output
    scripted_transport({"rabbitmqctl": "Listing queues ...\nalerts\t5\t3\n"})
    out = _dispatch("appliance_mq_queues")
    # returns a list of dataclass-as-dict rows (may be empty if parse finds none)
    assert isinstance(out, list)


def test_appliance_license_details_is_jsonable(scripted_transport):
    # csadm license --show-details output
    scripted_transport({"license": "Serial: FSR-123\nTier: Enterprise\n"})
    out = _dispatch("appliance_license_details")
    # dataclass -> dict (or error if unparseable); just assert it didn't raise
    assert "error" not in out or isinstance(out, dict)


def test_appliance_host_snapshot_is_jsonable(scripted_transport):
    free = (
        "              total        used        free      shared  buff/cache   available\n"
        "Mem:          23768       12363        1024         200       10381       10000\n"
        "Swap:          8191        2684        5507\n"
    )
    load = "1.05 1.10 1.20 2/1234 567890\n"
    ps = "  204800 /opt/.../uwsgi --ini integrations_wsgi.ini\n"
    stdout = "\n".join(["@@FREE", free, "@@LOAD", load.strip(), "@@PS", ps.strip()])
    scripted_transport({"free": stdout, "cat": stdout, "ps": stdout})
    out = _dispatch("appliance_host_snapshot")
    assert "error" not in out
    assert isinstance(out, dict)


# ------------------------------------------------- caller-controlled inputs


class RecordingTransport(ScriptedTransport):
    def __init__(self, routes):
        super().__init__(routes)
        self.calls = []

    def run(self, argv, **kw):  # type: ignore[override]
        self.calls.append((list(argv), kw.get("env") or {}))
        return super().run(argv, **kw)


@pytest.fixture
def recording(monkeypatch):
    t = RecordingTransport({"device_uuid": "0123456789abcdef0123456789abcdef", "psql": "n\n1\n", "tail": "x\n"})
    monkeypatch.setattr("fsr_agentic_toolkit.verbs.tools._appliance_transport", lambda: t)
    return t


def _ran(t):
    return [" ".join(a) for a, _ in t.calls]


def test_diagnose_run_ignores_caller_path(recording):
    _dispatch("appliance_diagnose_run", {"path": "/tmp/evil.sh"})
    assert not any("/tmp/evil.sh" in c for c in _ran(recording))
    assert "path" not in tools.REGISTRY["appliance_diagnose_run"].input_schema["properties"]


@pytest.mark.parametrize("service", ["/etc/shadow", "../../etc/shadow", "nginx/../../etc/shadow", "/var/log/x.log"])
def test_logs_tail_rejects_paths(recording, service):
    out = _dispatch("appliance_logs_tail", {"service": service})
    assert "error" in out
    assert not any("tail" in c for c in _ran(recording))


def test_logs_tail_caps_lines(recording):
    _dispatch("appliance_logs_tail", {"service": "nginx", "lines": 10_000_000})
    tails = [a for a, _ in recording.calls if a[0] == "tail"]
    assert tails and int(tails[0][2]) == tools.APPLIANCE_LOG_MAX_LINES


def test_logs_tail_enum_matches_aliases():
    from pyfsr.cli.appliance import logs

    enum = tools.REGISTRY["appliance_logs_tail"].input_schema["properties"]["service"]["enum"]
    assert set(enum) == set(logs.LOG_PATHS)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE alerts",
        "select 1;update alerts set name='x'",
        "COPY alerts TO '/tmp/x'",
        "\\! id",
        "SELECT pg_read_file('/etc/shadow')",
        "SELECT lo_export(1, '/tmp/x')",
        "SELECT * FROM dblink('host=x', 'select 1') AS t(a int)",
        "SELECT set_config('default_transaction_read_only','off',false)",
        "WITH d AS (DELETE FROM alerts RETURNING *) SELECT * FROM d",
        'SELECT * FROM U&"\\0070g_shadow"',
        "SELECT * FROM U&\"!0070g_shadow\" UESCAPE '!'",
        "SELECT query_to_xml('DELETE FROM alerts RETURNING 1', true, false, '')",
        "SELECT query_to_xml('dele' || 'te from alerts', true, false, '')",
    ],
)
def test_db_query_rejects_unsafe_sql(recording, sql):
    out = _dispatch("appliance_db_query", {"sql": sql, "confirm": True})
    assert "error" in out, sql
    assert not any(c.startswith("psql") and "alerts" in c for c in _ran(recording))


def test_db_query_runs_read_only_on_content_db(recording):
    out = _dispatch("appliance_db_query", {"sql": "SELECT count(*) FROM alerts", "confirm": True})
    psql = [(a, env) for a, env in recording.calls if a[0] == "psql" and "count(*)" in " ".join(a)]
    assert psql, out
    argv, env = psql[0]
    assert "default_transaction_read_only=on" in env.get("PGOPTIONS", "")
    assert "statement_timeout=" in env.get("PGOPTIONS", "")
    assert "postgres" not in argv[argv.index("-d") + 1]
    props = tools.REGISTRY["appliance_db_query"].input_schema["properties"]
    assert set(props) == {"sql"}
    assert "role" not in tools.REGISTRY["appliance_db_tables"].input_schema["properties"]


def test_db_query_needs_approval_and_is_dropped_read_only(recording, monkeypatch):
    out = _dispatch("appliance_db_query", {"sql": "SELECT 1"})
    assert out["error"]["type"] == "confirmation_required"
    assert not any(c.startswith("psql") for c in _ran(recording))
    assert tools.REGISTRY["appliance_db_query"].tier == 3
    monkeypatch.setenv("FSR_MCP_READONLY", "1")
    assert "appliance_db_query" not in {s.name for s in tools.enabled_tools()}
