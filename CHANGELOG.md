# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v0.4.1] - 2026-04-27

### Fixed — Python floor relaxed from 3.11 to 3.9

The original `requires-python = ">=3.11"` was inherited from the prior monorepo without an actual technical need; no code feature requires 3.10+ or 3.11+ (every source module uses `from __future__ import annotations`, so all the modern type-hint syntax is deferred-string only). Empirically verified that imports, CLI, and full session recording work under Python 3.9.6. Lowering to `>=3.9` lets `curl | bash` work on a stock macOS install — Apple's Command Line Tools ship a compatible `/usr/bin/python3` — without requiring a prior `brew install python@3.11`.

`install.sh` updated to match: detection floor lowered to 3.9, error message points macOS users at `xcode-select --install` instead of `brew install python@3.11`, and the candidate search list expanded to include `python3.10`/`python3.9` as fallbacks.

## [v0.4.0] - 2026-04-27

### Added — `--sessions-dir DIR` CLI flag

A per-invocation override for where transcripts land. Useful for one-off redirects without exporting an env var, and for embedding ogcode inside another tool's directory layout (`ogcode --sessions-dir "$OTHER_PROJECT_ROOT/sessions" claude ...`).

Precedence is now: `--sessions-dir` flag > `OGCODE_SESSIONS_DIR` env var > `~/.ogcode/sessions/`.

The flag must come before the vendor token; everything after the vendor token still forwards to the vendor verbatim. Both `--sessions-dir DIR` and `--sessions-dir=DIR` forms are accepted, and `~` is expanded.

### Added — `install.sh` one-line installer

A POSIX-bash installer at the repo root, intended to be served from `https://ogcode.dev/install.sh` (and reachable in the meantime via `https://raw.githubusercontent.com/omnigentdev/ogcode.dev/main/install.sh`). Detects Python 3.11+ and `pipx`, then runs `pipx install git+...@<version>`. Honours `OGCODE_VERSION`, `OGCODE_REPO`, and `OGCODE_FORCE` env overrides for pinning, forks, and reinstalls. Idempotent — rerunning upgrades an existing install in place.

## [v0.3.2] - 2026-04-26

### Fixed — stray `u` / `1u` / `1;1u` text in `transcript.txt`

Rendered `transcript.txt` from a Claude Code session contained stray `u` characters where there should have been spaces or nothing (`u ▐▛███▜▌` before the Claude logo, trailing `u` after slash commands, isolated `u` lines).

Root cause: pyte 0.8.2's CSI parser handles the private-mode prefix bytes `?` (sets the private flag) and `>` (silently consumed via `SP_OR_GT`), but the symmetric `<` and `=` prefixes fall through the dispatch table. When pyte sees `\x1b[<...u` or `\x1b[=...u`, it bails on the first byte after `[`, then treats the remaining parameters and the `u` final byte as plain drawn text.

Those two forms are exactly the **Kitty Keyboard Protocol** pop (`\x1b[<{n}u`) and set (`\x1b[={n};{m}u`) sequences that Claude Code and other modern TUIs send to negotiate enhanced keyboard reporting. They do not affect screen state, only keyboard input behaviour, so they are safe to drop before feeding pyte.

Fix: a pre-feed sanitizer regex (`_PYTE_LEAK_RE`) strips any CSI of the form `\x1B[[<=][params][intermediate][@-~]` before it reaches pyte. The raw `transcript.cast` keeps the original bytes, so a downstream replay through any conformant terminal still works correctly.

## [v0.3.1] - 2026-04-26

### Fixed — `tail -f transcript.txt` now works

The previous release wrote the transcript via tmp+rename (atomic replace). That changes the inode each rewrite, which breaks `tail -f` (it follows by inode → orphaned after the first rewrite).

Switched to in-place rewrite: `Path.write_text(content)` opens the same inode, truncates, and writes fresh content. The inode is preserved across snapshots, so `tail -f` keeps following the live file. Both BSD `tail` (macOS) and GNU `tail` (Linux) detect the truncation and re-read from the start cleanly.

