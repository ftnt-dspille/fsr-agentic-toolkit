"""setup_connector: install if asked, configure, and healthcheck a connector, with no secret
ever passing through the model.

The model supplies the connector, a configuration name, the plain (non-secret) field values,
and a *reference* for each secret field. The reference is resolved here, in the server's own
process, and the value goes straight to FortiSOAR:

- ``{"env": "FSR_SECRET_<CONNECTOR>_<FIELD>"}`` - a variable the operator set where the server
  runs. Only names with the ``FSR_SECRET_`` prefix resolve, so the model cannot ask for an
  arbitrary variable of the host (cloud keys, tokens) to be copied into a configuration.
- ``{"file": "<connector>/<name>"}`` - a file inside ``$FSR_SECRETS_DIR`` (trailing newline
  stripped). A path that leaves that directory is refused; with the variable unset, file
  references are off.

A secret is bound to the connector it was set up for: the variable name must carry the
connector (``FSR_SECRET_VIRUSTOTAL_API_KEY`` for ``virustotal``), and a file must sit under a
folder or name starting with the connector. A reference for another connector is refused, so a
model cannot point one service's key at a different connector. The operator can also pin where
secrets may be sent with ``FSR_SECRET_ALLOWED_HOSTS`` (comma-separated host names, suffix
match): any server/url/host field must then name an allowed host. The destination host is
always reported, and shown in the approval prompt through the field values.

The other direction is closed too: a literal value for a secret field is refused without
echoing it, and every string in the result (findings, evidence, errors, the healthcheck
message) is scrubbed of the values this call resolved. Secret fields are those the connector's
own schema types as ``password``, plus any field whose name reads like a credential.
"""
from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from typing import Any

from fsr_playbooks.mcp_server import _shared

from .result import WorkflowResult, WorkflowRun

_ENV_NAME = re.compile(r"^FSR_SECRET_[A-Z0-9_]+$")
_SECRETISH = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential)", re.IGNORECASE)
_MASK = "***"


class SecretError(ValueError):
    """A secret reference could not be used. The message never contains a secret value."""


class _Redactor:
    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, value: str) -> None:
        if value:
            self._values.add(value)

    def __call__(self, text: Any) -> str:
        out = str(text)
        for v in sorted(self._values, key=len, reverse=True):
            out = out.replace(v, _MASK)
        return out


