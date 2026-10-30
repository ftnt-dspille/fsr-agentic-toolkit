"""``fsr-agentic-toolkit init`` -- wire a coding agent to a FortiSOAR box.

``fsr-agentic-toolkit init`` is the one-command bootstrap: verify the connection with a
real whoami call, optionally save the settings to an env file, and print the
MCP config snippet for the agent product in use (Claude Code, OpenCode, GitHub
Copilot). The snippets launch the toolkit's MCP server (``fsr-agentic-toolkit
mcp``), preferring the locally installed executable and falling back to
``uvx --from fsr-agentic-toolkit``.

Safety env vars an agent setup may add to the snippet's ``env`` block:
``FSR_MCP_READONLY=1``, ``FSR_MCP_ALLOW_TOOLS``, ``FSR_MCP_DENY_TOOLS`` (see
:mod:`fsr_agentic_toolkit.verbs.tools`).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
from pathlib import Path


def _server_command() -> tuple[str, list[str]]:
    """The command + args that launch the toolkit MCP server on this machine."""
    exe = shutil.which("fsr-agentic-toolkit")
    if exe:
        return exe, ["mcp"]
    return "uvx", ["--from", "fsr-agentic-toolkit", "fsr-agentic-toolkit", "mcp"]


def _env_block(args: argparse.Namespace) -> dict[str, str]:
    env = {"FSR_BASE_URL": args.url}
    if getattr(args, "username", None) and getattr(args, "password", None):
        env["FSR_USERNAME"] = args.username
        env["FSR_PASSWORD"] = args.password
    else:
        env["FSR_API_KEY"] = args.api_key
    if args.no_verify_ssl:
        env["FSR_VERIFY_SSL"] = "false"
        env["FSR_SUPPRESS_INSECURE_WARNINGS"] = "true"
    if args.readonly:
        env["FSR_MCP_READONLY"] = "1"
    return env


def _claude_code_snippet(args: argparse.Namespace) -> str:
    command, cmd_args = _server_command()
    cfg = {
        "mcpServers": {
            "fortisoar": {
                "command": command,
                **({"args": cmd_args} if cmd_args else {}),
                "env": _env_block(args),
            }
        }
    }
    return "# .mcp.json (project root), or merge into ~/.claude.json\n" + json.dumps(cfg, indent=2)


def _opencode_snippet(args: argparse.Namespace) -> str:
    command, cmd_args = _server_command()
    cfg = {
        "mcp": {
            "fortisoar": {
                "type": "local",
                "command": [command, *cmd_args],
                "environment": _env_block(args),
                "enabled": True,
            }
        }
    }
    return "# opencode.json (project root) -- merge the 'mcp' key\n" + json.dumps(cfg, indent=2)


def _copilot_snippet(args: argparse.Namespace) -> str:
    command, cmd_args = _server_command()
    cfg = {
        "servers": {
            "fortisoar": {
                "command": command,
                **({"args": cmd_args} if cmd_args else {}),
                "env": _env_block(args),
            }
        }
    }
    return "# .vscode/mcp.json (VS Code / GitHub Copilot agent mode)\n" + json.dumps(cfg, indent=2)


def _toml_str(value: str) -> str:
    return json.dumps(value)  # a JSON string is a valid TOML basic string


def _codex_snippet(args: argparse.Namespace) -> str:
    command, cmd_args = _server_command()
    lines = ["# ~/.codex/config.toml (or: codex mcp add fortisoar --env KEY=VALUE -- <command> <args>)",
             "[mcp_servers.fortisoar]",
             f"command = {_toml_str(command)}"]
    if cmd_args:
        lines.append("args = [" + ", ".join(_toml_str(a) for a in cmd_args) + "]")
    lines.append("")
    lines.append("[mcp_servers.fortisoar.env]")
    lines += [f"{k} = {_toml_str(v)}" for k, v in _env_block(args).items()]
    return "\n".join(lines)


_SNIPPETS = {
    "claude-code": _claude_code_snippet,
    "opencode": _opencode_snippet,
    "copilot": _copilot_snippet,
    "codex": _codex_snippet,
}


def _verify(args: argparse.Namespace) -> dict | None:
    """whoami against the box; None (with a printed error) when unreachable.

    Under an API key, ``whoami`` is JWT-only (the DAS /api/auth login lookup
    rejects API keys). Rather than fail verification, identify the key by its
    ``ApiKey`` actor via ``/api/3/actors/current``, which answers in both auth
    modes -- the same degrade the whoami tool uses.
    """
    from pyfsr.client import FortiSOAR
    from pyfsr.exceptions import UnsupportedAuthOperationError

    use_user = bool(getattr(args, "username", None) and getattr(args, "password", None))
    try:
        client = (
            FortiSOAR(
                args.url,
                username=args.username,
                password=args.password,
                verify_ssl=not args.no_verify_ssl,
                suppress_insecure_warnings=args.no_verify_ssl,
            )
            if use_user
            else FortiSOAR(
                args.url,
                token=args.api_key,
                verify_ssl=not args.no_verify_ssl,
                suppress_insecure_warnings=args.no_verify_ssl,
            )
        )
        try:
            return client.users.whoami()
        except UnsupportedAuthOperationError:
            actor = client.get("/api/3/actors/current")
            return {
                "loginid": None,
                "email": None,
                "name": actor.get("name"),
                "principal_type": actor.get("@type") or actor.get("userType"),
            }
    except Exception as exc:  # noqa: BLE001 - report, don't trace
        print(f"error: could not verify the connection: {exc}", file=sys.stderr)
        return None


def _write_env_file(path: Path, args: argparse.Namespace) -> None:
    lines = [f"{k}={v}" for k, v in _env_block(args).items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the first byte (no write-then-chmod exposure window).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    os.chmod(path, 0o600)  # an O_TRUNC of a pre-existing looser file keeps its old mode


def cmd_init(args: argparse.Namespace) -> int:
    # Keep the password off argv (and out of shell history / `ps`) when we can:
    # a bare --username reads FSR_PASSWORD from the environment, or prompts for it
    # without echo. --password is still accepted for non-interactive use.
    if getattr(args, "username", None) and not getattr(args, "password", None):
        args.password = os.environ.get("FSR_PASSWORD")
        if not args.password and sys.stdin.isatty():
            args.password = getpass.getpass(f"FortiSOAR password for {args.username}: ")
    has_user = bool(getattr(args, "username", None) and getattr(args, "password", None))
    if not has_user and not getattr(args, "api_key", None):
        print(
            "error: provide --username (with --password, $FSR_PASSWORD, or a prompt), "
            "or --api-key",
            file=sys.stderr,
        )
        return 2
    me = _verify(args)
    if me is None:
        if not args.skip_verify:
            print("hint: re-run with --skip-verify to print the snippets anyway", file=sys.stderr)
            return 1
        print("continuing despite failed verification (--skip-verify)", file=sys.stderr)
    elif me.get("loginid"):
        print(f"connected: {me.get('loginid')} <{me.get('email')}> @ {args.url}")
    else:
        kind = me.get("principal_type") or "API key"
        print(f"connected: {me.get('name')} ({kind}) @ {args.url}")

    if args.save_env:
        path = Path(args.save_env).expanduser()
        _write_env_file(path, args)
        print(f"saved credentials to {path} (mode 600)")

    products = [p for p in _SNIPPETS if getattr(args, p.replace("-", "_"))]
    if not products:
        products = ["claude-code"]
    for product in products:
        print(f"\n--- {product} ---")
        print(_SNIPPETS[product](args))
    print(
        "\nsafety: add FSR_MCP_READONLY=1 to the env block for a read-only surface, "
        "or FSR_MCP_ALLOW_TOOLS/FSR_MCP_DENY_TOOLS to narrow it. Destructive tools "
        "always need confirm=true. By default every write asks the user to approve first and "
        "is refused when the client cannot be asked (FSR_MCP_APPROVAL=require, the default; "
        "auto falls back to the client's own prompts, off never asks). For Claude Code, "
        "`fsr-agentic-toolkit permissions` prints matching allow/ask rules."
    )
    from .verbs.mcp import OPERATING_GUIDE

    print("\n--- how to drive it ---\n" + OPERATING_GUIDE)
    return 0


def claude_permissions(server: str = "fortisoar") -> dict:
    """Claude Code ``permissions`` rules derived from the tool registry: every read is allowed,
    every write asks in the terminal. A write tool added later is on neither list, so it
    prompts by default. The server's own approval prompt (verbs/approval.py) works in any MCP
    client; these rules are the deterministic Claude Code layer on top."""
    from .verbs.mcp import LIST_INSTANCES
    from .verbs.tools import enabled_tools

    prefix = f"mcp__{server}__"
    tools = enabled_tools({})
    allow = [prefix + t.name for t in tools if not t.mutating] + [prefix + LIST_INSTANCES]
    ask = [prefix + t.name for t in tools if t.mutating]
    return {"permissions": {"allow": sorted(allow), "ask": sorted(ask)}}


def merge_permissions(existing: dict, new: dict) -> dict:
    """``existing`` settings with ``new``'s allow/ask rules added; nothing of theirs removed."""
    out = json.loads(json.dumps(existing))
    perms = out.setdefault("permissions", {})
    for key in ("allow", "ask"):
        have = list(perms.get(key) or [])
        perms[key] = have + [r for r in new["permissions"][key] if r not in have]
    return out


