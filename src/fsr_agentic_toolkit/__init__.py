"""FSR Agentic Toolkit: the agent layer for FortiSOAR.

pyfsr is the SDK. fsr_playbooks is the domain (compiler, validators, catalog).
This package is the agent layer on top of both: eight workflows that return one
evidence-backed result, the SDK verbs, one gate, one MCP server and one CLI. The
SOC Assistant connector is built on the same pieces.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("fsr-agentic-toolkit")
except PackageNotFoundError:  # not installed (e.g. running from a source tree)
    __version__ = "0.0.0"
