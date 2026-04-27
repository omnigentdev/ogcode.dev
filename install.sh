#!/usr/bin/env bash
#
# ogcode installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/omnigentdev/ogcode.dev/main/install.sh | bash
#
# Or, once www domain is wired up:
#   curl -fsSL https://ogcode.dev/install.sh | bash
#
# Env overrides (optional):
#   OGCODE_VERSION   git ref to install (default: pinned below). Examples:
#                    OGCODE_VERSION=v0.4.0 ...
#                    OGCODE_VERSION=main   ... (latest committed, may be unstable)
#   OGCODE_REPO      git URL to install from (default: public github repo).
#                    Lets forks / mirrors / private clones reuse this script.
#   OGCODE_FORCE     "1" to force-reinstall over an existing pipx install.
#
# Exit codes:
#   0  success
#   1  prerequisite missing or unrecoverable error (message printed)
#   2  python too old
#   3  pipx not installed and not on PATH
#

set -euo pipefail

OGCODE_VERSION="${OGCODE_VERSION:-v0.4.0}"
OGCODE_REPO="${OGCODE_REPO:-https://github.com/omnigentdev/ogcode.dev.git}"
OGCODE_FORCE="${OGCODE_FORCE:-0}"

REQUIRED_PYTHON_MAJOR=3
REQUIRED_PYTHON_MINOR=11

info()  { printf '\033[1;34m[ogcode]\033[0m %s\n' "$*" >&2; }
warn()  { printf '\033[1;33m[ogcode]\033[0m %s\n' "$*" >&2; }
err()   { printf '\033[1;31m[ogcode]\033[0m %s\n' "$*" >&2; }
have()  { command -v "$1" >/dev/null 2>&1; }

find_python() {
  # Prefer the highest available python3.X >= 3.11. Falls back to plain
  # `python3` if that's the only one and its version satisfies the floor.
  local candidates=(python3.13 python3.12 python3.11 python3)
  for c in "${candidates[@]}"; do
    if have "$c"; then
      local ver
      ver="$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
      if [[ -n "$ver" ]]; then
        local major="${ver%%.*}" minor="${ver##*.}"
        if (( major > REQUIRED_PYTHON_MAJOR )) || \
           (( major == REQUIRED_PYTHON_MAJOR && minor >= REQUIRED_PYTHON_MINOR )); then
          echo "$c"
          return 0
        fi
      fi
    fi
  done
  return 1
}

ensure_python() {
  local py
  if ! py="$(find_python)"; then
    err "Python ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}+ is required but was not found on PATH."
    err "Install python3.11 or newer (e.g. 'brew install python@3.11' on macOS,"
    err "or your distro's package manager on Linux), then re-run this installer."
    exit 2
  fi
  info "Found $py ($("$py" --version 2>&1 | tr -d '\n'))"
  echo "$py"
}

ensure_pipx() {
  local py="$1"
  if have pipx; then
    info "Found pipx ($(pipx --version 2>/dev/null || echo '?'))"
    return 0
  fi
  err "pipx is not installed or not on PATH."
  err "Install it with one of:"
  err "  $py -m pip install --user pipx && $py -m pipx ensurepath"
  err "  brew install pipx          # macOS"
  err "  apt install pipx           # Debian/Ubuntu"
  err "Then open a new shell and re-run this installer."
  exit 3
}

install_ogcode() {
  local spec="git+${OGCODE_REPO}@${OGCODE_VERSION}"
  info "Installing ogcode from ${spec}"
  local args=(install "$spec")
  if [[ "$OGCODE_FORCE" == "1" ]]; then
    args=(install --force "$spec")
  elif pipx list --short 2>/dev/null | grep -qE '^ogcode\b'; then
    info "ogcode already installed via pipx — upgrading."
    args=(install --force "$spec")
  fi
  pipx "${args[@]}"
}

verify() {
  if ! have ogcode; then
    warn "Install completed but 'ogcode' is not on PATH yet."
    warn "Run 'pipx ensurepath' and open a new shell, then try 'ogcode --version'."
    return 0
  fi
  info "Installed: $(ogcode --version)"
  info "Try: ogcode --help"
}

main() {
  info "ogcode installer — version=${OGCODE_VERSION} repo=${OGCODE_REPO}"
  local py
  py="$(ensure_python)"
  ensure_pipx "$py"
  install_ogcode
  verify
}

main "$@"
