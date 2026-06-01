#!/usr/bin/env bash
# Build the native Linux app for GameTracker (Version2 / Flet).
#
# Produces a self-contained Linux desktop bundle under build/linux/. Run from the
# repo root, ideally inside the project venv:
#
#     python3 -m venv .venv && source .venv/bin/activate
#     pip install -r requirements.txt
#     ./scripts/build_linux.sh
#
# Prerequisites (one-time; install with your distro package manager):
#   Debian/Ubuntu:
#     sudo apt-get update && sudo apt-get install -y \
#         clang cmake ninja-build pkg-config libgtk-3-dev \
#         liblzma-dev libstdc++-12-dev
#   Fedora:
#     sudo dnf install -y clang cmake ninja-build pkgconf-pkg-config gtk3-devel
#   Plus the Flutter SDK (stable) on PATH:  https://docs.flutter.dev/get-started/install/linux
#
# Run `flet doctor` to verify the toolchain. The entry point is app_flet.py
# (tool.flet.app.module in pyproject.toml); the legacy PySimpleGUI main.py is
# intentionally NOT packaged.
#
# ARM64 (e.g. a Raspberry Pi / ARM server / Apple-silicon Linux VM): run this
# same script ON the ARM64 machine — Flet cannot cross-compile desktop targets,
# so the build host's architecture is what you get. See docs/BUILD.md.

set -euo pipefail

# Move to repo root (parent of this script's dir).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

echo "==> GameTracker Linux build"
echo "    repo: $REPO_ROOT"
echo "    arch: $(uname -m)"

# Prefer the venv's flet, fall back to PATH.
FLET="$REPO_ROOT/.venv/bin/flet"
[ -x "$FLET" ] || FLET="flet"

if ! command -v flutter >/dev/null 2>&1; then
    echo "WARNING: Flutter SDK not found on PATH. 'flet build' needs it; install from" >&2
    echo "         https://docs.flutter.dev/get-started/install/linux and re-run." >&2
fi

"$FLET" build linux \
    --project "GameTracker" \
    --product "GameTracker" \
    --copyright "Copyright (C) 2025 DrNefarius" \
    --verbose

OUT="$REPO_ROOT/build/linux"
echo "==> Build complete: $OUT"
echo "    Launch: $OUT/GameTracker"
