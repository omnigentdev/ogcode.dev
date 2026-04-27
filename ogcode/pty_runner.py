"""PTY passthrough runner.

Allocates a pseudo-terminal, forks the vendor process, bridges bytes
between the parent's stdin/stdout and the PTY master fd, and tees both
directions to the asciicast recorder.

Design notes (full rationale: docs/plans/20260425T2111-ogcode-tui-wrapper.md):
  - The parent's stdin is put in raw mode so keystrokes pass through
    unfiltered (Ctrl+C, arrows, bracketed paste, drag-and-drop paths,
    iTerm2/kitty/sixel image escapes — all just bytes).
  - SIGWINCH on the parent is forwarded to the child PTY via TIOCSWINSZ
    so terminal resize re-flows the vendor TUI.
  - SIGINT/SIGQUIT are NOT trapped in the parent — the line discipline
    forwards them to the child's process group automatically because the
    child becomes a session leader via setsid() inside pty.fork().
  - Termios are restored in a try/finally AND via atexit AND via SIGTERM
    handler — three lines of defense so the user's shell is never left
    in raw mode if we crash.
  - Exit code is the child's exit code (or 128+signal if it died on a
    signal), so shell pipelines see a meaningful return code.
"""

from __future__ import annotations

import atexit
import errno
import fcntl
import os
import pty
import select
import signal
import struct
import sys
import termios
import tty
from pathlib import Path
from typing import Sequence

from . import __version__
from .recorder import Recorder, make_session_dir

CHUNK = 65536


def _default_sessions_root() -> Path:
    """Where session transcripts land.

    Resolution order:
      1. ``OGCODE_SESSIONS_DIR`` env var — lets a caller redirect transcripts
         anywhere on disk (useful for tests, CI, or embedding ogcode inside
         another tool's directory layout).
      2. ``$HOME/.ogcode/sessions/`` — the standalone default. Per-user,
         survives reinstalls of ogcode itself, easy to grep.
    """
    env = os.environ.get("OGCODE_SESSIONS_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".ogcode" / "sessions"


# Resolved at import time so tests can monkey-patch `pty_runner.SESSIONS_ROOT`
# the same way they did before this refactor. Callers that want to honour a
# late env-var change should call `_default_sessions_root()` directly.
SESSIONS_ROOT = _default_sessions_root()


def _get_winsize(fd: int) -> tuple[int, int, int, int]:
    """Return (rows, cols, xpix, ypix) for the given tty fd, or sane defaults."""
    try:
        packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols, xpix, ypix = struct.unpack("HHHH", packed)
        if rows == 0 or cols == 0:
            return (40, 132, 0, 0)
        return (rows, cols, xpix, ypix)
    except OSError:
        return (40, 132, 0, 0)


def _set_winsize(fd: int, rows: int, cols: int, xpix: int = 0, ypix: int = 0) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, xpix, ypix))
    except OSError:
        pass


