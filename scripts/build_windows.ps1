# Build the native Windows app for GameTracker (Version2 / Flet).
#
# Produces a self-contained Windows desktop app under build\windows\ (a folder
# containing GameTracker.exe + its data). Run from the repo root in the project
# venv:
#
#     .\.venv\Scripts\Activate.ps1
#     .\scripts\build_windows.ps1
#
# Prerequisites (one-time, NOT pip-installable):
#   * Flutter SDK (stable) on PATH                  -> https://docs.flutter.dev/get-started/install/windows
#   * Visual Studio 2019/2022 with the
#     "Desktop development with C++" workload        (MSVC + Windows SDK)
#   * Run `flet doctor` to verify the toolchain.
#
# The entry point is app_flet.py (set via tool.flet.app.module in pyproject.toml);
# the legacy PySimpleGUI main.py is intentionally NOT packaged.

$ErrorActionPreference = "Stop"

# Move to repo root (parent of this script's dir) regardless of where it's run.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "==> GameTracker Windows build" -ForegroundColor Cyan
Write-Host "    repo: $RepoRoot"

# Prefer the venv's flet, fall back to PATH.
$Flet = Join-Path $RepoRoot ".venv\Scripts\flet.exe"
if (-not (Test-Path $Flet)) { $Flet = "flet" }

# Quick toolchain sanity check (non-fatal; flet build will hard-fail with a
# clearer message if Flutter/VS are missing).
if (-not (Get-Command flutter -ErrorAction SilentlyContinue)) {
    Write-Warning "Flutter SDK not found on PATH. `flet build` needs it; install from https://docs.flutter.dev and re-run."
}

# NB: do NOT pass `--arch` for desktop. flet's `--arch` is for macOS/Android
# only, and it forwards the value to serious_python, whose Windows/Linux arch
# key is the empty string ""; passing `--arch x64` makes serious_python's
# per-arch install loop `continue` past the only entry, so it silently installs
# ZERO dependencies (the packaged app then crashes with ModuleNotFoundError for
# certifi/requests/etc.). Letting it default keeps the dependency install
# running. See docs/BUILD.md "Troubleshooting".
& $Flet build windows `
    --project "GameTracker" `
    --product "GameTracker" `
    --copyright "Copyright (C) 2025 DrNefarius" `
    --verbose

if ($LASTEXITCODE -ne 0) {
    Write-Error "flet build windows failed (exit $LASTEXITCODE). Run ``flet doctor`` and check the toolchain."
    exit $LASTEXITCODE
}

$Out = Join-Path $RepoRoot "build\windows"
Write-Host "==> Build complete: $Out" -ForegroundColor Green
Write-Host "    Launch: $Out\GameTracker.exe"
