"""asciicast v2 recorder + meta.json sidecar + rendered text transcript.

Spec: https://docs.asciinema.org/manual/asciicast/v2/

Each session produces three files:
  <session_dir>/transcript.cast  — JSONL: header + [t, "o"|"i", str] events
                                   (RAW — replayable with `asciinema play`)
  <session_dir>/transcript.txt   — vendor output rendered through a virtual
                                   terminal (pyte). Re-rendered in place every
                                   TXT_FLUSH_INTERVAL_S of output activity, so
                                   a `tail -f` reader sees the live session
                                   state.
  <session_dir>/meta.json        — vendor, argv, timestamps, exit code, env

Why "periodic full rewrite" instead of append-only:

  We previously tried to append each line to .txt at the moment pyte's
  HistoryScreen scrolled it into `history.top`. That works for vendors
  that drive the natural cursor-at-bottom + LF scroll path (verified by
  unit tests with rows=5 + 20 lines fed). It does NOT work for modern
  TUIs (Claude Code, Cursor agent, Codex, etc) which use cursor
  positioning + alternate-screen mode and manage their own scrollback
  internally — pyte's `index()` scroll hook never fires for them.

  So we re-render the full pyte state (history.top + display) every
  ~500ms of activity and rewrite the file in place (truncate + rewrite
  the same inode). `tail -f` works because the inode does not change;
  the partial-read window between truncation and rewrite is microseconds
  and effectively never observed for a file that nothing else writes to.

The .txt is what made it onto the user's screen, not the raw stream of
escape sequences. Spinners collapse to their final state, color codes
are not visible, redrawn frames are deduplicated naturally because the
virtual terminal models the screen the same way the real one does.
"""

from __future__ import annotations

import codecs
import datetime as _dt
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Optional

import pyte


# How many scrollback rows pyte keeps. ratio=0.5 means when the deque is
# full, half is evicted on each rotation (pyte API). 100k rows × ~200 cols
# is a few tens of MB peak — plenty for a long Claude/Codex session.
_HISTORY_ROWS = 100_000
_HISTORY_RATIO = 0.5

# Minimum gap between full-state .txt rewrites. 500ms is fast enough to
# feel live, slow enough to keep render cost negligible even on long
# sessions where each render walks 10k+ rows.
TXT_FLUSH_INTERVAL_S = 0.5


# Strips CSI (ESC [...), OSC (ESC ]... BEL/ST), DCS/SOS/PM/APC (ESC P/X/^/_ ... ST),
# and single-char escapes (ESC <one byte>). Reference: ECMA-48.
#
# Order matters: the multi-byte forms (CSI, OSC, DCS) must come *before* the
# single-byte Fe matcher, otherwise a stray ']' or 'P' after ESC gets eaten as
# a single-byte escape and the rest of the sequence leaks through as text.
_ANSI_RE = re.compile(
    r"\x1B"
    r"(?:"
    r"\[[0-?]*[ -/]*[@-~]"                    # CSI sequence
    r"|\][^\x07\x1B]*(?:\x07|\x1B\\)"         # OSC ... BEL or ESC \
    r"|[PX^_][^\x1B]*?\x1B\\"                 # DCS/SOS/PM/APC ... ESC \
    r"|[@-Z\\-_]"                             # 7-bit C1 Fe (single-byte)
    r"|."                                     # other single-char escape
    r")"
)


# CSI sequences that pyte 0.8.2's parser fails to recognise and leaks as
# drawn text. The CSI spec reserves bytes 0x3C-0x3F (`<`, `=`, `>`, `?`) as
# "private parameter" prefixes for vendor extensions. Pyte handles `?`
# (sets private mode) and `>` (silently consumed via SP_OR_GT), but `<` and
# `=` fall through its dispatch and the trailing parameters + final byte
# end up emitted as text — most visibly with the Kitty Keyboard Protocol
# pop (`\x1b[<...u`) and set (`\x1b[=...u`) forms, which Claude Code and
# other modern TUIs send to negotiate enhanced keyboard reporting.
#
# These sequences govern keyboard input behaviour, not screen state, so
# stripping them before feeding pyte preserves rendering fidelity. The
# raw .cast still has them so a downstream replay of the byte stream is
# unaffected.
#
# Verified failure cases (pyte 0.8.2):
#   "\x1b[<1uhello"  -> drawn text "1uhello"
#   "\x1b[=1;1uworld" -> drawn text "1;1uworld"
_PYTE_LEAK_RE = re.compile(
    r"\x1B\["          # CSI start
    r"[<=]"            # `<` or `=` private prefix that pyte mishandles
    r"[0-?]*"          # parameter bytes (digits, `;`, `:`, `<`, `=`, `>`, `?`)
    r"[ -/]*"          # intermediate bytes
    r"[@-~]"           # final byte
)


