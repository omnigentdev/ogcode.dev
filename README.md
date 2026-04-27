# ogcode

Passthrough TUI wrapper for the major coding CLIs. Records every interactive session as a replayable asciicast and a live-rendered plain-text transcript — without altering the vendor's behaviour.

```
ogcode claude   [args...]    # → claude ...
ogcode agent    [args...]    # → cursor agent ...
ogcode codex    [args...]    # → codex ...
ogcode opencode [args...]    # → opencode ...
ogcode gemini   [args...]    # → gemini ...
```

The wrapper allocates a pseudo-terminal, `exec`s the vendor binary inside it, and forwards every byte of input/output between your terminal and the child. The vendor's TUI, slash commands, model picker, keyboard shortcuts, drag-and-drop file attachments, and image protocols (iTerm2 inline / kitty graphics / sixel) all keep working natively because the child sees a real TTY. Args after the vendor token are forwarded verbatim. Exit code is the child's exit code.

## What it records

Per session, under `$OGCODE_SESSIONS_DIR/<vendor>/<UTC>-<id>/` (default `~/.ogcode/sessions/<vendor>/<UTC>-<id>/`):

- **`transcript.cast`** (RAW) — [asciicast v2](https://docs.asciinema.org/manual/asciicast/v2/) JSONL. Replay with `asciinema play <path>`. Contains every byte the vendor wrote, escape sequences and all.
- **`transcript.txt`** (readable, live-snapshot) — vendor output rendered through a virtual terminal ([pyte](https://pyte.readthedocs.io/)) and **rewritten in place every ~500ms** of output activity. Watch live with `tail -f transcript.txt`. Spinners collapse to their final state, color codes resolve away, redrawn frames deduplicate, scrollback is preserved up to 100,000 rows. User keystrokes are not included (the vendor echoes them back as output, which is what gets rendered).
- **`meta.json`** — vendor, full argv, started/ended timestamps, exit code, cwd, term, shell. Note: the recorded `user_argv` is the verbatim ogcode command line, so **avoid passing secrets as flags** (e.g. `--api-key sk-…`) — they would land on disk in `meta.json`. Files stay local; ogcode does not upload anything.

## What it does NOT do

- **No transformation.** Bytes flow through verbatim. The wrapper does not edit prompts, scrub output, or inject anything.
- **No UI overlay.** The vendor TUI owns the screen.
- **No event extraction.** Transcripts are raw byte streams; no per-vendor parsing.
- **No replay subcommand.** Use `asciinema play` on the `.cast` files.
- **No Windows support.** macOS + Linux only.

## Install

### One-line installer (recommended)

```bash
curl -fsSL https://ogcode.dev/install.sh | bash
```

While `ogcode.dev` is being set up, the same script is served directly from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/omnigentdev/ogcode.dev/main/install.sh | bash
```

The installer requires Python 3.9+ and `pipx` already on `PATH` (it will not auto-install either, but prints the exact command to fix each missing prerequisite). On modern macOS, Apple's Command Line Tools provide a compatible `/usr/bin/python3` out-of-the-box.

Pin a specific version, install from a fork, or force-reinstall via env vars:

```bash
OGCODE_VERSION=v0.4.1 curl -fsSL .../install.sh | bash
OGCODE_REPO=https://github.com/<your-fork>/ogcode.dev.git curl -fsSL .../install.sh | bash
OGCODE_FORCE=1 curl -fsSL .../install.sh | bash
```

### From a git URL (manual)

```bash
pipx install git+https://github.com/omnigentdev/ogcode.dev.git@v0.4.1
# or
python3 -m pip install --user git+https://github.com/omnigentdev/ogcode.dev.git@v0.4.1
```

### From a source checkout (local development)

```bash
git clone https://github.com/omnigentdev/ogcode.dev.git
cd ogcode.dev
python3 -m venv .venv
.venv/bin/pip install -e .
```

Then either put `.venv/bin` on `PATH`, or use the shim at `bin/ogcode`, or symlink the venv binary into a directory on `PATH`:

```bash
ln -s "$(pwd)/.venv/bin/ogcode" ~/.local/bin/ogcode
```

## Configure

| Env var | Default | Effect |
|---|---|---|
| `OGCODE_SESSIONS_DIR` | `~/.ogcode/sessions/` | Where session transcripts land. Overridden by `--sessions-dir` if both are set. |

## Verify

```bash
ogcode --help
ogcode --version
ogcode claude --version       # arg passthrough
ls "$OGCODE_SESSIONS_DIR/claude" 2>/dev/null || ls ~/.ogcode/sessions/claude
```

## Tests

```bash
.venv/bin/pytest -q
```

## License

See [`LICENSE`](LICENSE).
