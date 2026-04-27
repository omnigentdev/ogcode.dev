"""asciicast v2 recorder format conformance + UTF-8 boundary handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ogcode.recorder import Recorder, make_session_dir, strip_ansi


def _read_cast(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    events = [json.loads(ln) for ln in lines[1:]]
    return header, events


@pytest.fixture
def session_dir(tmp_path):
    return make_session_dir(tmp_path / "sessions", "claude")


def test_make_session_dir_layout(tmp_path):
    d = make_session_dir(tmp_path / "sessions", "claude")
    assert d.parent.name == "claude"
    assert d.parent.parent.name == "sessions"
    assert d.exists() and d.is_dir()
    # name shape: YYYYMMDDThhmmss-<6 hex>
    name = d.name
    assert "T" in name and "-" in name
    head, tail = name.rsplit("-", 1)
    assert len(tail) == 6
    int(tail, 16)


def test_header_well_formed(session_dir):
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude", "--resume"],
        user_argv=["ogcode", "claude", "--resume"],
        cols=132,
        rows=40,
        ogcode_version="0.74.0",
    )
    rec.close(exit_code=0)
    header, events = _read_cast(session_dir / "transcript.cast")
    assert header["version"] == 2
    assert header["width"] == 132 and header["height"] == 40
    assert header["command"] == "claude --resume"
    assert "timestamp" in header
    assert events == []


def test_meta_well_formed(session_dir):
    rec = Recorder(
        session_dir,
        vendor="codex",
        vendor_argv=["codex", "exec"],
        user_argv=["ogcode", "codex", "exec"],
        cols=80,
        rows=24,
        ogcode_version="0.74.0",
    )
    rec.close(exit_code=42)
    meta = json.loads((session_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["vendor"] == "codex"
    assert meta["vendor_argv"] == ["codex", "exec"]
    assert meta["user_argv"] == ["ogcode", "codex", "exec"]
    assert meta["exit_code"] == 42
    assert meta["started_at"].endswith("Z")
    assert meta["ended_at"].endswith("Z")
    assert meta["ogcode_version"] == "0.74.0"


def test_records_output_and_input(session_dir):
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=132,
        rows=40,
        ogcode_version="0.74.0",
    )
    rec.feed_output(b"hello")
    rec.feed_input(b"abc")
    rec.feed_output(b" world")
    rec.close(exit_code=0)

    _, events = _read_cast(session_dir / "transcript.cast")
    kinds_texts = [(e[1], e[2]) for e in events]
    assert ("o", "hello") in kinds_texts
    assert ("i", "abc") in kinds_texts
    assert ("o", " world") in kinds_texts
    # Timestamps are monotonic non-decreasing.
    times = [e[0] for e in events]
    assert times == sorted(times)


def test_utf8_split_across_chunks_no_replacement_chars(session_dir):
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=132,
        rows=40,
        ogcode_version="0.74.0",
    )
    # "héllo" — é is two bytes (0xC3 0xA9). Split between them.
    payload = "héllo".encode("utf-8")
    rec.feed_output(payload[:2])  # 'h' + first byte of é
    rec.feed_output(payload[2:])  # second byte of é + 'llo'
    rec.close(exit_code=0)

    _, events = _read_cast(session_dir / "transcript.cast")
    joined = "".join(e[2] for e in events if e[1] == "o")
    assert joined == "héllo"
    assert "�" not in joined


def test_osc_image_sequence_is_passthrough(session_dir):
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=132,
        rows=40,
        ogcode_version="0.74.0",
    )
    osc = b"\x1b]1337;File=name=PNG;inline=1:base64payload\x1b\\"
    rec.feed_output(osc)
    rec.close(exit_code=0)

    _, events = _read_cast(session_dir / "transcript.cast")
    out = "".join(e[2] for e in events if e[1] == "o")
    # ESC bytes survive the JSON round-trip (encoded as ).
    assert "\x1b]1337;File=" in out
    assert "base64payload" in out


# ─── strip_ansi ──────────────────────────────────────────────────────────────


def test_strip_ansi_removes_csi_color_codes():
    s = "\x1b[31mhello\x1b[0m world"
    assert strip_ansi(s) == "hello world"


def test_strip_ansi_removes_cursor_movement():
    s = "\x1b[2J\x1b[H\x1b[?25lprompt\x1b[?25h"
    assert strip_ansi(s) == "prompt"


def test_strip_ansi_removes_osc_image_sequence():
    s = "before\x1b]1337;File=name=PNG;inline=1:base64payload\x07after"
    assert strip_ansi(s) == "beforeafter"


def test_strip_ansi_removes_dcs_sequence():
    s = "before\x1bP1;2;3qpayload\x1b\\after"
    assert strip_ansi(s) == "beforeafter"


def test_strip_ansi_drops_carriage_return_keeps_newline_and_tab():
    assert strip_ansi("a\r\nb") == "a\nb"
    assert strip_ansi("spinner...\r") == "spinner..."
    assert strip_ansi("col1\tcol2\nrow2") == "col1\tcol2\nrow2"


def test_strip_ansi_removes_other_c0_controls():
    assert strip_ansi("a\x07b\x08c\x7fd") == "abcd"


def test_strip_ansi_preserves_unicode():
    assert strip_ansi("héllo \U0001F600 world") == "héllo \U0001F600 world"


def test_recorder_renders_via_pyte(session_dir):
    """transcript.txt is the rendered terminal state, not the raw stream."""
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=80,
        rows=24,
        ogcode_version="0.77.0",
    )
    # Color codes get rendered (no escape bytes in output).
    rec.feed_output(b"\x1b[?25l\x1b[31mError:\x1b[0m disk full\r\n")
    # Spinner: each frame overwrites the previous via CR. Final state is "Done!".
    rec.feed_output(b"Loading...\r")
    rec.feed_output(b"Loading.. \r")
    rec.feed_output(b"Loading.  \r")
    rec.feed_output(b"Done!     \r\n")
    # Plain prose flows through.
    rec.feed_output(b"\r\nplain prose flows through cleanly.\r\n")
    rec.close(exit_code=0)

    txt_path = session_dir / "transcript.txt"
    assert txt_path.exists()
    txt = txt_path.read_text(encoding="utf-8")

    assert txt.startswith("# ogcode session — claude — ")
    assert "# command: claude\n" in txt
    assert "live-rendered via pyte" in txt

    # Pyte resolves the redraw — only the final spinner state survives,
    # and there are no leaked Loading.../Loading.. intermediate frames.
    assert "Done!" in txt
    assert "Loading..." not in txt
    assert "Loading.. " not in txt

    # Color codes get rendered out.
    assert "Error:" in txt
    assert "disk full" in txt
    assert "\x1b" not in txt
    assert "[31m" not in txt

    assert "plain prose flows through cleanly." in txt


def test_recorder_txt_does_not_record_input(session_dir):
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=80,
        rows=24,
        ogcode_version="0.77.0",
    )
    rec.feed_input(b"secret-keystrokes")
    rec.feed_output(b"vendor reply\r\n")
    rec.close(exit_code=0)

    txt = (session_dir / "transcript.txt").read_text(encoding="utf-8")
    # Vendor echoes input back as output, so input is intentionally not
    # rendered into the .txt — the echoed output is what you see on screen.
    assert "secret-keystrokes" not in txt
    assert "vendor reply" in txt


def test_recorder_preserves_scrollback_beyond_viewport(session_dir):
    """A session that produces more rows than the viewport must keep
    scrollback in the .txt — the user expects 'the whole conversation',
    not just the last screenful."""
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=80,
        rows=5,  # tiny viewport so scrollback exercises
        ogcode_version="0.77.0",
    )
    for i in range(20):
        rec.feed_output(f"line {i}\r\n".encode())
    rec.close(exit_code=0)

    txt = (session_dir / "transcript.txt").read_text(encoding="utf-8")
    # All 20 lines must be present despite the 5-row viewport.
    for i in range(20):
        assert f"line {i}" in txt, f"missing line {i} in:\n{txt}"


def test_recorder_snapshots_txt_during_session(session_dir):
    """transcript.txt must reflect new content during the session, not just
    at close — so a `tail -F` reader sees the session live. We use
    txt_flush_interval_s=0 to force a snapshot on every feed."""
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=80,
        rows=24,
        ogcode_version="0.79.0",
        txt_flush_interval_s=0.0,
    )
    txt_path = session_dir / "transcript.txt"

    # Header-only file exists immediately at construction.
    assert txt_path.exists()
    initial = txt_path.read_text(encoding="utf-8")
    assert "# ogcode session — claude" in initial
    assert "first chunk" not in initial

    rec.feed_output(b"first chunk arrived\r\n")
    after_first = txt_path.read_text(encoding="utf-8")
    assert "first chunk arrived" in after_first, (
        f"first chunk should be in .txt before close; got:\n{after_first}"
    )

    rec.feed_output(b"second chunk arrived\r\n")
    after_second = txt_path.read_text(encoding="utf-8")
    assert "first chunk arrived" in after_second
    assert "second chunk arrived" in after_second

    rec.close(exit_code=0)


def test_recorder_throttles_snapshots(session_dir):
    """When the interval is non-zero, repeated rapid feeds within a single
    interval do NOT each rewrite the file — only the first feed triggers a
    snapshot, the rest are coalesced until the interval elapses."""
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=80,
        rows=24,
        ogcode_version="0.79.0",
        txt_flush_interval_s=10.0,  # huge interval — second feed should NOT flush
    )
    txt_path = session_dir / "transcript.txt"

    rec.feed_output(b"chunk-A\r\n")  # forces first snapshot
    snap1 = txt_path.read_text(encoding="utf-8")
    assert "chunk-A" in snap1

    rec.feed_output(b"chunk-B\r\n")  # within throttle window — should NOT flush
    snap2 = txt_path.read_text(encoding="utf-8")
    assert "chunk-B" not in snap2  # not yet flushed
    assert snap2 == snap1  # file unchanged

    rec.close(exit_code=0)
    # close() always does a final snapshot.
    final = txt_path.read_text(encoding="utf-8")
    assert "chunk-A" in final
    assert "chunk-B" in final


def test_recorder_inplace_rewrite_preserves_inode(session_dir):
    """Snapshots rewrite transcript.txt in place — the inode must NOT change
    between snapshots, otherwise `tail -f` (which follows by inode) would
    detach from the live file after the first rewrite. Also: no .txt.tmp
    file should be created (we no longer use tmp+rename)."""
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=80,
        rows=24,
        ogcode_version="0.79.1",
        txt_flush_interval_s=0.0,
    )
    txt_path = session_dir / "transcript.txt"

    # Initial snapshot was written by __init__; capture its inode.
    inode_initial = txt_path.stat().st_ino

    rec.feed_output(b"first chunk\r\n")
    inode_after_first = txt_path.stat().st_ino
    assert inode_after_first == inode_initial, (
        "inode changed between snapshots — tail -f would lose the file"
    )

    rec.feed_output(b"second chunk\r\n")
    inode_after_second = txt_path.stat().st_ino
    assert inode_after_second == inode_initial

    rec.close(exit_code=0)
    inode_after_close = txt_path.stat().st_ino
    assert inode_after_close == inode_initial

    # We no longer use tmp+rename, so no .txt.tmp should ever exist.
    assert not (session_dir / "transcript.txt.tmp").exists()


def test_recorder_collapses_blank_runs(session_dir):
    """Runs of blank lines collapse to at most one — pyte's history can
    contain many blank rows when the vendor clears or sends bare newlines."""
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=80,
        rows=2,
        ogcode_version="0.79.0",
        txt_flush_interval_s=0.0,
    )
    rec.feed_output(b"\r\n\r\n\r\n\r\n\r\n\r\nfinal\r\n")
    rec.close(exit_code=0)

    txt = (session_dir / "transcript.txt").read_text(encoding="utf-8")
    body = txt.split("\n\n", 1)[1] if "\n\n" in txt else txt
    assert "\n\n\n" not in body
    assert "final" in txt


def test_kitty_keyboard_protocol_does_not_leak_as_text(session_dir):
    """Pyte 0.8.2's CSI parser fails on \\x1b[<...u and \\x1b[=...u (Kitty
    Keyboard Protocol pop/set). Without sanitization, the parameters and
    terminator leak as drawn text in transcript.txt.

    Real-world reproduction: Claude Code emits these sequences to negotiate
    enhanced keyboard reporting; users see stray "u", "1u", "1;1u" in the
    rendered transcript.
    """
    rec = Recorder(
        session_dir,
        vendor="claude",
        vendor_argv=["claude"],
        user_argv=["ogcode", "claude"],
        cols=80,
        rows=24,
        ogcode_version="0.79.2",
        txt_flush_interval_s=0.0,
    )
    rec.feed_output(b"\x1b[<1uhello world\r\n")
    rec.feed_output(b"\x1b[=1;1usecond line\r\n")
    rec.feed_output(b"\x1b[?uthird line\r\n")
    rec.feed_output(b"\x1b[>1ufourth line\r\n")
    rec.close(exit_code=0)

    txt = (session_dir / "transcript.txt").read_text(encoding="utf-8")

    assert "hello world" in txt
    assert "second line" in txt
    assert "third line" in txt
    assert "fourth line" in txt

    body = txt.split("\n\n", 1)[1] if "\n\n" in txt else txt
    assert "1uhello" not in body, f"pop-form CSI leaked as text:\n{body}"
    assert "1;1usecond" not in body, f"set-form CSI leaked as text:\n{body}"


def test_sanitize_for_pyte_strips_kitty_csi():
    """Unit test of the sanitizer regex."""
    from ogcode.recorder import _sanitize_for_pyte

    # Pop: <[params]u
    assert _sanitize_for_pyte("\x1b[<1uhello") == "hello"
    assert _sanitize_for_pyte("\x1b[<uplain") == "plain"
    assert _sanitize_for_pyte("\x1b[<1;2;3uafter") == "after"

    # Set: =[params]u
    assert _sanitize_for_pyte("\x1b[=1;1uworld") == "world"
    assert _sanitize_for_pyte("\x1b[=ujust") == "just"

    # Working forms must NOT be stripped (pyte handles them).
    assert _sanitize_for_pyte("\x1b[?uquery") == "\x1b[?uquery"
    assert _sanitize_for_pyte("\x1b[>1upush") == "\x1b[>1upush"

    # Standard CSI must NOT be stripped.
    assert _sanitize_for_pyte("\x1b[31mred\x1b[0m") == "\x1b[31mred\x1b[0m"
    assert _sanitize_for_pyte("\x1b[2J") == "\x1b[2J"

    # Mixed stream — only the broken sequences go.
    assert (
        _sanitize_for_pyte("before\x1b[<1uafter\x1b[=2;3uend")
        == "beforeafterend"
    )
