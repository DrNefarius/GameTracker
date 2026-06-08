# Version2 (Flet UI migration) — Session Handoff

Compressed state for resuming the GameTracker UI migration in a fresh session.
Read this + `docs/VERSION2_PARITY_AUDIT.md` first.

## 1. Goal & ground rules
- **Project**: GameTracker — Python game-library tracker (repo `DrNefarius/GameTracker`).
- **Task**: replace the deprecated **PySimpleGUI** UI with a modern **Flet (Flutter)** UI,
  keeping the backend intact. Targets: native Windows exe + native Linux/ARM app.
- **Branch**: all work on local branch **`Version2`** — **NEVER push** (keep local).
- **venv python**: `.venv/Scripts/python.exe`. **Flet 0.85.2** (+ `flet-desktop` 0.85.2 installed).
- **Run the app**: `.venv/Scripts/python.exe app_flet.py` (or `flet run app_flet.py`). Legacy
  PySimpleGUI entry `main.py` is left runnable side-by-side until Phase 5.
- **Commits**: subject `Version2: …`; end body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
  **Avoid backticks in `git commit -m`** (bash command-substitution mangles the message).

## 2. Architecture
- **Backend = reused** (no GUI imports): `data_management`, `session_data`,
  `config`, `constants`, `process_watcher`, `notifications` (OS toasts), `igdb_integration`,
  `auto_updater`, `discord_integration`, `store_manifests`, `pause_utils`, `idle_detection`,
  `utilities`, `visualizations`, `session_visualizations`, `tray_icon`, `watcher_log`.
  (Mostly untouched, but this session **modified** `auto_updater` (install-target/cleanup, §10A),
  `process_watcher` (`force_track_exe`, §10C), `session_watcher_bridge`, `session_management`
  (heatmap relocation, §6), and added `legacy_cleanup` — all still GUI-free.)
- **`single_instance.py`** (new, GUI-free): cross-platform single-instance guard. **Prevention** =
  exclusive non-blocking lock on `<config dir>/gametracker.lock` (`msvcrt.locking` on Windows,
  `fcntl.flock` elsewhere; OS frees it on crash, immune to Windows dynamic-range port exclusions).
  **Activation** (best-effort) = a loopback listener on port 48219; a blocked launch pings it so the
  running instance surfaces its (possibly tray-hidden) window. `app_flet.py` calls `try_acquire()`
  before `ft.run` and exits if it returns None; `app.main` wires `get_instance().on_activate` to the
  sink's tray-'open' path (`write_event_value('-TRAY-ACTION-', {'action':'open_app'})`).
