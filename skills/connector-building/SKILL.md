---
name: connector-building
description: >-
  Build, fix, or review a FortiSOAR ConnectorsV2 connector (info.json +
  operations.py + connector.py). Use when creating a new integration from
  scratch, adding or repairing an operation, resolving why a connector won't
  import or an action fails, or checking a connector against FortiSOAR's
  conventions before deploying it.
---

# Building a FortiSOAR connector

A connector is a directory with three files: `info.json` (metadata, operations,
parameters, config fields), `operations.py` (one handler per operation plus a
dispatch dict), and `connector.py` (a `Connector` subclass with `execute()` and
`check_health()`).

## One workflow runs the loop

Reach for **`build_connector`** first. In one call it scaffolds a gate-clean
skeleton (when you pass `scaffold={ops:[...]}` and the directory is empty) and
then runs the static domain gates — info.json shape, dispatch coverage, imports,
class structure, and code safety (missing error handling, disabled TLS verification). It returns each blocking issue and the next
call. The loop is: `build_connector` → edit the files it flags → `build_connector`
again, until it comes back `answered`.

| Goal | Do |
| --- | --- |
| Start a new integration | `build_connector(directory, scaffold={ops:[...]})` — a clean foundation, not a hand-rolled skeleton |
| Fix / review an existing connector | `build_connector(directory)` — then edit what it flags and re-run |
| Install + smoke-test on a box | pyfsr `deploy_connector(client, directory)` (packages, installs, healthchecks; bumps the version on reupload so you don't get a ghost connector) |

Do not hand-write the request helper: the scaffold's is safe by
construction (TLS verify from config, status checked before `.json()`, path
segments quoted). Design each operation from the real API shape before coding it.

## Conventions that trip people

- Connector sources are loaded **latin-1** on the box: no en/em dashes anywhere.
- Quote user-supplied path segments with `urllib.parse.quote`; put user values in
  the query/body, never `.format()`-ed straight into the URL.
- Raise `ConnectorError` (from `connectors.core.connector`), not bare `Exception`.
- Import cyops helpers as `from connectors.cyops_utilities.builtins import ...`.

## Where this lives

The static gates are the domain layer (`fsr_playbooks.connectors`); scaffold,
package and deploy are pyfsr (`pyfsr.connector_dev`); `build_connector` composes
them. A connector that validates clean is still runtime-UNVERIFIED — deploy and
exercise it on a box before calling it done.
