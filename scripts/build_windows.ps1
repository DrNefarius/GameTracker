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

# --arch x64 — the common desktop target. (ARM64 Windows is a separate run:
# `--arch arm64`, build on an ARM64 host; see docs/BUILD.md.)
#
# Known flet 0.85.2 / serious_python_windows 1.0.0 issue: the generated CMake
# always tries to copy build\site-packages into the bundle (it's gated on the
# SERIOUS_PYTHON_SITE_PACKAGES env var, which flet sets unconditionally), but
# serious_python only creates that dir when it has native wheels to install
# there — when it bundles everything into app.zip instead, the dir is absent and
# `cmake -E copy_directory` hard-fails. We pre-create the (possibly empty) dir so
# the copy is a harmless no-op. See docs/BUILD.md "Troubleshooting".
New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "build\site-packages") | Out-Null

& $Flet build windows `
    --arch x64 `
    --project "GameTracker" `
    --product "GameTracker" `
    --copyright "Copyright (C) 2025 DrNefarius" `
    --verbose

if ($LASTEXITCODE -ne 0) {
    # The site-packages copy runs late (during `flutter build`); if flet created
    # build\ fresh and wiped our dir mid-run, re-assert it and resume the native
    # build (idempotent — it reuses the already-packaged app.zip).
    Write-Warning "flet build returned $LASTEXITCODE; ensuring build\site-packages exists and resuming the native build..."
    New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot "build\site-packages") | Out-Null
    $FlutterProj = Join-Path $RepoRoot "build\flutter"
    if (Test-Path $FlutterProj) {
        Push-Location $FlutterProj
        $env:SERIOUS_PYTHON_SITE_PACKAGES = (Join-Path $RepoRoot "build\site-packages")
        & flutter build windows --release
        $rc = $LASTEXITCODE
        Pop-Location
        if ($rc -ne 0) {
            Write-Error "Native build still failed (exit $rc). Run ``flet doctor`` and check the toolchain."
            exit $rc
        }
        Write-Host "==> Resumed native build succeeded." -ForegroundColor Green
        $ReleaseDir = Join-Path $RepoRoot "build\flutter\build\windows\x64\runner\Release"
        Write-Host "    Launch: $ReleaseDir\GameTracker.exe"
        exit 0
    }
    Write-Error "flet build windows failed (exit $LASTEXITCODE). Run ``flet doctor`` and check the toolchain."
    exit $LASTEXITCODE
}

$Out = Join-Path $RepoRoot "build\windows"
Write-Host "==> Build complete: $Out" -ForegroundColor Green
Write-Host "    Launch: $Out\GameTracker.exe"