The trade-off versus tmp+rename is a microsecond partial-read window between truncate and full write. For a transcript file no other writer touches, that window is effectively unobservable; even if observed, the next snapshot ~500ms later is correct.

## [v0.3.0] - 2026-04-26

### Changed — live transcript actually updates during real TUI sessions

The earlier "live append on scroll-into-history" approach worked for vendors that drive the natural cursor-at-bottom + LF scrolling path (unit tests with rows=5 + 20 lines fed pass cleanly). It did **not** work for the actual targets — Claude Code, Cursor agent, Codex — which manage their own scrollback inside an alternate-screen buffer using cursor positioning. pyte's `index()` scroll hook never fires for that rendering style, so `transcript.txt` got the header at session start and was empty until close.

Replaced with **periodic in-place rewrite**: every `TXT_FLUSH_INTERVAL_S` (default 500ms) of output activity, the recorder re-renders pyte's full state (scrollback + visible viewport) and writes the result to `transcript.txt`. Works regardless of how the vendor scrolls — pyte's screen state is always current, and the snapshot is just a dump of what's there.

Use `tail -f transcript.txt` to watch a live session.

## [v0.2.0] - 2026-04-26

### Changed — `transcript.txt` is rendered through a virtual terminal (pyte)

The earlier `transcript.txt` was a flat ANSI-stripped dump of the output stream — readable in trivial cases (`--version`) but unusable for real interactive sessions. TUI vendors redraw the screen continuously: spinners overwrite themselves on every tick, prompt boxes are repainted on every keystroke, streaming responses re-render the answer area character by character. ANSI strip alone captures every intermediate frame, so the resulting file is full of duplicate prompt-box outlines, partial states, and visual noise.

The fix: feed the output stream through a [pyte](https://pyte.readthedocs.io/) `HistoryScreen` — a pure-Python virtual terminal emulator — and dump the resolved screen state plus scrollback at session close. Pyte models the same screen the user's real terminal models, so:

- **Spinners collapse to their final state.**
- **Color codes resolve away.**
- **Redrawn frames deduplicate naturally.**
- **Scrollback is preserved** up to 100,000 rows.

### Added

- New dependency: `pyte>=0.8.2,<1` (~150 KB, pure Python, MIT, transitive dep on `wcwidth`).

## [v0.1.1] - 2026-04-25

### Fixed — shim is now symlink-safe

The `bin/ogcode` shim resolved its package directory from `$0` without following symlinks, so symlinking it into a directory on `PATH` (`ln -s .../bin/ogcode ~/.local/bin/ogcode`) made it look for the venv next to the **symlink** instead of the real script — falling back to the system `python3`, which doesn't have `ogcode` installed and fails with `No module named ogcode`.

The shim now walks `BASH_SOURCE[0]` through symlinks (canonical `while [ -L ]` + `readlink` pattern; portable across BSD and GNU) before computing the package directory. Chained symlinks are handled.

## [v0.1.0] - 2026-04-25

### Added — initial release of `ogcode`

A CLI binary that ships an `ogcode` command, a pure pseudo-terminal (PTY) passthrough wrapper around five third-party coding CLIs:

| Token      | Vendor binary                                          |
| ---------- | ------------------------------------------------------ |
| `claude`   | `claude` (Anthropic Claude Code)                       |
| `agent`    | `cursor agent` (Cursor's agent subcommand — two-token) |
| `codex`    | `codex` (OpenAI Codex CLI)                             |
| `opencode` | `opencode`                                             |
| `gemini`   | `gemini` (Google Gemini CLI)                           |

The wrapper allocates a PTY, `exec`s the vendor with all extra args forwarded verbatim, and bridges the user's terminal stdin/stdout to the PTY. The vendor's TUI, slash commands, model picker, keyboard shortcuts, drag-and-drop, image protocols, `--resume`, etc. all keep working **natively** because they're talking to a real TTY.

Each session is recorded as asciicast v2 (`transcript.cast`, replayable with `asciinema play`) plus a `meta.json` sidecar with vendor / argv / timestamps / exit code / cwd / term / shell.
