# Process Watcher

The Process Watcher auto-tracks your gaming sessions on Windows by detecting
when a known game launches, attributing the session to the correct entry in
your library, and surfacing non-intrusive Windows toast notifications. When
enabled, you can stop using the manual Play/Pause/Stop timer entirely - just
launch the game and the time gets recorded.

> **Platform support.** v1 is **Windows-only**. macOS and Linux fall back to
> the existing manual timer.

## Enabling the watcher

1. **Options -> Process Watcher: Off** to flip it to **On**.
2. Optionally open **Options -> Process Watcher Settings** to tune behavior
   (idle threshold, foreground-only mode, notifications, quiet hours).
3. **Options -> Rescan Game Libraries** rebuilds the cache of installed
   Steam / Epic / GOG games. Run it after installing a new title if the
   watcher doesn't recognise it on launch.

The watcher runs on a single background thread and only **reads** the
process table - it never modifies running processes or files outside your
GamesList Manager config and data file.

## How matching works

When the watcher sees a new process, it walks the resolver pipeline:

```
exe path
  |
  +-- L1  Learned mapping  (watcher_process_map[exe_path])         fast
  +-- L1b Learned install dir  (watcher_installdir_map[prefix])
  +-- L2  Steam manifest    (-SteamAppId=NNN  or  steamapps/...)
       |  Epic manifest    (...\\EpicGamesLauncher\\Manifests\\*.item)
       |  GOG registry / Galaxy DB
  +-- L3  Fuzzy match against your library names + IGDB aliases
  +-- L4  Ask the user (toast notification)
```

**Strict mode** (default ON): the watcher only considers processes whose
executable lives under a known install root (Steam / Epic / GOG / any folder
you added to `watcher_user_roots`). This is the recommended setting because
it strictly limits what the watcher looks at and minimizes false positives.

If an exe path *looks* like a real store install (for example under
`...\steamapps\common\...`) but is not yet under any whitelisted root—often
because you launched the game right after Steam finished installing it while
GamesList was already running—the watcher runs an **opportunistic manifest
rescan** (throttled to a few seconds apart), then re-checks. If it still
cannot whitelist the path, it temporarily forgets that process ID so the next
poll treats the game as newly seen again after the index updates. You do not
need to restart the app for that case; *Options → Rescan Game Libraries* is
still available if you want to force a full refresh immediately.

### Tracking games outside the auto-discovered launchers

Plenty of titles aren't installed by Steam / Epic / GOG: standalone old
games, MMOs that ship their own installer (Guild Wars, EVE Online,
classic WoW), and indie itch.io builds. Strict mode deliberately filters
those out, so even Layer 3 fuzzy matching never sees them. Two flows let
you teach the watcher about them:

- **Link Executable (per game).** Open a game from the Games tab, click
  *Link Executable*, browse to the .exe (e.g. `D:/Games/GuildWars/Gw.exe`),
  and pick the match scope:
  - *Just this executable* - ordinary single-binary games.
  - *Any executable in this folder* - MMOs and games whose launcher .exe
    spawns a separate renderer .exe (Guild Wars 2's two binaries, games
    that update via a dedicated updater process, etc).
  The dialog persists the mapping into `watcher_process_map` (or
  `watcher_installdir_map` for folder-scope) **and** auto-adds the
  parent folder to `watcher_user_roots` so strict mode lets the path
  through. No restart needed - the watcher picks up the new mapping on
  its next tick.

- **Watch folders (whole-tree).** *Process Watcher Settings -> Watch
  folders* lets you whitelist whole directories like `D:/Games`. Any
  executable found under a watched folder becomes eligible for the
  resolver, so games that share a parent root all get discovered
  without per-title linking. Useful when you keep many unmanaged games
  on a second drive.

The same dialog also exposes *Process Watcher Settings -> Learned
mappings -> Add mapping...* for users who prefer to add associations in
bulk without opening each game's hub.

**Debounce.** A process must stay alive for **10 seconds** before a session
is started. This filters out launchers that spawn the real game and exit.

**Sibling handoff.** When a tracked process disappears, the watcher waits up
to **15 seconds** for another process under the same install directory to
appear before ending the session - this keeps "launcher.exe -> game.exe"
handoffs from creating two sessions.

## Notifications

Three categories, each toggleable independently in Process Watcher Settings:

- **Session started** - "Now tracking: \<game\>" with cover art (when IGDB
  cover is cached). Buttons: *Dismiss* (clears the toast only), *Wrong game?*,
  *Don't track this session*. The last button immediately discards the
  just-started session so it's never written to disk - use it when the
  watcher fired on something you don't want recorded.