def _slug(connector: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", connector).strip("_")


_HOST_FIELD = re.compile(r"(server|url|host|address|endpoint|domain|base)", re.IGNORECASE)


def _host_of(value: Any) -> str:
    text = str(value).strip()
    text = re.sub(r"^[a-z][a-z0-9+.-]*://", "", text, flags=re.IGNORECASE)
    return re.split(r"[/:?#]", text.rsplit("@", 1)[-1], maxsplit=1)[0].lower()


def _allowed_hosts() -> list[str]:
    return [h.strip().lower() for h in os.environ.get("FSR_SECRET_ALLOWED_HOSTS", "").split(",") if h.strip()]


def _destinations(fields: dict, schema: dict[str, dict]) -> dict[str, str]:
    """Field name -> host for every field that says where the connector will talk to."""
    return {k: h for k, v in fields.items()
            if isinstance(v, str) and (_HOST_FIELD.search(k) or (schema.get(k) or {}).get("type") == "url")
            for h in [_host_of(v)] if h}


def _resolve_secret(ref: Any, connector: str = "") -> tuple[str, str]:
    """``(value, where_it_came_from)`` for a secret reference. Raises SecretError.
    With ``connector``, the reference must belong to it (see the module docstring)."""
    if not isinstance(ref, dict) or len(ref) != 1:
        raise SecretError('a secret is given as a reference: {"env": "FSR_SECRET_<CONNECTOR>_<FIELD>"} '
                          'or {"file": "<name inside $FSR_SECRETS_DIR>"}, never as a value')
    (kind, target), = ref.items()
    if kind == "env":
        if not isinstance(target, str) or not _ENV_NAME.fullmatch(target):
            raise SecretError("an env reference must name a variable starting with FSR_SECRET_ "
                              "(upper-case letters, digits, underscores)")
        if connector and not target.startswith(f"FSR_SECRET_{_slug(connector).upper()}_"):
            raise SecretError(f"{target} is not bound to {connector!r}: name it "
                              f"FSR_SECRET_{_slug(connector).upper()}_<FIELD> so the key can only "
                              "be used for that connector")
        value = os.environ.get(target, "")
        if not value:
            raise SecretError(f"{target} is not set in the environment of the server process; "
                              "add it to the `env` block of the MCP server config and restart it")
        return value, f"env {target}"
    if kind == "file":
        root = os.environ.get("FSR_SECRETS_DIR")
        if not root:
            raise SecretError("file references are off: set FSR_SECRETS_DIR to a directory of "
                              "secret files in the server's environment, or use an env reference")
        base = Path(root).resolve()
        path = (base / str(target)).resolve()
        if base != path and base not in path.parents:
            raise SecretError("a file reference must stay inside $FSR_SECRETS_DIR")
        rel = path.relative_to(base).as_posix().lower()
        if connector and not rel.startswith(_slug(connector).lower()):
            raise SecretError(f"{str(target)!r} is not bound to {connector!r}: put it in a folder "
                              f"or file named for the connector (e.g. {connector}/<field>)")
        if not path.is_file():
            raise SecretError(f"no secret file {str(target)!r} inside $FSR_SECRETS_DIR")
        value = path.read_text(encoding="utf-8").rstrip("\r\n")
        if not value:
            raise SecretError(f"secret file {str(target)!r} is empty")
        return value, f"file {target}"
    raise SecretError('unknown secret reference kind; use "env" or "file"')


def _is_secret_field(name: str, schema: dict[str, dict]) -> bool:
    spec = schema.get(name)
    return (spec is not None and spec.get("type") == "password") or bool(_SECRETISH.search(name))


def setup_connector(connector: str, *, config_name: str = "Default",
                    fields: dict | None = None, secrets: dict | None = None,
                    version: str = "", make_default: bool = False,
                    install: bool = False, check_only: bool = False,
                    healthcheck: bool = True, confirm_install: bool = False) -> WorkflowResult:
    run = WorkflowRun("setup_connector")
    redact = _Redactor()
    fields, secrets = dict(fields or {}), dict(secrets or {})
    client = _shared._live_client()
    if client is None:
        run.unknown("no FortiSOAR instance is configured")
        return run.result("blocked")
    try:
        return _setup(run, redact, client, connector, config_name, fields, secrets,
                      version, make_default, install, check_only, healthcheck, confirm_install)
    except Exception as exc:  # noqa: BLE001 - never raise; never leak a value in the text
        run.unknown(redact(f"{type(exc).__name__}: {exc}"))
        return run.result("blocked")


def _setup(run: WorkflowRun, redact: _Redactor, client: Any, connector: str, config_name: str,
           fields: dict, secrets: dict, version: str, make_default: bool, install: bool,
           check_only: bool, healthcheck: bool, confirm_install: bool = False) -> WorkflowResult:
    api = client.connectors

    # 1. installed?
    installed = {c.name: c for c in api.list_configured()}
    if connector not in installed:
        near = difflib.get_close_matches(connector, list(installed), n=3, cutoff=0.5)
        if not install:
            run.unknown(f"connector {connector!r} is not installed"
                        + (f" (did you mean {near}?)" if near else ""))
            run.next(f"setup_connector(connector='{connector}', install=true, ...)",
                     "install it from Content Hub as part of this call", 2)
            return run.result("blocked")
        if check_only:
            run.unknown(f"{connector!r} is not installed; check_only did not install it")
            return run.result("partial")
        if not confirm_install:
            # installing puts executable code on the appliance: confirm-gated like install_connector
            run.unknown(f"installing {connector!r} runs third-party code on the box, so it needs "
                        "confirm_install=true. Tell the user what will be installed, then call again.")
            run.next(f"setup_connector(connector='{connector}', install=true, confirm_install=true, ...)",
                     "install it after the user agrees", 2)
            return run.result("blocked")
        try:
            api.install(connector, version=version or None, wait=True)
        except Exception as exc:  # noqa: BLE001
            run.unknown(redact(f"install failed: {exc}"))
            run.next("list_connectors()", "installed connector names on this box", 1)
            return run.result("blocked")
        iev = run.evidence("op", f"install {connector}", "installed from Content Hub")
        run.finding(f"installed {connector!r} from Content Hub", [iev], "high")
        api.list_configured(refresh=True)

    # 2. what the connector asks for
    def read_schema() -> dict[str, dict]:
        # pyfsr returns ConfigSchemaField models, not dicts; keep dict access either way
        rows = [f if isinstance(f, dict) else f.model_dump()
                for f in api.config_schema(connector, version=version or None)]
        return {f["name"]: f for f in rows if f.get("name")}

    # The library answers [] when the definition read fails (a busy box), which would read as
    # "this connector has no such field". Retry once, then say it was a read failure.
    schema = read_schema() or read_schema()
    if not schema:
        run.unknown(f"could not read {connector!r}'s configuration schema (the box may be busy, "
                    "or that version is not installed); nothing was written")
        run.next(f"setup_connector(connector='{connector}', ...)", "retry in a moment", 2)
        return run.result("blocked")
    sev = run.evidence("schema", f"{connector} configuration",
                       ", ".join(f"{n}:{f.get('type')}{'*' if f.get('required') else ''}"
                                 for n, f in schema.items()))

    # 3. refuse literal secrets before anything else, without echoing them
    leaked = [k for k in fields if _is_secret_field(k, schema)]
    for k in leaked:
        run.unknown(f"{k!r} is a secret field: do not pass its value. Set it in an environment "
                    f"variable where the MCP server runs (name it FSR_SECRET_<CONNECTOR>_<FIELD>) and call again "
                    f"with secrets={{{k!r}: {{'env': 'FSR_SECRET_<CONNECTOR>_<FIELD>'}}}}")
    if leaked:
        run.next("setup_connector(..., secrets={<field>: {'env': 'FSR_SECRET_<CONNECTOR>_<FIELD>'}})",
                 "give the reference, not the value", 2)
        return run.result("blocked")

    # 4. unknown keys (did-you-mean), then resolve the secret references
    problems = []
    for k in [*fields, *secrets]:
        if k not in schema:
            near = difflib.get_close_matches(k, list(schema), n=1, cutoff=0.5)
            problems.append(f"{connector!r} has no field {k!r}"
                            + (f" - did you mean {near[0]!r}?" if near else
                               f" (fields: {', '.join(schema)})"))
    dest = _destinations(fields, schema)
    allowed = _allowed_hosts()
    if secrets and allowed:
        for k, h in dest.items():
            if not any(h == a or h.endswith("." + a) for a in allowed):
                problems.append(f"{k!r} points at {h!r}, which is not in FSR_SECRET_ALLOWED_HOSTS; "
                                "secrets are only sent to hosts the operator listed")
    sources: dict[str, str] = {}
    resolved: dict[str, str] = {}
    for k, ref in secrets.items():
        if k not in schema:
            continue
        try:
            value, where = _resolve_secret(ref, connector)
        except SecretError as exc:
            problems.append(f"secret {k!r}: {exc}")
            continue
        redact.add(value)
        resolved[k], sources[k] = value, where
    # a non-secret field handed a secret reference, or a secret field given a plain value, is
    # a mistake worth naming rather than silently accepting
    for k in secrets:
        if k in schema and not _is_secret_field(k, schema):
            problems.append(f"{k!r} is not a secret field; pass it in fields= as a plain value")

    # 5. required fields with no default
    given = {*fields, *resolved}
    missing = [n for n, f in schema.items()
               if f.get("required") and n not in given and f.get("value") in (None, "")]
    for n in missing:
        how = "secrets={'" + n + "': {'env': 'FSR_SECRET_<CONNECTOR>_<FIELD>'}}" if _is_secret_field(n, schema) \
            else f"fields={{'{n}': <value>}}"
        problems.append(f"required field {n!r} ({schema[n].get('title') or n}) is missing - pass {how}")
    if problems:
        for p in problems:
            run.finding(redact(f"cannot configure yet: {p}"), [sev], "high")
        run.next("setup_connector(...)", "call again once those are fixed", 2)
        return run.result("blocked")

    existing = {c.name for c in api.configurations(connector)}
    action = "update" if config_name in existing else "create"
    run.data.update({"connector": connector, "config_name": config_name, "action": action,
                     "destination_hosts": sorted(set(dest.values())), "fields": fields, "secrets": {k: f"from {w}" for k, w in sources.items()}})
    if check_only:
        run.finding(f"ready to {action} configuration {config_name!r} on {connector!r}; "
                    f"{len(sources)} secret(s) resolved ({', '.join(sources.values()) or 'none needed'})"
                    + (f"; it will talk to {', '.join(sorted(set(dest.values())))}" if dest and sources else ""),
                    [sev], "high")
        run.next("setup_connector(..., check_only=false)", "write it and healthcheck", 2)
        return run.result()

    # 6. write: the values go to the box, not back to the caller
    config = {**fields, **resolved}
    try:
        api.upsert_configuration(connector, config, name=config_name,
                                 version=version or None, default=make_default)
    except Exception as exc:  # noqa: BLE001
        run.evidence("op", f"{action} configuration {config_name}", redact(exc), ok=False)
        run.unknown(redact(f"saving the configuration failed: {exc}"))
        run.next(f"validate_connector_config(connector='{connector}', ...)",
                 "check the field values against the connector's schema", 1)
        return run.result("blocked")
    wev = run.evidence("op", f"{action} configuration {config_name}",
                       f"{len(fields)} field(s) and {len(sources)} secret(s) written")
    run.finding(f"{action}d configuration {config_name!r} on {connector!r}"
                + (" (an existing configuration with that name was overwritten)"
                   if action == "update" else ""), [wev], "high")

    # 7. prove it reaches the service
    if healthcheck:
        try:
            h = api.healthcheck(connector, config=config_name)
            status, message = str(h.status), redact(getattr(h, "message", "") or "")
        except Exception as exc:  # noqa: BLE001
            status, message = "error", redact(exc)
        hev = run.evidence("op", f"healthcheck {connector}/{config_name}",
                           f"{status}: {message}"[:300])
        run.data["health"] = {"status": status, "message": message}
        if status == "Available":
            run.finding(f"{connector!r} is reachable with that configuration", [hev], "high")
            run.next(f"list_connector_operations(connector='{connector}')",
                     "the operations it offers, read live from this box", 1)
            return run.result()
        run.finding(f"saved, but the healthcheck says {status}: {message}", [hev], "high")
        run.unknown("the configuration exists but the service did not accept it; check the "
                    "server URL, the secret's value at its source, and verify_ssl")
        run.next(f"healthcheck_connector(connector='{connector}')", "retry after fixing", 1)
        return run.result("partial")
    return run.result()
