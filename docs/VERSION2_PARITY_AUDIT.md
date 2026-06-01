# Version2 (Flet) — Feature Parity Audit vs. legacy PySimpleGUI UI

Audit of every user-facing feature in the legacy UI (`main.py` event loop,
`ui_components.py` layout/menu, `event_handlers.py`, `game_hub.py`, `*_ui.py`,
`date_activity_view.py`, `session_*`, `ratings.py`, `tray_icon.py`) against the
Flet port under `ui_flet/`.

Legend: ✅ ported · ❌ missing (Phase-2 gap to fix) · 🔶 deferred to Phase 3 (correct) · ⚠️ partial

---

## File menu
| Feature | Status | Notes |
|---|---|---|
| Open `.gmd` | ✅ | Toolbar "Open" |
| Save / Save As | ✅ | Toolbar "Save" / "Save As" |
| Import from Excel | ✅ | Toolbar "Import Excel" |
| Exit | ✅ | Window close |

## View menu
| Feature | Status | Notes |
|---|---|---|
| View Activity by Date (date picker) | ✅ | Statistics → "View date activity…" (ft.DatePicker) |
| Today's Activity | ❌ | Quick menu item; reachable via picker but no 1-click entry |
| Yesterday's Activity | ❌ | Quick menu item; reachable via picker but no 1-click entry |

## Options menu
| Feature | Status | Notes |
|---|---|---|
| IGDB Settings | ✅ | Toolbar button |
| Process Watcher Settings | ✅ | Toolbar button |
| Discord enable/disable toggle | ✅ | **3D** toolbar toggle (`discord_enabled`); see App-wide row |
| Process Watcher On/Off toggle | 🔶 | Phase 3 (watcher) |
| Rescan Game Libraries | ✅ | **3F** IGDB menu → Rescan (`igdb_match.rescan_game_libraries`) |
| Enrich Library from IGDB | ✅ | **3F** IGDB menu → Enrich (`igdb_match.open_enrich_library`) |
| Check for Updates / Update Settings | ✅ | **3E** toolbar Updates menu (`ui_flet/update_view.py`) |

## Help menu
| Feature | Status |
|---|---|
| User Guide, Feature Tour, Data Format, Troubleshooting, Release Notes, Report Bug, About | ✅ (Help menu) |

## Games List tab
| Feature | Status | Notes |
|---|---|---|
| Search / filter | ✅ | Search box (live filter) |
| Reset selection/search | ✅ | Clear search field |
| Add Entry | ✅ | "Add game" |
| Edit / Delete game | ✅ | Row → Game Hub (Edit/Delete), + row delete icon |
| Column header sort | ✅ | `DataColumn.on_sort` |
| Open game (double-click → actions) | ✅ | Row click → Game Hub |
| Row colours by status | ✅ | Row tint |
| Pagination + full-width table | ✅ | New (better than legacy) |
| **Status-column click → quick "Change Status"** | ❌ | `handle_status_change`: click the Status cell to change status without opening the editor |
| Row numbers (`display_row_numbers`) | ❌ | Cosmetic only |

## Summary tab
| Feature | Status | Notes |
|---|---|---|
| Status pie, Top-games playtime, Year, Rating, Genre charts | ✅ | `update_summary_charts` PNGs |
| Refresh Charts | ✅ | |
| **"Total Play Time" header text** | ❌ | Minor: legacy `-TOTAL-TIME-` |

## Statistics tab
| Feature | Status | Notes |
|---|---|---|
| Overall stats (sessions/time/avg/most-active) | ✅ | Stat cards |
| Game scope picker — searchable | ✅ | `Dropdown(editable, enable_filter)` |
| "All games" / Show All Games | ✅ | "All games" option |
| Rating comparison (auto vs manual, tags, comment) | ✅ | |
| Sessions table | ✅ | |
| **Sessions interactive (view / edit / delete)** | ✅ | Row tap → session-actions popup |
| Status-history table | ✅ | |
| Contributions calendar | ✅ | Native heatmap grid |
| Contributions **year picker** | ✅ | "Period" dropdown |
| Date-activity (click a day, Prev/Next day) | ✅ | + "View date activity…" picker |
| Session Timeline chart | ✅ | |
| Session Distribution chart + **Line/Scatter/Box/Histogram** | ✅ | |
| Status Timeline chart | ✅ | |
| **"Gaming Heatmap" chart** (`create_session_heatmap`) | ❌ | Distinct from the contributions calendar; has window size (1/3/6/12 mo) + Prev/Next/Latest/Most-active navigation |
| **"Add Session" button (per selected game)** | ❌ | Exists in Game Hub, but legacy also had it on the Statistics tab |
| **"View Activity Log"** (`display_all_game_notes`) | ⚠️ | Chronological all-notes+status popup; Flet shows feedback inline + per-session. Not a 1:1 dedicated view |
| Comments **word cloud** (`create_comments_word_cloud_visualization`) | ⚠️ | Generator exists; verify whether/where the legacy actually surfaces it |

