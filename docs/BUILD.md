# Building GameTracker (Version2 / Flet) — Phase 4

The Version2 UI is a [Flet](https://flet.dev) (Flutter) app. `flet build`
compiles it into a **native desktop executable** per platform. This document
covers Windows and Linux (incl. ARM64). The legacy PySimpleGUI app (`main.py`)
is **not** packaged — the build entry point is `app_flet.py`, set via
`tool.flet.app.module` in `pyproject.toml`.

> **`flet build` cannot cross-compile desktop targets.** You build *on* the OS
> (and CPU architecture) you're targeting: Windows exe on Windows, Linux binary
> on Linux, ARM64 binary on an ARM64 host. There is no `--target-os` flag.

---

## What's in the repo

| File | Purpose |
|---|---|
| `pyproject.toml` | `[tool.flet]` build config: entry module (`app_flet`), metadata, **build-scoped dependencies** (deliberately excludes PySimpleGUI — see below). |
| `assets/icon.png` | 256×256 app icon `flet build` turns into per-platform icons. |
| `scripts/build_windows.ps1` | One-shot Windows build. |
| `scripts/build_linux.sh` | One-shot Linux build (x64 or ARM64, per host). |

### Why the build deps differ from `requirements.txt`

`flet build` prefers `[project.dependencies]` in `pyproject.toml` over
`requirements.txt`. The packaged app's dependency set **omits `PySimpleGUI`**:
the Flet UI only reaches it through one lazy import (the Statistics "Gaming
heatmap" chart — the known sg leak tracked for Phase 5), and that path already
degrades to a placeholder when the import fails. So the bundle stays
tkinter/PySimpleGUI-free. `requirements.txt` keeps PySimpleGUI for the legacy dev
entry point (`main.py`), which is still runnable side-by-side until Phase 5.

---

## Windows

### Prerequisites (one-time)

1. **Flutter SDK** (stable channel) on `PATH` —
   <https://docs.flutter.dev/get-started/install/windows>.
2. **Visual Studio 2019 or 2022** with the **“Desktop development with C++”**
   workload (gives you MSVC + the Windows 10/11 SDK). The Community edition is
   fine. (Build Tools-only also works.)
3. Verify: `flet doctor` (or `flutter doctor`) — both should report no blocking
   issues for the *Windows* toolchain.

### Build

```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\build_windows.ps1
```

Output: `build\windows\` — a self-contained folder containing
`GameTracker.exe` plus its data/DLLs. Zip that folder to distribute, or wrap it
with an installer (e.g. Inno Setup) later.

### ARM64 Windows

Run the build **on an ARM64 Windows machine** with `--arch arm64`:

```powershell
.\.venv\Scripts\flet.exe build windows --arch arm64 --project GameTracker
```

---

## Linux

### Prerequisites (one-time)

Debian/Ubuntu:

```bash
sudo apt-get update && sudo apt-get install -y \
    clang cmake ninja-build pkg-config libgtk-3-dev \
    liblzma-dev libstdc++-12-dev
```

Fedora:

```bash
sudo dnf install -y clang cmake ninja-build pkgconf-pkg-config gtk3-devel
```

Plus the **Flutter SDK** (stable) on `PATH` —
<https://docs.flutter.dev/get-started/install/linux>. Verify with `flet doctor`.

### Build

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/build_linux.sh
```

Output: `build/linux/` — a self-contained bundle; launch `build/linux/GameTracker`.

### ARM64 Linux (Raspberry Pi, ARM servers, Apple-silicon Linux VMs)

Flet's weakest target. Build **on the ARM64 device itself** (no cross-compile):
run the exact same `./scripts/build_linux.sh` there. `uname -m` should report
`aarch64`. Expect the first build to be slow (Flutter compiles the engine
shell). If GTK headers are missing the build fails early with a clear pkg-config
error — install `libgtk-3-dev` and retry.

---

## Troubleshooting

* **`main.py not found ...`** — the build is looking for the wrong entry point.
  Confirm `pyproject.toml` has `[tool.flet.app] module = "app_flet"`, or pass
  `--module-name app_flet` explicitly.
* **`Flutter SDK not found`** — install Flutter and ensure `flutter` is on
  `PATH` in the same shell you run the build from.
* **MSVC / Windows SDK errors** — the “Desktop development with C++” workload is
  missing or incomplete; reopen the Visual Studio Installer and add it.
* **`Error copying directory from "...\build\site-packages"`** (build fails in
  the `serious_python_windows` / `CopyPythonDLLs` step with `MSB3073`) — a
  flet 0.85.2 / serious_python 1.0.0 packaging bug. The generated CMake always
  tries to copy `build/site-packages` into the bundle (it's gated on the
  `SERIOUS_PYTHON_SITE_PACKAGES` env var, which flet sets unconditionally), but
  serious_python only creates that dir when it has native wheels to install
  there. When it bundles every dependency into `app.zip` instead, the dir never
  exists and `cmake -E copy_directory` hard-fails. **The build scripts work
  around this automatically** (they pre-create the empty dir and, if needed,
  resume the native build). If you invoke `flet build` directly, first run
  `mkdir build/site-packages` (Windows: `New-Item -ItemType Directory -Force
  build\site-packages`).
* **Slow first build** — `flet build` downloads a Flutter build template and
  compiles the native shell on first run; subsequent builds are much faster.
* **Tray icon missing in the bundle** — `tray_icon.py` resolves
  `gameslisticon.ico` relative to its module dir (and falls back to a generated
  image), so the tray still works; ship `gameslisticon.ico` alongside the app
  for the branded icon.
