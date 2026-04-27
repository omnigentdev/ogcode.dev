"""End-to-end PTY smoke test using a benign command — no real vendor required.

We fork `cat` under the runner: it's a TTY-aware program that echoes input back
through the line discipline, exercising both directions of the bridge plus
exit-code propagation. This is enough to validate the wrapper independent of
which (if any) vendor CLIs are installed on the host.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PKG_ROOT = Path(__file__).resolve().parents[1]


def _run_ogcode(*args, input_bytes: bytes = b"", timeout: float = 10.0) -> tuple[int, bytes, bytes]:
    """Invoke `python -m ogcode <args>` as a subprocess. Returns (rc, out, err)."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PKG_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ogcode", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    out, err = proc.communicate(input=input_bytes, timeout=timeout)
    return proc.returncode, out, err


def test_help_subprocess():
    rc, out, _ = _run_ogcode("--help")
    assert rc == 0
    assert b"usage: ogcode" in out


def test_unknown_vendor_subprocess():
    rc, _, err = _run_ogcode("banana")
    assert rc == 2
    assert b"unknown vendor" in err


def test_missing_binary_subprocess():
    """When the vendor binary is absent, exit 127 cleanly."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PKG_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PATH"] = "/var/empty"  # no binaries here
    proc = subprocess.run(
        [sys.executable, "-m", "ogcode", "claude"],
        capture_output=True,
        env=env,
        timeout=5,
    )
    assert proc.returncode == 127
    assert b"command not found" in proc.stderr


def _has(binary: str) -> bool:
    from shutil import which
    return which(binary) is not None


@pytest.mark.skipif(not _has("cat"), reason="cat(1) required for PTY smoke")
def test_pty_runner_with_cat(tmp_path, monkeypatch):
    """Drive the runner directly: spawn `cat`, feed bytes, assert echo + transcript."""
    from ogcode import pty_runner

    monkeypatch.setattr(pty_runner, "SESSIONS_ROOT", tmp_path / "sessions")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PKG_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["OGCODE_SESSIONS_ROOT_OVERRIDE"] = str(tmp_path / "sessions")
    code = (
        "import os, sys, pathlib; "
        "from ogcode import pty_runner; "
        "pty_runner.SESSIONS_ROOT = pathlib.Path(os.environ['OGCODE_SESSIONS_ROOT_OVERRIDE']); "
        "sys.exit(pty_runner.run(vendor='claude', argv=['/bin/cat'], user_argv=['ogcode','claude']))"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    # Feed a line, then EOF (Ctrl+D = byte 0x04 in raw mode).
    out, err = proc.communicate(input=b"hello\n\x04", timeout=10)
    assert proc.returncode == 0, err.decode(errors="replace")
    assert b"hello" in out

    sessions = list((tmp_path / "sessions" / "claude").iterdir())
    assert len(sessions) == 1
    cast = sessions[0] / "transcript.cast"
    meta = sessions[0] / "meta.json"
    assert cast.exists() and meta.exists()
    header_line = cast.read_text(encoding="utf-8").splitlines()[0]
    header = json.loads(header_line)
    assert header["version"] == 2
    meta_json = json.loads(meta.read_text(encoding="utf-8"))
    assert meta_json["vendor"] == "claude"
    assert meta_json["vendor_argv"] == ["/bin/cat"]
    assert meta_json["exit_code"] == 0
