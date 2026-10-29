"""Human approval for writes, enforced by the MCP server so it holds in every client.

Coding agents do not share a permissions file format, but they all speak MCP. Two things
work everywhere:

1. **Tool hints.** Each tool is advertised with ``readOnlyHint`` / ``destructiveHint``, the
   standard annotations clients use to decide what may run without asking.
2. **Elicitation.** Before a write runs, the server asks the *user* (not the model) to approve
   it, through the client's own UI, showing the target box and the arguments with anything
   credential-shaped masked.

``FSR_MCP_APPROVAL``:

- ``auto`` (default) - ask when the client supports elicitation; otherwise run the write and
  leave it to the client's own tool prompts.
- ``require`` - ask, and refuse the write when the client cannot be asked.
- ``off`` - never ask (CI, or a client with its own approval flow already configured).

Reads never prompt. A call the safety policy would refuse anyway (read-only mode, a deny list,
a missing ``confirm``) is refused first, so the human is only ever asked about a call that
would really run.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
from typing import Any

from .tools import REGISTRY, _policy_refusal

_SECRETISH = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|private[_-]?key|credential|authorization)", re.IGNORECASE)
_MAX_ARGS = 1600
_MAX_BRIEF = 220
_MASK = "***"


def approval_timeout(env: dict[str, str] | None = None) -> float:
    """Seconds to wait for the user's answer (``FSR_MCP_APPROVAL_TIMEOUT``, default 300)."""
    try:
        return max(1.0, float((env if env is not None else os.environ).get("FSR_MCP_APPROVAL_TIMEOUT") or 300))
    except ValueError:
        return 300.0


def approval_mode(env: dict[str, str] | None = None) -> str:
    value = ((env if env is not None else os.environ).get("FSR_MCP_APPROVAL") or "auto").strip().lower()
    return value if value in ("auto", "require", "off") else "auto"


