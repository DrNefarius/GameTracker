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
- **Backend = untouched & reused** (no GUI imports): `data_management`, `session_data`,
  `config`, `constants`, `process_watcher`, `notifications` (OS toasts), `igdb_integration`,
  `auto_updater`, `discord_integration`, `store_manifests`, `pause_utils`, `idle_detection`,
  `utilities`, `visualizations`, `session_visualizations`, `tray_icon`, `watcher_log`.
- **`core/` = UI-agnostic facade** (no GUI imports):
  - `services.py` → `GameLibraryService`: `.data` (list of `(orig_idx, row)`), `.filename`,
    `.config`; methods `bootstrap / open_path / import_excel / save / save_as / add_game /
    update_game / delete_game / get_game / set_status`; module fns `new_game_row`, `edited_game_row`.
  - `ratings_logic.py` → sg-free `format_rating / calculate_session_rating_average /
    get_session_rating_summary` (legacy `ratings.py` imports sg, can't be imported by the new UI).
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

## 6. The ONE sg leak in the new UI
`statistics_view._render_chart_kind("heatmap")` lazy-imports `create_session_heatmap` from
`session_management.py` (which imports PySimpleGUI). **Phase 5 must relocate `create_session_heatmap`
AND `create_github_contributions_canvas` to a GUI-free module** before sg is removed. Tracked in the audit.

## 7. Status
- **Done**: Phase 0 (foundation), Phase 1 (Games List), Phase 2 (all screens, parity audit + all gaps
  closed), Phase 3A (watcher runtime), Phase 3B (tray + close-to-tray), **Phase 3C (watcher interactive
  dialogs → Flet: match-picker, remap, crash orphan-recovery — see §5)**, plus many Flet-API bug fixes.
- **Remaining**:
  - **3D** Discord: `initialize_discord` at startup; pass `get_discord_integration` as the bridge's
    `discord_provider` (currently `lambda: None`); enable/disable toggle (config `discord_enabled`).
  - **3E** auto-updater UI: port `update_ui.py` (Check for Updates / Update Settings / update-available
    → download → install). Backend `auto_updater.py`.
  - **3F** IGDB: enrichment wizard + match-picker (`igdb_ui.py`); Game Hub **Re-fetch / Change Match**
    (hub currently only Remove-metadata); **Rescan Game Libraries**; **Enrich Library** menu item.
  - **Phase 4** packaging: `flet build windows` (native exe) + `flet build linux` + early **ARM64**
    smoke (Flet's weakest target — build on-device, GTK deps, no cross-compile; CI matrix). **Not Nuitka.**
  - **Phase 5** cleanup: remove PySimpleGUI dep + legacy UI files (`main.py`, `ui_components.py`,
    `event_handlers.py`, `*_ui.py`, legacy `game_hub.py`, `session_display.py` UI parts,
    `date_activity_view.py`, `ratings.py` popup); relocate the two chart fns (§6); drop the bridge's sg
    fallbacks. Gate on `docs/VERSION2_PARITY_AUDIT.md`.

## 8. Verification workflow (reuse it)
- **Headless tests** were kept in `C:\Users\Tobias\AppData\Local\Temp\claude\` (EPHEMERAL — recreate as
  needed): `v2_smoke.py`, `v2_startup.py` (MockPage drives `main()`), `v2_view_test.py`,
  `v2_stats_test.py`, `v2_watcher_test.py`, `gamehub_smoke.py`. Patterns: construct controls with
  `page=None` (mounted-guard skips updates); `MockPage`/`StackPage` (dialog stack) for dialog flows;
  fake event objects (`types.SimpleNamespace(column_index=, ascending=)`). In watcher tests, monkeypatch
  `notifications.notify_*`, `session_watcher_bridge.save_data`, and `config.save_config` to no-ops to
  avoid real toasts / disk / config writes. (Consider moving these into a repo `tests/` dir.)
- **Live launch**: run `app_flet.py` in background, `Start-Sleep ~10`, read the task output file
  (empty == clean startup), then kill via
  `Get-CimInstance Win32_Process | ? { $_.CommandLine -like '*app_flet.py*' } | % { Stop-Process -Id $_.ProcessId -Force }`.
  The user validates GUI behavior (tabs, tray, real-game watcher detection) — those can't be auto-tested.

## 9. Git
Branch `Version2` (do not push). Latest: `9b25f60` (3C watcher interactive dialogs). Earlier key
commits include `835a07f` (3B tray Quit/close-to-tray fixes), `c1cd797` (3A watcher), `94a8cae`
(3B tray), `f86478b` (parity gaps closed), `f592cf6` (audit doc), `e3e2ee9` (foundation).
`main`/`master` carry the two pre-migration bug fixes; `Version2` branched off `main`.
