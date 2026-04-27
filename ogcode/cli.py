"""ogcode entry point — argv parsing, dispatch table, hand-off to the PTY runner."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

from . import __version__

DISPATCH: dict[str, list[str]] = {
    "claude":   ["claude"],
    "agent":    ["cursor", "agent"],
    "codex":    ["codex"],
    "opencode": ["opencode"],
    "gemini":   ["gemini"],
}

USAGE = """\
usage: ogcode [options] <vendor> [vendor-args...]

vendors:
  claude     → claude
  agent      → cursor agent
  codex      → codex
  opencode   → opencode
  gemini     → gemini

options (must come before <vendor>; everything after is forwarded verbatim):
  --sessions-dir DIR    Override where transcripts land for this run.
                        Precedence: this flag > $OGCODE_SESSIONS_DIR >
                        ~/.ogcode/sessions/.
  -h, --help            Show this help and exit.
  -V, --version         Show ogcode version and exit.

Wraps the chosen vendor's interactive CLI in a pseudo-terminal and records
the session to <sessions-dir>/<vendor>/<UTC>-<id>/. All args after the
vendor token are forwarded to the vendor verbatim.
"""


def _print_help() -> None:
    sys.stdout.write(USAGE)


def _print_version() -> None:
    sys.stdout.write(f"ogcode {__version__}\n")


def resolve_argv(vendor_token: str, extra: Sequence[str]) -> List[str]:
    """Return the full argv list to exec for `vendor_token + extra`.

    Raises KeyError if the vendor token is unknown.
    """
    prefix = DISPATCH[vendor_token]
    return [*prefix, *extra]


def _parse_ogcode_opts(args: List[str]) -> Tuple[Path | None, List[str]]:
    """Consume ogcode-specific flags from the front of ``args`` until the
    first non-flag token (the vendor). Returns ``(sessions_dir, rest)``.

    Stops at the first token that does not start with ``-``, so anything
    after the vendor token is forwarded to the vendor untouched.
    """
    sessions_dir: Path | None = None
    i = 0
    while i < len(args):
        tok = args[i]
        if not tok.startswith("-"):
            break
        if tok == "--sessions-dir":
            if i + 1 >= len(args):
                raise ValueError("--sessions-dir requires a path argument")
            sessions_dir = Path(args[i + 1]).expanduser()
            i += 2
            continue
        if tok.startswith("--sessions-dir="):
            sessions_dir = Path(tok.split("=", 1)[1]).expanduser()
            i += 1
            continue
        raise ValueError(f"unknown option: {tok}")
    return sessions_dir, args[i:]


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help", "help"):
        _print_help()
        return 0
    if args[0] in ("-V", "--version"):
        _print_version()
        return 0

    try:
        sessions_dir, rest = _parse_ogcode_opts(args)
    except ValueError as e:
        sys.stderr.write(f"ogcode: {e}\n\n")
        sys.stderr.write(USAGE)
        return 2

    if not rest:
        sys.stderr.write("ogcode: missing <vendor>\n\n")
        sys.stderr.write(USAGE)
        return 2

    vendor_token, extra = rest[0], rest[1:]
    if vendor_token not in DISPATCH:
        sys.stderr.write(f"ogcode: unknown vendor: {vendor_token!r}\n\n")
        sys.stderr.write(USAGE)
        return 2

    full_argv = resolve_argv(vendor_token, extra)
    resolved = shutil.which(full_argv[0])
    if resolved is None:
        sys.stderr.write(f"ogcode: {full_argv[0]}: command not found in PATH\n")
        return 127
    full_argv[0] = resolved

    from .pty_runner import run

    return run(
        vendor=vendor_token,
        argv=full_argv,
        user_argv=["ogcode", *args],
        sessions_root=sessions_dir,
    )