def run(
    *,
    vendor: str,
    argv: Sequence[str],
    user_argv: Sequence[str],
    sessions_root: Path | None = None,
) -> int:
    """Fork `argv` under a PTY, bridge I/O with the parent terminal, record.

    If ``sessions_root`` is given (e.g. from the ``--sessions-dir`` CLI
    flag), it overrides the module-level ``SESSIONS_ROOT`` for this call
    only. Otherwise the import-time-resolved ``SESSIONS_ROOT`` is used,
    which honours ``OGCODE_SESSIONS_DIR`` or falls back to
    ``~/.ogcode/sessions/``.

    Returns the child's exit code (or 128+signal if it died on a signal).
    """
    parent_in_fd = sys.stdin.fileno()
    parent_out_fd = sys.stdout.fileno()

    parent_is_tty = os.isatty(parent_in_fd)

    rows, cols, _, _ = _get_winsize(parent_in_fd) if parent_is_tty else (40, 132, 0, 0)

    saved_termios = None
    if parent_is_tty:
        try:
            saved_termios = termios.tcgetattr(parent_in_fd)
        except termios.error:
            saved_termios = None

    def _restore_termios() -> None:
        if saved_termios is not None:
            try:
                termios.tcsetattr(parent_in_fd, termios.TCSADRAIN, saved_termios)
            except (termios.error, OSError):
                pass

    atexit.register(_restore_termios)

    session_dir = make_session_dir(sessions_root if sessions_root is not None else SESSIONS_ROOT, vendor)
    recorder = Recorder(
        session_dir,
        vendor=vendor,
        vendor_argv=list(argv),
        user_argv=list(user_argv),
        cols=cols,
        rows=rows,
        ogcode_version=__version__,
    )

    sys.stderr.write(f"[ogcode] session: {session_dir}\n")
    sys.stderr.flush()

    pid, master_fd = pty.fork()
    if pid == 0:
        # Child: replace ourselves with the vendor binary. The child
        # already has a controlling TTY (the slave side of the PTY) and
        # is the leader of a new session/process group.
        try:
            os.execvp(argv[0], list(argv))
        except OSError as exc:
            os.write(2, f"ogcode: exec {argv[0]}: {exc}\n".encode())
            os._exit(127)

    # Parent.
    _set_winsize(master_fd, rows, cols)

    if parent_is_tty:
        try:
            tty.setraw(parent_in_fd)
        except termios.error:
            pass

    def _on_winch(_signum, _frame):
        if parent_is_tty:
            r, c, xp, yp = _get_winsize(parent_in_fd)
            _set_winsize(master_fd, r, c, xp, yp)

    prev_winch = signal.signal(signal.SIGWINCH, _on_winch)

    def _on_term(_signum, _frame):
        # Forward to the child's process group so it can shut down cleanly,
        # then let the wait loop finish naturally.
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    prev_term = signal.signal(signal.SIGTERM, _on_term)
    prev_hup = signal.signal(signal.SIGHUP, _on_term)

    exit_code: int = 0
    try:
        exit_code = _bridge_loop(
            parent_in_fd=parent_in_fd,
            parent_out_fd=parent_out_fd,
            master_fd=master_fd,
            child_pid=pid,
            recorder=recorder,
        )
    finally:
        signal.signal(signal.SIGWINCH, prev_winch)
        signal.signal(signal.SIGTERM, prev_term)
        signal.signal(signal.SIGHUP, prev_hup)

        try:
            os.close(master_fd)
        except OSError:
            pass

        _restore_termios()
        recorder.close(exit_code=exit_code)
        sys.stderr.write(f"[ogcode] transcript: {session_dir}/transcript.cast (exit {exit_code})\n")
        sys.stderr.flush()

    return exit_code


def _bridge_loop(
    *,
    parent_in_fd: int,
    parent_out_fd: int,
    master_fd: int,
    child_pid: int,
    recorder: Recorder,
) -> int:
    """select() between parent stdin and the PTY master, copying bytes both ways."""
    fds = [parent_in_fd, master_fd]
    while True:
        try:
            r, _, _ = select.select(fds, [], [])
        except InterruptedError:
            continue
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            break

        if parent_in_fd in r:
            try:
                data = os.read(parent_in_fd, CHUNK)
            except OSError as exc:
                if exc.errno in (errno.EIO, errno.EBADF):
                    data = b""
                else:
                    raise
            if not data:
                # Parent stdin closed (e.g. running headless). Stop forwarding
                # input but keep draining child output until child exits.
                if parent_in_fd in fds:
                    fds.remove(parent_in_fd)
            else:
                _write_all(master_fd, data)
                recorder.feed_input(data)

        if master_fd in r:
            try:
                data = os.read(master_fd, CHUNK)
            except OSError as exc:
                # EIO on a PTY master means the slave (child) has gone away.
                if exc.errno in (errno.EIO, errno.EBADF):
                    data = b""
                else:
                    raise
            if not data:
                break
            _write_all(parent_out_fd, data)
            recorder.feed_output(data)

    # Drain any remaining child output without blocking.
    try:
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        while True:
            try:
                data = os.read(master_fd, CHUNK)
            except (BlockingIOError, OSError):
                break
            if not data:
                break
            _write_all(parent_out_fd, data)
            recorder.feed_output(data)
    except OSError:
        pass

    return _wait_for(child_pid)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            n = os.write(fd, view)
        except OSError as exc:
            if exc.errno == errno.EINTR:
                continue
            return
        if n <= 0:
            return
        view = view[n:]


def _wait_for(pid: int) -> int:
    while True:
        try:
            _, status = os.waitpid(pid, 0)
        except InterruptedError:
            continue
        except ChildProcessError:
            return 0
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
