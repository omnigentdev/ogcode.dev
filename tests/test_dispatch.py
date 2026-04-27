"""Vendor token → argv resolution and CLI argument handling."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from ogcode import cli


@pytest.mark.parametrize(
    "token, prefix",
    [
        ("claude", ["claude"]),
        ("agent", ["cursor", "agent"]),
        ("codex", ["codex"]),
        ("opencode", ["opencode"]),
        ("gemini", ["gemini"]),
    ],
)
def test_dispatch_table(token, prefix):
    assert cli.DISPATCH[token] == prefix


def test_resolve_argv_forwards_extra_verbatim():
    assert cli.resolve_argv("claude", ["--resume"]) == ["claude", "--resume"]
    assert cli.resolve_argv("agent", []) == ["cursor", "agent"]
    assert cli.resolve_argv("codex", ["exec", "--full-auto", "fix it"]) == [
        "codex",
        "exec",
        "--full-auto",
        "fix it",
    ]


def test_resolve_argv_unknown_vendor_raises():
    with pytest.raises(KeyError):
        cli.resolve_argv("banana", [])


def test_main_help(capsys):
    rc = cli.main(["--help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "usage: ogcode" in out
    assert "claude" in out and "agent" in out and "codex" in out


def test_main_no_args_prints_help(capsys):
    rc = cli.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "usage: ogcode" in out


def test_main_version(capsys):
    rc = cli.main(["--version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("ogcode ")


def test_main_unknown_vendor_exit_2(capsys):
    rc = cli.main(["banana"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown vendor" in err


def test_main_missing_binary_exit_127(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    rc = cli.main(["claude"])
    err = capsys.readouterr().err
    assert rc == 127
    assert "command not found" in err


def test_parse_ogcode_opts_no_flags_returns_args_unchanged():
    sd, rest = cli._parse_ogcode_opts(["claude", "--version"])
    assert sd is None
    assert rest == ["claude", "--version"]


def test_parse_ogcode_opts_sessions_dir_space_form():
    sd, rest = cli._parse_ogcode_opts(["--sessions-dir", "/tmp/x", "claude", "--version"])
    assert sd == Path("/tmp/x")
    assert rest == ["claude", "--version"]


def test_parse_ogcode_opts_sessions_dir_equals_form():
    sd, rest = cli._parse_ogcode_opts(["--sessions-dir=/tmp/x", "claude"])
    assert sd == Path("/tmp/x")
    assert rest == ["claude"]


def test_parse_ogcode_opts_expanduser():
    sd, _ = cli._parse_ogcode_opts(["--sessions-dir", "~/foo", "claude"])
    assert sd == Path("~/foo").expanduser()


def test_parse_ogcode_opts_stops_at_vendor_token():
    """Flags after the vendor token must NOT be consumed by ogcode — they
    forward to the vendor verbatim."""
    sd, rest = cli._parse_ogcode_opts(["claude", "--sessions-dir", "/tmp/x"])
    assert sd is None
    assert rest == ["claude", "--sessions-dir", "/tmp/x"]


def test_parse_ogcode_opts_missing_value_raises():
    with pytest.raises(ValueError, match="requires a path"):
        cli._parse_ogcode_opts(["--sessions-dir"])


def test_parse_ogcode_opts_unknown_flag_raises():
    with pytest.raises(ValueError, match="unknown option"):
        cli._parse_ogcode_opts(["--bogus", "claude"])


def test_main_sessions_dir_forwarded_to_runner(monkeypatch):
    """`--sessions-dir DIR claude ...` should call run() with
    sessions_root=Path(DIR), and rest of argv forwarded."""
    captured: dict = {}

    def fake_run(*, vendor, argv, user_argv, sessions_root):
        captured["vendor"] = vendor
        captured["argv"] = argv
        captured["sessions_root"] = sessions_root
        return 0

    import ogcode.pty_runner as pr
    monkeypatch.setattr(pr, "run", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/fake/{name}")

    rc = cli.main(["--sessions-dir", "/tmp/sessions", "claude", "--version"])
    assert rc == 0
    assert captured["vendor"] == "claude"
    assert captured["argv"] == ["/fake/claude", "--version"]
    assert captured["sessions_root"] == Path("/tmp/sessions")


def test_main_no_sessions_dir_passes_none_to_runner(monkeypatch):
    captured: dict = {}

    def fake_run(*, vendor, argv, user_argv, sessions_root):
        captured["sessions_root"] = sessions_root
        return 0

    import ogcode.pty_runner as pr
    monkeypatch.setattr(pr, "run", fake_run)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/fake/{name}")

    rc = cli.main(["claude"])
    assert rc == 0
    assert captured["sessions_root"] is None


def test_main_unknown_flag_exit_2(capsys):
    rc = cli.main(["--bogus", "claude"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "unknown option" in err
