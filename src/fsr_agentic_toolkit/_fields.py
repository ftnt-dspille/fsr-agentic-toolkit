"""Field-spec coercion shared by the create_module tool and the provision_module
workflow.

Lives in its own low-level module (it imports only pyfsr) so both the verb layer
(``verbs.tools``) and the workflow layer (``workflows.provision``) can use it
without the import cycle that ``verbs.tools`` <-> ``workflow_tools`` <->
``workflows`` would otherwise create.
"""
from __future__ import annotations

from typing import Any


def coerce_field_specs(fields: Any) -> Any:
    """Translate simple ``{name, type[, picklist][, required][, label]}`` specs
    into real attribute metadata via the typed builders.

    Hand-built dicts with a bare display type (``text``, ``picklist``) pass the
    staging POST but fail at publish with "Attribute type 'text' does not exist"
    (live-verified on 8.0.0), and keys like ``required`` are dropped, so the
    natural agent shape is translated here. A spec that already carries
    ``formType`` is treated as full metadata and passed through untouched.
    """
    if not fields:
        return fields
    # Classmethod builders: independent of the client, so test fakes work too.
    from pyfsr.api.modules_admin import ModulesAdminAPI

    out = []
    for f in fields:
        if not isinstance(f, dict) or "formType" in f or "type" not in f:
            out.append(f)
            continue
        spec = dict(f)
        name = spec.pop("name")
        ftype = spec.pop("type")
        picklist = spec.pop("picklist", None)
        if ftype in ("picklist", "multiselectpicklist") or picklist:
            out.append(
                ModulesAdminAPI.picklist_field(
                    name, picklist or "Severity", multi=ftype == "multiselectpicklist", **spec
                )
            )
        else:
            out.append(ModulesAdminAPI.typed_field(name, ftype, **spec))
    return out