def cmd_permissions(args: argparse.Namespace) -> int:
    new = claude_permissions(args.server)
    if not args.write:
        print("# .claude/settings.json (project) or ~/.claude/settings.json: merge the 'permissions' key")
        print(json.dumps(new, indent=2))
        return 0
    path = Path(args.write).expanduser()
    existing = json.loads(path.read_text()) if path.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merge_permissions(existing, new), indent=2) + "\n")
    print(f"merged {len(new['permissions']['ask'])} ask rules and "
          f"{len(new['permissions']['allow'])} allow rules into {path}")
    return 0


def build_subparser(sub: argparse._SubParsersAction) -> None:
    """Wire the ``agent`` subcommands onto an existing subparsers object."""
    p_init = sub.add_parser("init", help="verify a box and print the MCP setup snippet for your coding agent")
    p_init.add_argument("--url", required=True, help="FortiSOAR base URL, e.g. fortisoar.example.com")
    p_init.add_argument("--username", help="FortiSOAR login id (use with --password)")
    p_init.add_argument("--password", help="FortiSOAR password; omit to read $FSR_PASSWORD or be prompted (keeps it off argv)")
    p_init.add_argument("--api-key", help="API key (Settings > API Keys); alternative to --username/--password")
    p_init.add_argument("--no-verify-ssl", action="store_true", help="disable TLS verification (lab boxes)")
    p_init.add_argument("--readonly", action="store_true", help="bake FSR_MCP_READONLY=1 into the snippet")
    p_init.add_argument("--save-env", metavar="PATH", help="also write the FSR_* values to this env file (mode 600)")
    p_init.add_argument("--skip-verify", action="store_true", help="print snippets even if whoami fails")
    p_init.add_argument("--claude-code", action="store_true", help="print the Claude Code .mcp.json snippet (default)")
    p_init.add_argument("--opencode", action="store_true", help="print the OpenCode opencode.json snippet")
    p_init.add_argument("--copilot", action="store_true", help="print the VS Code / Copilot .vscode/mcp.json snippet")
    p_init.add_argument("--codex", action="store_true", help="print the Codex ~/.codex/config.toml snippet")
    p_init.set_defaults(func=cmd_init)

    p_perm = sub.add_parser("permissions", help="print (or merge) Claude Code rules: reads allowed, writes ask")
    p_perm.add_argument("--server", default="fortisoar", help="the MCP server name in your config (default fortisoar)")
    p_perm.add_argument("--write", metavar="PATH", help="merge into this settings.json instead of printing")
    p_perm.set_defaults(func=cmd_permissions)