- **`core/` = UI-agnostic facade** (no GUI imports):
  - `services.py` → `GameLibraryService`: `.data` (list of `(orig_idx, row)`), `.filename`,
    `.config`; methods `bootstrap / open_path / import_excel / save / save_as / add_game /
    update_game / delete_game / get_game / set_status`; module fns `new_game_row`, `edited_game_row`.
  - `ratings_logic.py` → sg-free `format_rating / calculate_session_rating_average /
    get_session_rating_summary` (legacy `ratings.py` imports sg, can't be imported by the new UI).
  - `igdb_logic.py` → sg-free IGDB business logic (search / confidence-ranking / auto-match / details /
    cover caching), relocated out of `igdb_ui.py` (which imports sg). `igdb_ui.py` re-imports it under
    the old underscore names for legacy compatibility. Consumed by `ui_flet/igdb_match.py`.
  - `notifier.py` → `UINotifier` Protocol (the watcher's UI seam).
- **`ui_flet/` = all Flet UI** (sg-free, **one exception** — see §6). Entry `app_flet.py` → `ft.run(main)`.
- **Game row layout** (the in-memory tuple's `row`): `[0]=name, [1]=release_date('YYYY-MM-DD' or '-'),
  [2]=platform, [3]=time_played('HH:MM:SS' or None), [4]=status, [5]=owned('✅'/''),
  [6]=last_played, [7]=sessions(list of dicts), [8]=status_history(list), [9]=rating(dict|None),
  [10]=igdb(dict|None)]`.

## 3. Flet 0.85.2 API cheat-sheet (post-rewrite; most online examples are WRONG)
Every one of these caused a real bug — honor them:
- Entry `ft.run(main)`. Capitalized `ft.Colors.X`, `ft.Icons.X`.
- **Dialogs**: build `ft.AlertDialog(modal=, title=, content=, actions=[...], actions_alignment=)`;
  OPEN `page.show_dialog(dlg)`, CLOSE `page.pop_dialog()`. **No** `page.open/close/dialog`.
  Flet effectively shows **one dialog at a time** — pop before showing another (or design flows
  to never stack 2 deep; the Game Hub pops itself then re-opens).
- **SnackBar**: `sb=ft.SnackBar(content=ft.Text(..)); page.overlay.append(sb); sb.open=True; page.update()`.
- **Dropdown**: event is **`on_select`** (NOT `on_change`); options `ft.dropdown.Option(key=k, text=k)`
  — set **both** key and text or it renders blank. (Editable dropdowns w/ huge option lists render a
  giant menu box — avoid for big lists; use a ListView, see Statistics scope picker.)
- **DataTable**: `DataColumn(label=, numeric=, on_sort=handler)`; sort handler gets event with
  `e.column_index` + `e.ascending`. `DataRow(cells=[DataCell(content, on_tap=)], color=)`. Give the
  table an explicit `width` to fill horizontally. Wrap WIDE/short content in a horizontal-scroll
  **Row** (a vertical-scroll Column nested in the page's scroll Column balloons to a huge height).
- **`control.page` RAISES** until the control is mounted → guard updates:
  `try: return self.control.page is not None except (RuntimeError, AssertionError): return False`.
- **Threads → UI**: `page.update()` from a foreign thread does NOT propagate. Use
  `page.run_task(coro, *args)` (safe to call from any thread; schedules on the loop). The live timer
  + the watcher sink both rely on this.
- **FilePicker**: lives in `page.services` (append it); `pick_files(...)`/`save_file(...)` are **async
  coroutines** (await; use `async def` handlers). Result items have `.path`.
- **Window methods `center()`/`to_front()`/`destroy()` are ASYNC coroutines** — sync calls no-op
  (RuntimeWarning). Use sync **properties** instead: `window.visible / minimized / focused /
  always_on_top / prevent_close`. To focus: set `focused=True` + flip `always_on_top` True→False.
  Close-to-tray: `window.prevent_close=True` + `window.on_event` handler checking
  `e.type == ft.WindowEventType.CLOSE`. To force-quit: `await window.destroy()` then `os._exit(0)`.
- **`ElevatedButton` is deprecated** (since 0.80) → use `ft.Button` (same signature). `FilledButton /
  OutlinedButton / TextButton / IconButton` are fine.
- **Button color params differ by type**: `Button`/`FilledButton` take `color` + `bgcolor` directly.
  `OutlinedButton`/`TextButton` take **`icon_color` + `style` only** (NO `color`/`bgcolor` — passing
  `color=` raises `TypeError`). For their text color use `style=ft.ButtonStyle(color=…)`. `IconButton`
  uses `icon_color`. (Signal colors live on the action buttons — green add/save, red delete, blue edit.)
- **Tabs** = `ft.Tabs(content=ft.Column([ft.TabBar(tabs=[ft.Tab(label=..)]), ft.TabBarView(controls=[..])]),
  length=N, selected_index=, on_change=)`. Statistics renders each tab's chart lazily into its own host.
- **DatePicker**: a `DialogControl`; open via `page.show_dialog`; `on_change` → `e.control.value` is a
  **UTC-shifted datetime** → round to nearest day (`statistics_view._normalize_picked_date`: `+12h`).
- **`expand=True` is ILLEGAL inside a `wrap=True` Row** (Expanded-in-Wrap) → renders a big grey error
  box. Never mix them.
- `ft.ListView` is virtualized (use for large lists). `ft.Padding(l,t,r,b)`, `ft.Alignment(x,y)`,
  image fit `ft.BoxFit.CONTAIN` (NOT `ft.ImageFit`), `ft.PopupMenuItem(content=ft.Text(..))` (no `text=`).

## 4. ui_flet/ file map
- `app.py` — shell: toolbar (Save/Open/SaveAs/Import, IGDB settings, **watcher toggle**, watcher
  settings, Help popup, theme toggle), NavigationRail (Games/Summary/Statistics), responsive table
  width, **starts watcher + tray**, close-to-tray.
- `theme.py` — theme + status colors. `games_view.py` — Games List (search, sort, pagination,
  `#` col, status-cell quick-change, row→Game Hub). `game_dialog.py` — add/edit, delete confirm,
  `open_status_dialog`. `game_hub.py` — detail view (cover/IGDB display+remove, **inline live timer**,
  sessions/status tables, Edit/Add-session/Rate/View-Statistics/Delete). `session_dialogs.py` —
  manual session, feedback, `open_session_actions_dialog` (view/edit/remove feedback, delete session),
  `open_activity_log_dialog` (journal). `summary_view.py` — 5 charts + total play time.
  `statistics_view.py` — stat cards, **filterable game ListView** scope, contributions heatmap (native,
  year picker, click-day→date-activity, Today/Yesterday/Pick-date), rating comparison, sessions/status
  tables, **tabbed charts** (timeline/distribution/status/gaming-heatmap, scope follows selection).
  `igdb_view.py` — IGDB settings dialog. `watcher_view.py` — watcher settings dialog.
  `help_view.py` — About/User Guide/etc. `watcher_runtime.py` — **watcher↔Flet bridge** (§5).

## 5. Watcher integration (done, 3A/3B/3C)
- `process_watcher` runs in its own thread, emits via `obj.write_event_value(key,payload)`.
  `notifications.py` posts toast-button clicks the same way (set via `notifications.bind_window(obj)`).
- `ui_flet/watcher_runtime.py`:
  - `FletWatcherSink.write_event_value` → `page.run_task(self._dispatch, key, payload)` (thread→loop).
  - `_dispatch`: `-TRAY-ACTION-` → tray handler (open/quit/pause/resume/start/stop/console);
    `-TOAST-ACTION- rate` → opens Flet feedback dialog + attaches to last session; everything else →
    `bridge.handle_event(...)`; refresh games list on `session_added`/`watcher_session_started`.
  - `FletWatcherNotifier`: `notify`(SnackBar)/`info`/`focus`(window props).
  - `start_watcher(page, service, refresh_cb)`: builds bridge + sink, `bind_window`, `initialize_watcher`,
    idle hooks, **system tray** (`tray_icon.initialize_tray` with the same sink). Returns the sink.
- `session_watcher_bridge.py` got an optional **`notifier`** param: Flet path routes
  `_focus_main_window` / discard-message / `_show_simple_info` through it; **PySimpleGUI is the fallback
  when `notifier is None`** (legacy app unchanged). detect/end/pause/status are already sg-free.
- **Quit** awaits `window.destroy()` then `os._exit(0)`. Close-to-tray shows a one-time toast
  (`notifications.notify_info`, gated by config `tray_close_hint_shown`).
- **3C interactive dialogs (done)**: the three watcher dialogs are ported to Flet in
  `ui_flet/watcher_dialogs.py` (searchable virtualized ListView game picker, like the Statistics
  scope selector). The GUI-free decision logic lives in `SessionWatcherBridge` helper pairs
  `get_remap_context`/`apply_remap_decision`, `get_match_pick_context`/`apply_match_pick_decision`,
  `get_orphan_recovery_context`/`apply_orphan_recovery`; the **legacy sg dialogs now delegate to the
  same helpers** (single source of truth). The sink (`FletWatcherSink._handle_toast_action`)
  intercepts the **Wrong game? (`remap`)** and **Pick another (`pick`)** toast actions → Flet
  dialogs; `confirm`/`ignore`/`discard`/`dismiss` stay on the GUI-free bridge path. Unresolved
  ambiguous matches re-fire their OS toast on window **FOCUS** (`drain_pending_matches_on_focus`,
  wired in `app.main`'s `window.on_event`). **Crash orphan-recovery** runs at startup via
  `app.main`'s `_post_startup` (`page.run_task`).

## 6. sg leaks in the new UI — RESOLVED (Phase 5 complete; legacy UI deleted)
> NB: the "re-exported from `session_management` for the legacy UI" notes below are historical —
> `session_management.py` was **deleted** in step 3. The canonical homes are now
> `session_visualizations.py` (charts/heatmap) and `session_data.py` (migrations).
- **RESOLVED (Phase 5 step 1)**: `create_session_heatmap` was **relocated** from `session_management.py`
  (imports PySimpleGUI) → GUI-free **`session_visualizations.py`** (re-exported from `session_management`
  for the legacy UI). `statistics_view.py` now imports it at top-level from `session_visualizations`.
  Verified: importing `session_visualizations` / `ui_flet.statistics_view` no longer pulls sg. The gaming
  heatmap now renders in the packaged build. See §10.
- **RESOLVED (Phase 5 step 2)**: the session-format migrations (`migrate_all_game_sessions`,
  `migrate_session_to_unified_feedback`, `migrate_pauses_to_integrated_structure`) were **relocated** from
  `session_management.py` → the GUI-free **`session_data.py`** (re-exported from `session_management` for the
  legacy UI). `core/services.py` now imports `migrate_all_game_sessions` at **top level** from `session_data`
  (the old lazy/guarded import into the sg-importing `session_management` is gone). **Verified end-to-end**:
  importing `session_data` / `core.services` / **`ui_flet.app`** no longer pulls in PySimpleGUI — the entire
  Flet UI import chain is now sg-free. This was the last `core`/`ui_flet`→sg path.
- **DONE (Phase 5 step 3)**: `create_github_contributions_canvas` was deleted along with
  `session_management.py` (legacy-only; the Flet UI uses its own native contributions grid). No `core`/`ui_flet`
  module imports PySimpleGUI at any level anymore; `PySimpleGUI` is no longer a dependency.

## 7. Status
- **Done**: Phase 0 (foundation), Phase 1 (Games List), Phase 2 (all screens, parity audit + all gaps
  closed), Phase 3A (watcher runtime), Phase 3B (tray + close-to-tray), **Phase 3C (watcher interactive
  dialogs → Flet: match-picker, remap, crash orphan-recovery — see §5)**, **Phase 3D (Discord — see
  below)**, **Phase 3E (auto-updater UI — see below)**, **Phase 3F (IGDB enrichment/match-picker +
  Game Hub Re-fetch/Change Match + Rescan — see below)**, plus the single-instance guard (§2) and many
  Flet-API bug fixes. **Phase 3 is complete.**
- **3D Discord (done)**: `ui_flet/discord_runtime.py` owns the Flet lifecycle. **Threading is critical**:
  pypresence's sync `Presence` uses `run_until_complete`, which raises *"Cannot run the event loop while
  another loop is running"* if called on the thread with the **running Flet asyncio loop** — and in the
  Flet app every caller (tab handlers AND watcher events, marshalled via `page.run_task`) is on that
  thread. So discord_runtime funnels **all** pypresence ops onto a single dedicated **`discord-worker`**
  daemon thread (a `queue.Queue` drained by one thread; also serializes the non-thread-safe socket). The
  watcher reaches it through `provider()` → `_DiscordProxy`, a thread-confining proxy wired as the
  bridge's `discord_provider` (was `lambda: None`). `start_discord` (init+browsing), `set_enabled`
  (toggle+persist `discord_enabled`), `notify_tab` (per-tab browsing+counts) all `_submit` to the worker;
  `shutdown(timeout)` runs `cleanup_discord` on the worker and waits so the presence clear flushes before
  `_quit_app`'s `os._exit`. Toolbar **Discord toggle** = `ft.Icons.DISCORD` (greyed when off). **NB**:
  `constants.DISCORD_CLIENT_ID` is a placeholder, so presence won't actually show until a real Discord app
  id is set; all calls degrade gracefully. **Tray refresh**: `FletWatcherSink._dispatch` now calls
  `tray.refresh()` on `-WATCHER-STATUS-/-PROCESS-DETECTED-/-PROCESS-ENDED-/-WATCHER-IDLE-PAUSE-` and after
  tray actions, so e.g. a console session started from the tray enables "Stop Current Session" (mirrors
  legacy `main.py`).
- **3E auto-updater UI (done)**: `ui_flet/update_view.py` ports `update_ui.py`. Toolbar **Updates** popup
  (`ft.Icons.SYSTEM_UPDATE`) → **Check for Updates** (`check_for_updates_manual`: checking spinner →
  notification or "no updates") + **Update Settings** (`open_update_settings_dialog`: check-on-startup
  toggle + Check now / Open downloads / Clear downloads). The notification renders release notes via
  **`ft.Markdown`** (GITHUB_WEB; links open with `webbrowser.open`). Download/stage run on daemon threads
  with progress callbacks marshalled via `page.run_task`; `_do_restart` tears down watcher/tray/discord
  then calls `auto_updater.restart_application()` + `os._exit`. `app.main`'s `_post_startup` calls
  `update_view.startup_check` (success popup + auto-check when enabled). **NB**: the actual file-replace +
  relaunch only fully works in a **packaged build**; from source the final install step stages against the
  repo. Backend `auto_updater.py` untouched.
- **3F IGDB (done)**: the GUI-free IGDB business logic (search / confidence-ranking / auto-match /
  details / cover caching) was **relocated from `igdb_ui.py` into `core/igdb_logic.py`** (single source
  of truth); `igdb_ui.py` now re-imports it under the old underscore names so the legacy UI still runs.
  New `ui_flet/igdb_match.py`: **`open_match_picker`** (search → confidence-ranked candidate list →
  re-search/select/skip → download details+cover → write row[10]); **`refetch_metadata`** (re-pull
  details for an already-matched game, in place); **`open_enrich_library`** (batch wizard: off-thread
  search, auto-apply strong matches when `igdb_auto_match_on_add`, queue ambiguous ones for an
  end-of-run review chained through the picker); **`rescan_game_libraries`** (store-manifest rescan
  off-thread). Game Hub gained **Re-fetch / Change match** (in the IGDB panel) + a **Fetch metadata**
  action button shown only when no real match exists. Toolbar **IGDB popup** (`ft.Icons.CLOUD_SYNC`):
  Settings / Enrich Library / Rescan. A user "skip" stores `row[10] = {'_skipped': True}` so enrichment
  won't re-prompt. All network on daemon threads, marshalled via `page.run_task`.
- **Phase 4 packaging (Windows DONE; Linux ready to run on a Linux host)**: config in `pyproject.toml`
  (`tool.flet.app.module = app_flet` so the build packages the Flet entry, NOT legacy `main.py`;
  build-scoped `[project.dependencies]` excludes PySimpleGUI). Build via `scripts/build_windows.ps1` /
  `scripts/build_linux.sh`; full guide in **`docs/BUILD.md`**. Prereqs: Flutter SDK on PATH (`flet build`
  auto-downloads it on first run; here it landed at `C:\Users\Tobias\flutter\3.41.7`), + VS2019/22 C++
  on Windows / clang+cmake+ninja+GTK3 on Linux. **Three gotchas — all fixed (see docs/BUILD.md
  Troubleshooting for the gory detail):** (1) **Never pass `--arch`** for desktop — it's macOS/Android
  only but flet forwards it to serious_python, whose Win/Linux arch key is `""`; `--arch x64` makes its
  per-arch install loop skip the only entry → **ZERO deps installed** → packaged app crashes with
  `ModuleNotFoundError: certifi/requests/...`. A correct build shows a multi-minute "Installing ... with
  pip" step and a populated `build/site-packages`. (2) **No eager `import tkinter`/`PySimpleGUI`** in any
  module the Flet UI imports — the embedded CPython has no tkinter; `utilities.py` now guards its import
  (legacy table-width/popup helpers fall back). (3) **`tool.flet.app.exclude`** keeps `.venv/.git/build`
  etc. out of `app.zip` (else serious_python bundles the whole repo). Build mechanics: rich console
  crashes on cp1252 stdout when redirected → use `TERM=dumb PYTHONIOENCODING=utf-8 --no-rich-output`;
  `--yes` auto-accepts the Flutter-SDK install prompt. **Windows VERIFIED end-to-end**: from-scratch
  `build_windows.ps1` → `build/windows/GameTracker.exe` (embedded CPython 3.12), launches, loads the
  saved layout, and runs with no traceback. **Linux/ARM64 still need to be run on the target host** (no
  cross-compile) — the script is ready and carries the same `--arch`-free invocation.
  - **Phase 5** cleanup (sg/legacy removal). **Steps 1–5 DONE; only step 6 (rebuild) remains.**
    - **Step 1**: heatmap relocation → GUI-free `session_visualizations.py` (§6, §10).
    - **Step 2**: session-migration relocation → GUI-free `session_data.py` + `core/services.py` top-level
      import (§6).
    - **Step 4**: bridge PySimpleGUI fallbacks dropped + legacy-only sg dialog methods deleted; `auto_updater`
      no longer imports `update_ui`'s success popup. (Commit `da8e789`.)
    - **Step 3**: the 16 legacy UI modules deleted (`main.py`, `event_handlers.py`, `ui_components.py`,
      `game_statistics.py`, `game_hub.py`, `igdb_ui.py`, `session_ui.py`, `session_display.py`,
      `session_management.py`, `update_ui.py`, `help_dialogs.py`, `process_watcher_settings.py`, `ratings.py`,
      `date_activity_view.py`, `watcher_link_dialog.py`, `emoji_utils.py`) + the orphaned cx_Freeze `setup.py`.
      `create_github_contributions_canvas` went away with `session_management` (legacy-only). (Commit `e19f668`.)
      Verified: every kept module compiles, `ui_flet.app`/`core.services` import sg-free, and a headless
      `app_flet.py` launch starts clean (watcher + store scan, no traceback).
    - **Step 5**: `PySimpleGUI` removed from `requirements.txt`; `pyproject.toml` sg comments refreshed; README
      rewritten (Flet install + `flet build`, no more PySimpleGUI/cx_Freeze/`main.py`).
    - **Step 6 (REMAINING)**: rebuild the Windows package via `scripts/build_windows.ps1` and do a final
      from-packaged smoke test. Gate on `docs/VERSION2_PARITY_AUDIT.md`. The legacy 1.11.x→2.0.0 auto-update
      pipeline (§10) must keep working — don't break `auto_updater.py`'s install-target/cleanup logic.

## 8. Verification workflow (reuse it)
- **Headless tests** were kept in `C:\Users\Tobias\AppData\Local\Temp\claude\` (EPHEMERAL — recreate as
  needed): `v2_smoke.py`, `v2_startup.py` (MockPage drives `main()`), `v2_view_test.py`,
  `v2_stats_test.py`, `v2_watcher_test.py`, `gamehub_smoke.py`. Patterns: construct controls with
  `page=None` (mounted-guard skips updates); `MockPage`/`StackPage` (dialog stack) for dialog flows;
  fake event objects (`types.SimpleNamespace(column_index=, ascending=)`). In watcher tests, monkeypatch
  `notifications.notify_*`, `session_watcher_bridge.save_data`, and `config.save_config` to no-ops to
  avoid real toasts / disk / config writes. (Consider moving these into a repo `tests/` dir.)
- **⚠ CRITICAL — never let a test write the real config.** A headless test that builds a `FakeService`
  with a minimal `config` dict and lets it reach `save_config()` will **silently overwrite the user's real
  `%APPDATA%\GamesListManager\config.json`** (it writes the FIXED `get_config_file()` path), wiping
  `last_file`, IGDB creds, and watcher mappings. This actually happened this session (clobbered the D:
  `.gmd` path). In ANY test that could reach config I/O, **monkeypatch `config.get_config_file` to a temp
  path** (and/or stub `save_config`/`load_config`) — never rely on the fake dict alone.
- **Live launch**: run `app_flet.py` in background, `Start-Sleep ~10`, read the task output file
  (empty == clean startup), then kill via
  `Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*app_flet.py*' } | % { Stop-Process -Id $_.ProcessId -Force }`.
  The user validates GUI behavior (tabs, tray, real-game watcher detection) — those can't be auto-tested.

## 9. Git
Branch `Version2` (**do not push** — kept local). The §10 work is now **committed** in logical chunks on
top of `18a095f` ("fixed quite a few issues with the updater" — the updater pipeline / `auto_updater.py` /
`pyproject.toml`, user's manual commit):
- `42c297f` — Phase 5 step 1: heatmap relocation (`session_management.py` → `session_visualizations.py`).
- `33adcad` — watcher fixes (`process_watcher.py`, `session_watcher_bridge.py`, `ui_flet/watcher_*`).
- `05139c4` — pre-2.0.0 cx_Freeze leftover cleanup (`legacy_cleanup.py` + `ui_flet/update_view.py`).
- `bc2a771` — UI/UX polish (`ui_flet/{app,loading,games_view,statistics_view,summary_view,game_hub,
  game_dialog,igdb_view,session_dialogs}.py`).
Earlier key commits: `9b25f60` (3C watcher dialogs), `835a07f` (3B tray Quit/close-to-tray),
`c1cd797` (3A watcher), `94a8cae` (3B tray), `f86478b` (parity gaps closed), `f592cf6` (audit doc),
`e3e2ee9` (foundation). `main`/`master` carry the two pre-migration bug fixes; `Version2` branched off `main`.

**Intentionally NOT committed:** `constants.py` (local table-color tweaks — kept out of history at the
user's request; still shows as modified). **Throwaway dev helpers (do NOT commit, gitignore or delete):**
`test_update_ui.py`, `make_fake_update.py` (already in `tool.flet.app.exclude`); `.claude/` is local agent
config.

## 10. Latest session — updater 1.11.x→2.0.0 pipeline, UI/UX polish, Phase 5 step 1
All uncommitted (see §9). `auto_updater.py` is **no longer "untouched"** — backend list in §2 is stale for it.

### A. Auto-updater: legacy(cx_Freeze/PySimpleGUI)→Flet upgrade — hardened & VERIFIED end-to-end
Shipped v1.11.3 is a **cx_Freeze / Python 3.10** build (`lib/`, `share/`, `python310.dll`, exe at zip root);
v2.0.0 is **Flet/serious_python / Python 3.12** (`Lib/`, `DLLs/`, `data/`, `site-packages/`, `python312.dll`).
The 1.11.3 updater (already shipped, immutable) relaunches the new exe; the new exe then owns Flet→Flet.
Fixes (all in `auto_updater.py` unless noted):
- **`_resolve_install_target()` / `_process_image_path()`** (Win32 `GetModuleFileNameW(NULL)`): the Flet build
  sets neither `sys.frozen` nor a usable `sys.executable`, so the old code relaunched `main.py`. Now resolves
  the real `GameTracker.exe` for packaged Flet builds (via process image + Flet markers), classic frozen
  builds, and source (→ `app_flet.py`, never legacy `main.py`). `_serious_python_extract_dir()` finds the
  `…\flet\app` extraction dir via its `.hash` file + path shape.
- **serious_python re-extract / white-screen `PathAccessException`**: on relaunch the runtime re-extracts
  `app.zip` into `%APPDATA%\DrNefarius\GameTracker\flet\app` and crashed because the dir was locked. The
  generated `updater.ps1` now: runs with OS cwd moved out via `[System.IO.Directory]::SetCurrentDirectory($env:TEMP)`
  (**PowerShell `Set-Location` does NOT move the OS process cwd** — that was the root cause); the Python side
  Popen-launches it with `cwd=config_dir`; **kills stale `GameTracker.exe` zombies** (a failed extract leaves a
  white-window process holding the dir as cwd — the single-instance guard can't catch it because Python never
  starts); then **retry-deletes** the extraction dir.
- **Blank window after relaunch (theme paints, zero controls)**: the dying app's per-launch env
  (`FLET_SERVER_PORT`, `FLET_PYTHON_CALLBACK_SOCKET_ADDR`, `PYTHONINSPECT=1`, `FLET_APP_CONSOLE`, …) leaked
  dying-app → updater → new-app, so the new app reused the dead port/socket. Updater now **strips `FLET_*` +
  `PYTHONINSPECT`** before relaunch.
- **Console tether**: serious_python's `AttachConsole(ATTACH_PARENT_PROCESS)` bound the relaunched app to the
  updater's console window. Updater now launches via **`explorer.exe "<path>"`** (parent = console-less shell,
  clean env) — like a double-click. (Verified the alternatives DON'T detach: `[Process]::Start(UseShellExecute)`
  tethers *more*; plain `Start-Process` still lets `AttachConsole` bind the parent console.)
- **Logging**: `%APPDATA%\GamesListManager\update_log.txt` (`_log_update()`, Python staging) + `updater_log.txt`
  (PowerShell `Start-Transcript`).
- **Legacy-leftover cleanup — new `legacy_cleanup.py`**: removes ~114 MB of pre-2.0.0 cx_Freeze files (whole
  `share/`, `python310.dll`, 56 `lib/` package dirs, 31 `lib/` files — derived by diffing a fresh cx_Freeze
  build vs the Flet build; excludes `gameslisticon.ico`; never touches Flet's `Lib/`). `cleanup_legacy_files()`
  + `maybe_cleanup_after_upgrade(previous_version)` run off-thread from `update_view.startup_check` when the
  upgrade marker's `previous_version` < 2.0.0 in a packaged Flet build. Verified: removes the leftovers, 0
  Flet files touched.
- **Release-notes images** (`update_view.py`): GitHub `<img …>` HTML is rewritten to Markdown (`_notes_to_markdown()`)
  so `ft.Markdown` renders them (it doesn't parse raw HTML); added `image_error_content`.
- **Packaging NB for the real v2.0.0 release**: the asset must be a `.zip` with `GameTracker.exe` at the zip
  ROOT + the Flet support files (so the relaunch target exists); robocopy stages with `/E` (merge, NO purge),
  so `legacy_cleanup.py` is what removes the orphaned legacy files. `releases/latest` ignores drafts/prereleases.

### B. Phase 5 — step 1 DONE (chart relocation). See §6.
`create_session_heatmap` → `session_visualizations.py` (GUI-free), re-exported for legacy; `statistics_view`
imports it from there. The gaming heatmap now works in the packaged build. Remaining Phase 5 steps in §7.

### C. Watcher fixes (`session_watcher_bridge.py`, `process_watcher.py`, `ui_flet/watcher_*`)
- **Ambiguous-match toast endless loop** (`notify_match_confirmation`, weak/best-guess candidates): each poll of
  the same exe created a new pending entry (fresh uuid) → all re-fired on window focus, and the toast↔focus
  interaction looped while GameTracker was focused. Fixed: `_on_match_ambiguous` **dedupes by exe** (reuse the
  detection_id, no re-toast if already pending); `drain_pending_matches_on_focus` **rate-limits** re-fire
  (`_REFIRE_COOLDOWN_SEC = 60`, `_last_refire`) and **skips exes with an open picker** (`_active_pickers` set
  via `begin_match_pick`/`cancel_match_pick` — `begin` is called in `watcher_runtime._handle_toast_action`
  BEFORE `notifier.focus()`, `cancel` from the picker's Cancel/`_on_cancel`).
- **Immediate tracking on resolve**: picking/confirming an ambiguous match used to only save a mapping ("tracked
  after restart"). New **`ProcessWatcher.force_track_exe(exe_path, game_name)`** finds the running pid and, under
  `self._lock`, builds a `_Candidate` + calls `_start_session` NOW (bypasses the 10 s debounce). Wired into BOTH
  `confirm_pending_match('confirm')` and `apply_match_pick_decision(chosen)` (the `notify_match_confirmation`
  path specifically), with fallback to `recheck_exe`/`recheck_install_dir` if the process already exited, and
  message "Now tracking X." (Remap of an existing strong mapping already worked.)
- **Ignore-list staleness**: a toast-added ignore wrote the config FILE but not in-memory `service.config`, so it
  only appeared after restart. `open_watcher_settings_dialog` now does `service.config.update(load_config())` on
  open (also stops Save from clobbering watcher-side writes).

### D. UI/UX enhancements (all `ui_flet/`; tasks #1–#14 + follow-ups, all verified)
- **Toolbar** redesigned into labeled **pill** PopupMenuButtons via a local `_pill()`/`_pill_bg()`: **File**
  (Save/Open/Save As/Import/**Quit GameTracker** → sink `-TRAY-ACTION- quit`), **Library** (IGDB Settings/Enrich/
  **Remember filter,page&rows** toggle), **Watcher** (Enabled toggle/Settings/**Rescan** moved here), **Discord**
  toggle, **Updates**, **Help**, theme. **Enabled-state accents**: Discord = blue (`_DISCORD_ACCENT`), Watcher =
  green (`_WATCHER_ACCENT`). A **DB-name chip** (`_refresh_db_label()`) shows the loaded `.gmd`; window title too.
- **Startup splash + lazy charts** (fixes multi-second blank window): `main()` is a thin wrapper that paints a
  centered spinner (via `page.vertical/horizontal_alignment`) then defers the heavy build to `_build_main` through
  `page.run_task(_go)` after a one-loop-yield sleep. `SummaryView`/`StatisticsView` no longer build charts in
  `__init__` — lazy on first nav, wrapped in the loading overlay.
- **Window geometry persistence**: size/position/maximized saved (debounced `_schedule_geom_save`/`_capture_geometry`)
  on move/resize/maximize via `window.on_event`, restored in `main()` (config `window_width/height/left/top/maximized`,
  bounds-checked via `_num()`).
- **`ui_flet/loading.py` (new)**: global translucent overlay `show_loading`/`hide_loading`/`run_with_loading(page,
  msg, work, *args)` — used on Summary/Statistics nav, Open/Import, View-Statistics, and large table page changes.
- **Games list** (`games_view.py`): persists sort (`games_sort_col`/`_asc`, always) + **opt-in** filter/page/rows
  (`remember_library_view`, toggled from the Library menu, restores `library_query`/`library_page_size`/
  `library_page_index`); page-change loading overlay for large pages; edit icon blue; Add-game button green.
- **Statistics** (`statistics_view.py`): prominent **selected-game header** (`_render_game_header()`: cover via
  `cover_path_for` + name + metadata chips + genres/summary); **Sessions & Status-history side-by-side**
  (`ResponsiveRow` md=7/md=5); **Fetch metadata** button when the game is unmatched (→ `igdb_match.open_match_picker`,
  `on_done=self.refresh`); dropdown labels rendered as external `_labeled()` text (the floating `label=` was clipped);
  lazy (removed the `__init__` `self.refresh()`); Add-session button green.
- **Game Hub** (`game_hub.py`): redundant Sessions & Status-history tables **removed** (dialog 640→560).
- **Signal button colors** across hub/dialogs: green add/save, red delete/remove, blue edit. **GOTCHA fixed**
  (see §3): `OutlinedButton`/`TextButton` reject `color=`/`bgcolor=` (`TypeError`) → use `icon_color=` +
  `style=ft.ButtonStyle(color=…)`; only `Button`/`FilledButton` take `color`/`bgcolor`.
- **#2 heatmap unicode crash**: `session_management.py` (~L114) was a non-raw f-string with an invalid `\s` escape
  next to literal `★☆`; the SyntaxWarning's cp1252 re-encode crashed → fixed to a raw string.