## Game Hub
| Feature | Status | Notes |
|---|---|---|
| Cover art + IGDB metadata (genres, summary, time-to-beat) | ✅ | Display only |
| Remove metadata | ✅ | |
| Inline timer Play/Pause/Stop (+ **live count-up**) | ✅ | Async ticker |
| Sessions table (interactive) + status history | ✅ | Row tap → session-actions |
| Edit / Add session / Rate / Delete | ✅ | |
| View Statistics | ✅ | Switches to Statistics tab + selects game |
| **IGDB Re-fetch / Change Match** | ✅ | **3F** Game Hub IGDB panel (`igdb_match.open_match_picker` / `refetch_metadata`) + Fetch metadata when unmatched |
| **Link Executable** | ✅ | Game Hub "Link executable" (`watcher_dialogs.open_link_executable_dialog`): browse .exe + exe/install-dir scope, console-platform guard |

## App-wide / background
| Feature | Status | Notes |
|---|---|---|
| Light/Dark/System theme toggle | ✅ | New |
| System tray icon (`tray_icon.py`) | 🔶 | Phase 3 |
| Process watcher (detection, toasts, match-confirm) | ✅ | 3A detection/toasts; **3C** match-confirm + remap + crash recovery as Flet dialogs |
| Discord Rich Presence | ✅ | **3D** `ui_flet/discord_runtime.py`: startup init (daemon thread), watcher-driven playing/paused/complete, per-tab browsing, cleanup on quit. (Real presence needs a non-placeholder `DISCORD_CLIENT_ID`.) |
| Auto-updater + update notifications | ✅ | **3E** `ui_flet/update_view.py`: notification (markdown notes), download/stage progress, install confirm, settings, success popup, startup check. (Full install+relaunch is a packaged-build concern.) |
| Idle detection | 🔶 | Phase 3 |

---

## Phase-2 gaps — STATUS (all closed)
1. ✅ Games list **Status-cell quick-change** popup (click the Status badge).
2. ✅ Statistics **"Gaming Heatmap" chart** + window-size (1/3/6/12 mo) & Prev/Next/Latest/Most-active nav.
3. ✅ Statistics **"Add Session"** button for the selected game.
4. ✅ Statistics **"View Activity Log"** — journal-style popup (all notes + status changes, chronological,
   readable text). Inline/per-session feedback kept too.
5. ⛔ **Word cloud** — dropped (confirmed unused in the legacy UI).
6. ✅ Summary **"Total Play Time"** text.
7. ✅ View menu **Today's / Yesterday's Activity** quick entries (Statistics contributions header).
8. ✅ Games list **row numbers** ("#" column).

### Phase-5 cleanup carried from these fixes
- Relocate `create_session_heatmap` (and `create_github_contributions_canvas`) out of
  `session_management.py` (which imports PySimpleGUI) into a GUI-free chart module. The new UI
  currently reaches `create_session_heatmap` via a localized lazy import; this is the only place
  `ui_flet` touches an sg-importing module, and it must be cut before sg is removed.

## Phase 3 — DONE
Discord toggle/presence (3D) · Watcher on/off + Rescan (3F) + detection/toasts/match-confirm (3A/3C) ·
IGDB enrichment + match-picker + Re-fetch/Change Match (3F) · Check for Updates / Update Settings /
update UI (3E) · system tray (3B).

### Still open (carried past Phase 3)
- (none) — **Link Executable** is now done: Game Hub → "Link executable" opens
  `watcher_dialogs.open_link_executable_dialog` (browse .exe, exe-vs-install-dir scope,
  console-platform guard, persists via `apply_link_executable`).