def _sanitize_for_pyte(s: str) -> str:
    """Drop CSI sequences pyte 0.8.2 cannot parse so they do not leak as text."""
    return _PYTE_LEAK_RE.sub("", s)


def strip_ansi(s: str) -> str:
    """Strip ANSI escape sequences and most C0 control chars from `s`.

    Keeps newlines (\\n) and tabs (\\t). Drops carriage returns (\\r) so
    TUI line-overwrites don't accumulate as flat-text duplicates. Drops
    other C0 control chars (BEL, BS, DEL, etc.).
    """
    s = _ANSI_RE.sub("", s)
    s = s.replace("\r", "")
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\n" or ch == "\t" or o >= 0x20:
            if o != 0x7F:  # DEL
                out.append(ch)
    return "".join(out)


def _utc_iso(t: float) -> str:
    return _dt.datetime.fromtimestamp(t, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_compact(t: float) -> str:
    return _dt.datetime.fromtimestamp(t, tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%S")


def make_session_dir(sessions_root: Path, vendor: str) -> Path:
    started = time.time()
    name = f"{_utc_compact(started)}-{secrets.token_hex(3)}"
    path = sessions_root / vendor / name
    path.mkdir(parents=True, exist_ok=True)
    return path


class Recorder:
    """Tee bytes from each direction into an asciicast v2 transcript and a
    pyte-rendered text transcript that updates live on disk.

    UTF-8 boundaries that fall mid-codepoint between chunks are buffered by
    incremental decoders so the transcript never contains spurious
    replacement characters from a clean byte stream.
    """

    def __init__(
        self,
        session_dir: Path,
        *,
        vendor: str,
        vendor_argv: list[str],
        user_argv: list[str],
        cols: int,
        rows: int,
        ogcode_version: str,
        txt_flush_interval_s: float = TXT_FLUSH_INTERVAL_S,
    ) -> None:
        self._dir = session_dir
        self._cast_path = session_dir / "transcript.cast"
        self._txt_path = session_dir / "transcript.txt"
        self._meta_path = session_dir / "meta.json"

        self._vendor = vendor
        self._vendor_argv = list(vendor_argv)
        self._cols = cols
        self._rows = rows

        self._t0 = time.monotonic()
        self._started_at = time.time()
        self._txt_flush_interval_s = txt_flush_interval_s
        self._last_txt_flush_at = self._t0 - txt_flush_interval_s  # force first write

        self._cast_fp = self._cast_path.open("w", buffering=1, encoding="utf-8")

        self._dec_o = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._dec_i = codecs.getincrementaldecoder("utf-8")(errors="replace")

        # Virtual terminal that the output stream is replayed into. Anything
        # the vendor wrote — colors, cursor moves, redraws, alt-screen flips —
        # gets resolved into a real screen state. Scrollback grows up to
        # _HISTORY_ROWS; lines beyond that are evicted (FIFO), but periodic
        # snapshots ensure they reach disk before eviction in normal use.
        self._screen = pyte.HistoryScreen(
            cols, rows, history=_HISTORY_ROWS, ratio=_HISTORY_RATIO
        )
        self._pyte_stream = pyte.Stream(self._screen)

        self._meta = {
            "ogcode_version": ogcode_version,
            "vendor": vendor,
            "vendor_argv": vendor_argv,
            "user_argv": user_argv,
            "started_at": _utc_iso(self._started_at),
            "ended_at": None,
            "duration_seconds": None,
            "exit_code": None,
            "cwd": os.getcwd(),
            "term": os.environ.get("TERM", ""),
            "shell": os.environ.get("SHELL", ""),
            "host_os": _host_os(),
        }

        header = {
            "version": 2,
            "width": cols,
            "height": rows,
            "timestamp": int(self._started_at),
            "env": {"SHELL": self._meta["shell"], "TERM": self._meta["term"]},
            "command": " ".join(vendor_argv),
            "title": "ogcode session",
        }
        self._cast_fp.write(json.dumps(header, ensure_ascii=False) + "\n")
        self._write_meta()
        self._snapshot_txt()  # initial header-only file so it exists immediately

    def feed_output(self, data: bytes) -> None:
        if not data:
            return
        s = self._dec_o.decode(data, final=False)
        if s:
            self._emit("o", s)
            try:
                self._pyte_stream.feed(_sanitize_for_pyte(s))
            except Exception:
                # Don't let a pyte parsing fault take down the wrapper —
                # the .cast remains the source of truth, and a worst-case
                # render only loses fidelity in the .txt.
                pass
            # Throttled snapshot — full re-render and in-place rewrite.
            now = time.monotonic()
            if now - self._last_txt_flush_at >= self._txt_flush_interval_s:
                self._snapshot_txt()
                self._last_txt_flush_at = now

    def feed_input(self, data: bytes) -> None:
        if not data:
            return
        s = self._dec_i.decode(data, final=False)
        if s:
            self._emit("i", s)

    def _emit(self, kind: str, text: str) -> None:
        t = max(0.0, time.monotonic() - self._t0)
        self._cast_fp.write(json.dumps([round(t, 6), kind, text], ensure_ascii=False) + "\n")

    def _write_meta(self) -> None:
        tmp = self._meta_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._meta, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(self._meta_path)

    def _snapshot_txt(self) -> None:
        """Re-render the full pyte state and rewrite transcript.txt in place.

        In-place rewrite (truncate the same inode, write fresh content)
        means `tail -f` continues to follow the same file across snapshots.
        The trade-off versus tmp+rename is a microsecond partial-read
        window between truncate and full write — unobservable in practice
        for a file no other writer touches. tail detects truncation and
        re-reads from the start cleanly on macOS BSD tail and GNU tail.
        """
        try:
            lines: list[str] = []
            for hline in self._screen.history.top:
                lines.append(_history_line_to_str(hline).rstrip())
            for vline in self._screen.display:
                lines.append(vline.rstrip())

            # Trim trailing blanks (TUI leaves the bottom of the viewport
            # empty during normal use).
            while lines and not lines[-1]:
                lines.pop()

            # Collapse runs of blank lines to at most one.
            collapsed: list[str] = []
            blank_streak = 0
            for line in lines:
                if not line.strip():
                    if blank_streak >= 1:
                        continue
                    blank_streak += 1
                else:
                    blank_streak = 0
                collapsed.append(line)

            header = (
                f"# ogcode session — {self._vendor} — {self._meta['started_at']}\n"
                f"# command: {' '.join(self._vendor_argv)}\n"
                f"# (live-rendered via pyte, in-place rewrite — `tail -f` to follow)\n\n"
            )
            body = "\n".join(collapsed)
            content = header + body + ("\n" if body else "")

            self._txt_path.write_text(content, encoding="utf-8")
        except Exception:
            pass

    def close(self, *, exit_code: Optional[int]) -> None:
        # Flush any incomplete codepoints buffered in the incremental decoders.
        tail_o = self._dec_o.decode(b"", final=True)
        if tail_o:
            self._emit("o", tail_o)
            try:
                self._pyte_stream.feed(_sanitize_for_pyte(tail_o))
            except Exception:
                pass
        tail_i = self._dec_i.decode(b"", final=True)
        if tail_i:
            self._emit("i", tail_i)

        try:
            self._cast_fp.flush()
            self._cast_fp.close()
        except Exception:
            pass

        # Final snapshot of the rendered state.
        self._snapshot_txt()

        ended = time.time()
        self._meta["ended_at"] = _utc_iso(ended)
        self._meta["duration_seconds"] = round(ended - self._started_at, 3)
        self._meta["exit_code"] = exit_code
        self._write_meta()


def _history_line_to_str(line) -> str:
    """Convert a pyte history/buffer line (StaticDefaultDict[col -> Char]) to text."""
    if hasattr(line, "items"):
        return "".join(c.data for _, c in sorted(line.items()))
    try:
        return "".join(c.data for c in line)
    except Exception:
        return str(line)


def _host_os() -> str:
    try:
        u = os.uname()
        return f"{u.sysname} {u.release}"
    except Exception:
        return ""