- **Session ended** - "\<game\> - 2h 13m" with *Rate it* (opens the feedback /
  rating dialog when you next focus the app) and *Dismiss* (clears the toast
  only; does not switch to GamesList).
- **Match needed** - When the watcher detects something it can't confidently
  attribute, it asks you to confirm or ignore the executable. Buttons:
  *Yes, that's it* (commits the best guess as a learned mapping),
  *Pick another* (opens the same library picker the *Wrong game?* flow
  uses - the chosen mapping is saved as either an exe-only entry or a
  whole-folder rule, the parent folder is auto-added to your watch
  roots if needed, and the still-running process is forced through the
  resolver again so tracking begins on the next tick without you having
  to relaunch the game), and *Never for this exe* (adds the executable
  to the ignore list).

**Wrong game?** opens a small picker that lists every title in your
library. Pick the correct one and we'll **retitle the live session in
place** - the elapsed time you've already accumulated keeps counting,
just under the right title - and replace the auto-learned mapping so
the next launch of the same executable starts correctly too. No game
restart needed. The Discord rich-presence and the system-tray "Tracking:
..." line both update immediately; the start-toast in the Action Center
is replaced with a fresh one for the corrected title. *Don't track this
process* puts the executable on the ignore list and discards the live
session instead.

**Quiet hours.** If set (format `HH:MM-HH:MM`, 24h), no notifications fire
inside that window. The session is still tracked silently.

**Fullscreen and Focus Assist.** Windows' Focus Assist auto-enables a
"playing a game" rule that suppresses normal toasts whenever an app is
in fullscreen / exclusive-fullscreen - which is exactly when most start
toasts would fire. You have two options:

- **Best fix:** open Windows Settings -> System -> Notifications -> Turn
  off do not disturb automatically (older builds: System -> Focus assist)
  and disable the "When I'm playing a game" automatic rule. Toasts then
  show at their normal priority during fullscreen play.
- **In-app workaround:** enable *Process Watcher Settings -> Notifications
  -> "Show toasts even in fullscreen games (bypass Focus Assist)"*. This
  escalates the start / end / match toasts to the Reminder scenario, which
  Windows lets through Focus Assist. Trade-off: those toasts stay
  on-screen until you dismiss them rather than auto-fading after a few
  seconds. The toggle is off by default.

## Tray icon

The system-tray icon shows watcher state at a glance and exposes:

- **Tracking: \<game\> - elapsed time** (header, refreshes on each event)
- **Pause / Resume / Start Watcher**
- **Stop Current Session** (force-end the active session and persist it)
- **Start Console Session ->** quick-pick recently played console games
- **Open GamesList Manager** (default action - left click the tray icon)
- **Quit**

Disable it via **Process Watcher Settings -> System tray -> Show tray icon**.

### Manual console sessions

The **Start Console Session ->** submenu lists up to 15 console titles,
ordered to surface what you're most likely to want to launch next:

1. **Recently played and active** - status ``In progress`` or
   ``Pending`` *and* last played within the last 30 days. Within this
   group, ``In progress`` games come before ``Pending`` ones, and
   inside each status the newest play is first. Completed and Dropped
   games are deliberately *excluded* from this bucket regardless of
   how recently you played them - a game you finished last week
   shouldn't outrank an active backlog title.
2. **In progress backlog** - any other ``In progress`` console title
   (never played, or last played longer than 30 days ago), ordered by
   release date *ascending* so older games surface first.
3. **Pending backlog** - same shape as #2 but for ``Pending``.
4. **Other** - completed / dropped console titles, in library order,
   so they're still reachable from the menu.

Any entry whose platform string is anything other than ``PC`` is
treated as a console for the purposes of this menu - we don't try to
keep a list of known consoles, so PlayStation, Switch, Atari, MSX,
arcade cabinets, your homebrew system, and everything else all
qualify automatically.
Pick one and the watcher starts a *manual* session for it: same toast,
same Discord rich-presence, same tray "Tracking: ..." line, same
games-file output as a Steam session - just kicked off by your click
instead of a process appearing.

Manual sessions deliberately bypass two pieces of the auto-tracking
pipeline because neither carries useful signal when you're playing on a
different device:

- **No process-death check.** The session has `pid=0` so there's
  nothing for the watcher to look for in the live process list. It
  ends only when you click *Stop Current Session* on the tray (or
  *Don't track this session* on the start toast within a few seconds
  of clicking).
- **No idle / foreground auto-pause.** You're by design away from the
  PC; idle minutes accumulating shouldn't pause the session.

If a session is already being tracked when you click a console title,
the app prompts before stopping the current one and starting yours -
sessions remain one-at-a-time end to end so the recorded durations
never overlap.

## Idle and foreground gating