def mask_secrets(value: Any) -> Any:
    """A copy of ``value`` with the value of every credential-named key replaced."""
    if isinstance(value, dict):
        return {k: (_MASK if _SECRETISH.search(str(k)) and not isinstance(v, (dict, list))
                    else mask_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_secrets(v) for v in value]
    return value


def tool_hints(name: str) -> dict[str, Any]:
    """``ToolAnnotations`` fields for a tool: reads are read-only, writes are not, the
    irreversible ones are marked destructive. Every tool talks to an external system."""
    spec = REGISTRY.get(name)
    if spec is None:  # the list_instances meta-tool and anything unregistered
        return {"read_only_hint": True, "open_world_hint": False}
    if not spec.mutating:
        return {"read_only_hint": True, "open_world_hint": True}
    return {"read_only_hint": False, "destructive_hint": bool(spec.destructive),
            "idempotent_hint": False, "open_world_hint": True}


def _supports_elicitation(session: Any) -> bool:
    from mcp import types

    try:
        return bool(session.check_client_capability(types.ClientCapabilities(
            elicitation=types.ElicitationCapability(form=types.FormElicitationCapability()))))
    except Exception:  # noqa: BLE001 - an older client/session: treat as unable to be asked
        return False


def _summary(spec: Any, arguments: dict[str, Any], target: str) -> str:
    """The prompt text. Clients collapse a long message after a few lines (Claude Code shows
    about three, then "+N more lines"), so everything a person needs to decide goes first:
    what will run, where, with what, and how to answer. The detail follows."""
    shown = mask_secrets({k: v for k, v in arguments.items() if k not in ("instance", "confirm")})
    brief = json.dumps(shown, separators=(", ", ": "), default=str)
    if len(brief) > _MAX_BRIEF:
        brief = brief[:_MAX_BRIEF] + "..."
    full = json.dumps(shown, indent=2, default=str)
    if len(full) > _MAX_ARGS:
        full = full[:_MAX_ARGS] + "\n... (truncated)"
    first = spec.description.strip().split(". ")[0].rstrip(".")
    lead = "IRREVERSIBLE change" if spec.destructive else "Approve"
    return (f"{lead}: {spec.name} on {target}?\n"
            f"{brief}\n"
            "Answer Yes (tick it / choose True) to approve; anything else cancels.\n\n"
            f"What it does: {first}.\nFull arguments:\n{full}")


def _refusal(kind: str, message: str, tool: str) -> dict[str, Any]:
    return {"error": {"type": kind, "message": message, "tool": tool}}


_STATE_KEY = os.urandom(32)  # per process: an approval cannot outlive or cross servers


def _state(name: str, arguments: dict[str, Any]) -> str:
    """Binds an approval to ONE tool and its exact arguments (``confirm`` aside)."""
    body = json.dumps([name, {k: v for k, v in arguments.items() if k != "confirm"}],
                      sort_keys=True, default=str)
    return hmac.new(_STATE_KEY, body.encode(), hashlib.sha256).hexdigest()


def _form(spec: Any, arguments: dict[str, Any], target: str) -> tuple[str, dict[str, Any]]:
    return (_summary(spec, arguments, target),
            {"type": "object",
             "properties": {"approve": {"type": "boolean", "title": "Approve this change", "default": False}},
             "required": ["approve"]})


def _answer(result: Any) -> bool:
    return getattr(result, "action", None) == "accept" and bool((getattr(result, "content", None) or {}).get("approve"))


_DECLINED = ("The user did not approve this change, so nothing was done. Do not retry the "
             "same call; ask the user what they want instead.")


async def require_approval(session: Any, name: str, arguments: dict[str, Any],
                           target: str, *, mode: str | None = None, request_id: Any = None,
                           input_responses: dict[str, Any] | None = None,
                           request_state: str | None = None) -> Any:
    """``None`` when the call may proceed; a refusal dict to return instead; or an
    ``InputRequiredResult`` to hand back so the client asks the user and retries.

    Two ways to ask, because MCP has two eras. A client on the older protocol can be sent an
    ``elicitation/create`` request directly (tied to the in-flight call by ``request_id``).
    Over stdio on the 2026-07-28 protocol a server may not send requests at all
    (``NoBackChannelError``); it answers the call with an ``InputRequiredResult`` carrying the
    approval form, and the client retries the same call with the user's answer in
    ``input_responses``. That retry is only honoured when ``request_state`` matches this exact
    tool and arguments."""
    mode = mode or approval_mode()
    spec = REGISTRY.get(name)
    if spec is None or not spec.mutating or mode == "off":
        return None
    refused = _policy_refusal(spec, dict(arguments))
    if refused is not None:
        return refused  # would not run anyway: do not ask the human about it

    # -- the retry of an earlier InputRequiredResult --------------------------------------------
    if input_responses and "approve" in input_responses:
        if not request_state or not hmac.compare_digest(request_state, _state(name, arguments)):
            return _refusal("ApprovalMismatch", f"{name} was not run: the approval does not belong to "
                            "this call. Ask again.", name)
        return None if _answer(input_responses["approve"]) else _refusal("ApprovalDeclined", _DECLINED, name)

    if not _supports_elicitation(session):
        if mode == "require":
            return _refusal("ApprovalUnavailable",
                            f"{name} changes the box and this client cannot ask the user to approve "
                            "it (FSR_MCP_APPROVAL=require). Use a client that supports MCP "
                            "elicitation, or set FSR_MCP_APPROVAL=auto and rely on its tool prompts.", name)
        return None
    message, schema = _form(spec, arguments, target)
    try:
        result = await asyncio.wait_for(
            session.elicit_form(message=message, requested_schema=schema, related_request_id=request_id),
            approval_timeout())
    except TimeoutError:
        # a client that accepted the request but never answered (headless, no UI): not an approval
        return _refusal("ApprovalUnavailable",
                        f"{name} was not run: nobody answered the approval prompt within "
                        f"{approval_timeout():g}s. Nothing was changed.", name)
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "NoBackChannelError":
            from mcp_types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult

            return InputRequiredResult(
                input_requests={"approve": ElicitRequest(params=ElicitRequestFormParams(
                    message=message, requested_schema=schema))},
                request_state=_state(name, arguments))
        # never let a broken prompt become an unasked write
        return _refusal("ApprovalUnavailable",
                        f"{name} was not run: the client advertised approval prompts but the request "
                        f"failed ({type(exc).__name__}). Nothing was changed.", name)
    return None if _answer(result) else _refusal("ApprovalDeclined", _DECLINED, name)