- **Idle pause.** After N minutes of no mouse / keyboard input (default 10),
  the watcher pauses the active session. It resumes automatically on the
  next input event. Set to 0 to disable.
- **Foreground only.** When on, the session is only counted while the game's
  window is in the foreground. Useful if you tend to leave games running in
  the background. To avoid logging a phantom pause every time you alt-tab
  for a few seconds (replying to a message, glancing at a wiki, etc.) the
  watcher waits *Pause after focus has been away for* seconds (default 30)
  before it actually begins a pause. If focus returns to the game inside
  that window, nothing is recorded. Set the grace to 0 for legacy
  instant-pause behaviour.

A pause does not split the session into multiple entries - it adds a pause
record to the same session, just like the manual timer's pause feature.

## What data is read vs persisted

| Data | Read | Persisted? |
| --- | --- | --- |
| Process names + executable paths | yes (psutil) | only for matched / learned games |
| Process command line (for `-SteamAppId`) | yes | no |
| Steam `appmanifest_*.acf` files | yes | only the discovered game name + install dir |
| Epic `*.item` manifests | yes | only the discovered game name + install dir |
| GOG registry / Galaxy DB | yes | only the discovered game name + install dir |
| User mouse / keyboard activity | only as **idle seconds** via `GetLastInputInfo` | no |
| Foreground-window PID | yes | no |

Nothing about other applications, browsing, or non-game processes is logged
or stored. Strict mode further restricts attention to known game-library
folders.

## Fixing mistakes

If the watcher mis-attributes a session:

1. Open **Process Watcher Settings -> Learned mappings**.
2. Select the offending row and click **Forget selected mapping**.
3. Optionally add the executable basename to the user ignore list via
   editing config (a UI for this is coming in a follow-up).

The watcher learns the correct mapping the next time you launch the game
and confirm it via the toast.

## Crash recovery

The active session state is persisted to `config.json` every 30 seconds
while a session is running. If the app is killed mid-session, on next
startup it offers to record the partial session (using the last persisted
tick as the end time) so no playtime is lost.

## Debugging: the watcher log

Every watcher decision is recorded to a rotating log file so you can answer
"why didn't this game get tracked?" without re-running the app.

**Location.** `<config dir>/logs/watcher.log` plus up to 3 rotated backups
(2 MiB each). On Windows the config dir is `%APPDATA%\GamesListManager\`.

**Quick access.** **Options -> Process Watcher Settings -> Logging** has
**Open log folder** and **Open log file** buttons.

**Levels.** Set via the same dialog; takes effect immediately, no restart.

| Level | What you get |
|---|---|
| `ERROR` | Only crashes / unrecoverable failures. |
| `WARNING` | Errors plus things the watcher worked around (failed manifest, missing toast button). |
| `INFO` *(default)* | Plus session start/end, candidate promotion, sibling handoff, ambiguous detections, store-rescan summaries. |
| `DEBUG` | Plus every per-tick decision: which filter rejected each new process, which resolver layer matched, fuzzy scores, candidate ages, idle seconds, foreground PID, every event emitted. |

**Logger names** (useful for `grep`):

- `watcher.core` — start/stop/pause/resume, top-level errors
- `watcher.resolver` — per-process accept/reject and resolver layer hits
- `watcher.state` — state-machine transitions (Idle ↔ Candidate ↔ Tracking ↔ Paused ↔ Ending), idle/foreground decisions
- `watcher.manifests` — Steam / Epic / GOG scan progress
- `watcher.bridge` — session persistence, Discord pass-through, recovery
- `watcher.toast` — toast construction and action dispatch
- `watcher.tray` — tray menu actions
- `watcher.idle` — reserved for the idle-detection helpers
- `watcher.main` — bootstrap and cleanup

**Example: troubleshooting "my game didn't track"**

1. Set log level to `DEBUG`.
2. Launch the game, wait 30 s, exit.
3. Open the log file (the button in settings) and search for the executable
   basename. You'll see exactly which filter rejected it (`builtin_ignore`,
   `user_ignore`, `path_fragment(...)`, `strict_outside_roots`) or which
   resolver layer it hit (`L1_learned_path`, `L2_steam_appid(NNN)`,
   `L3_fuzzy(NN)`, or `ambiguous: ... below threshold 85`).
4. If it was below the fuzzy threshold, either rename the library entry to
   match the install folder more closely, or launch the game once more and
   confirm via the toast — the watcher learns the mapping after that.

## Disabling completely

- **Options -> Process Watcher: On** (toggle off) stops the worker thread.
- The tray icon can be hidden via **Process Watcher Settings**.
- All `watcher_*` and `notifications_*` config keys are preserved across
  restarts so re-enabling restores your prior preferences.
