"""
Constants used throughout the GamesList application
"""

# Application version - update this single location to change version across the entire app
VERSION = "1.11.3"

_DEBUG = False

# GitHub repository configuration for auto-updater
GITHUB_OWNER = "DrNefarius"
GITHUB_REPO = "GameTrackerV2"
GITHUB_API_BASE = "https://api.github.com"

# Discord Rich Presence configuration
# SETUP REQUIRED: Replace CLIENT_ID with your actual Discord Application ID
# See DISCORD_SETUP.md for detailed setup instructions
DISCORD_CLIENT_ID = "1387760989843492947"  # Placeholder - needs to be replaced with actual Discord app ID
DISCORD_GITHUB_URL = "https://drnefarius.github.io/GameTrackerV2/"

# Special key constants for different platforms
QT_ENTER_KEY1 = 'special 16777220'
QT_ENTER_KEY2 = 'special 16777221'

# Rating stars and tags
STAR_FILLED = "★"
STAR_EMPTY = "☆"

# Rating tags organized by sentiment
NEGATIVE_TAGS = ["Boring", "Frustrating", "Buggy", "Repetitive", "Confusing", "Grindy", "Unbalanced", "Broken", "Disappointing", "Overrated"]
NEUTRAL_TAGS = ["Challenging", "Linear", "Open-world", "Short", "Long", "Casual", "Hardcore", "Nostalgic", "Retro", "Complex"]
POSITIVE_TAGS = ["Fun", "Amazing", "Immersive", "Story-rich", "Rewarding", "Addictive", "Beautiful", "Creative", "Innovative", "Polished", 
                "Relaxing", "Engaging", "Epic", "Hilarious", "Atmospheric", "Memorable", "Satisfying", "Unique", "Well-designed", "Masterpiece"]

# Combined list for backward compatibility
RATING_TAGS = NEGATIVE_TAGS + NEUTRAL_TAGS + POSITIVE_TAGS

# Table styling
COMPLETED_STYLE = ('#000000', '#dff0d8')  # Light green background, black text
DROPPED_STYLE = ('#000000', '#b7e1b7')  # Slightly deeper green than Completed, black text
IN_PROGRESS_STYLE = ('#000000', '#fcf8e3')  # Light yellow background, black text
FUTURE_RELEASE_STYLE = ('#000000', '#b4acff')   # Light purple background, black text
DEFAULT_STYLE = ('#000000', '#f8d7da')  # Light red background, black text

# IGDB API configuration (Twitch OAuth client-credentials flow).
# Users supply their own client_id / client_secret via Options -> IGDB Settings.
IGDB_API_BASE = "https://api.igdb.com/v4"
IGDB_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_IMAGE_BASE = "https://images.igdb.com/igdb/image/upload"
IGDB_COVER_SIZE = "t_cover_big"  # 264x374, good tradeoff for a details popup
IGDB_COVER_THUMB_SIZE = "t_cover_small"  # 90x128, for match picker thumbnails
# IGDB allows up to 4 requests per second; stay a bit below the ceiling.
IGDB_RATE_LIMIT_PER_SEC = 4
IGDB_CACHE_SUBDIR = "igdb_cache"
IGDB_TOKEN_FILE = "igdb_token.json"

# Canonical game status values. Always import these rather than hard-coding strings
# so the casing (e.g. 'In progress' vs 'In Progress') stays consistent everywhere.
STATUS_PENDING = 'Pending'
STATUS_IN_PROGRESS = 'In progress'
STATUS_COMPLETED = 'Completed'
STATUS_DROPPED = 'Dropped'
VALID_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_COMPLETED, STATUS_DROPPED)

# ---------------------------------------------------------------------------
# Process watcher defaults
# ---------------------------------------------------------------------------

# How often the watcher polls the process table. 3s is a reasonable balance:
# users perceive session start within one toast worth of latency, and the cost
# is negligible thanks to PID-set diffing (only NEW pids get attribute fetches).
WATCHER_POLL_INTERVAL_SEC = 3

# A new process must remain alive this long before we attribute a session to
# it. Filters out launchers that spawn the real game and exit, plus crash
# dialogs / installers that briefly appear under a game's install dir.
WATCHER_START_DEBOUNCE_SEC = 10

# When a tracked process disappears, wait this long for a sibling under the
# same install dir to appear before ending the session. Handles the common
# "launcher.exe -> game.exe" handoff without ending and re-starting sessions.
WATCHER_END_GRACE_SEC = 15

# Persist active session state every N seconds for crash recovery.
WATCHER_STATE_PERSIST_SEC = 30

# Default idle threshold (minutes of no input) before pausing a session.
# 0 disables idle pausing.
WATCHER_DEFAULT_IDLE_MINUTES = 10

# Fuzzy match threshold (rapidfuzz token_set_ratio, 0-100). Below this we ask
# the user to confirm rather than guess.
WATCHER_FUZZY_THRESHOLD = 85

# Auto-pauses (idle / foreground-only) shorter than this duration that were
# still open when the session ended are treated as transient artifacts of the
# user's exit gesture (e.g. clicking outside the game window before closing
# it) and discarded from the recorded session. Manual pauses are always kept.
WATCHER_TRAILING_AUTO_PAUSE_DROP_SEC = 8

# Default grace window (seconds) before "watcher_foreground_only" pauses
# a session whose tracked process lost window focus. Anything shorter
# than this - quick alt-tab to read a message, glance at a browser, etc.
# - is treated as part of normal play and not recorded as a pause.
# Set the corresponding config key to 0 to fall back to the legacy
# instant-pause behaviour.
WATCHER_DEFAULT_FOREGROUND_GRACE_SEC = 30

# When strict mode rejects an exe whose path still looks like a real
# Steam/Epic/GOG install layout, re-run the store-manifest scan at most
# once per this many seconds (avoids hammering disk if something is
# genuinely not in the index).
WATCHER_OPPORTUNISTIC_RESCAN_MIN_INTERVAL_SEC = 3

# After this many "strict denied but path looks like a store game"
# attempts for the same pid (each attempt evicts the pid so the next
# tick re-sees it), give up so we don't spin forever on a bogus path.
WATCHER_STRICT_STORE_RETRY_MAX = 12

# Process basenames the watcher should always ignore. Lowercased for matching.
# Includes platform launchers, anti-cheat services, common helpers and
# installers, browser/IDE noise, and storefronts whose own .exe is sometimes
# installed under steamapps/common (Steam Linux runtime, Proton, etc.).
IGNORED_PROCESS_NAMES = (
    # Storefronts / launchers
    'steam.exe', 'steamwebhelper.exe', 'steamservice.exe',
    'epicgameslauncher.exe', 'epicwebhelper.exe', 'epicgameslauncher-win32-shipping.exe',
    'galaxyclient.exe', 'galaxyclient-helper.exe', 'galaxyclienthelper.exe',
    'galaxycommunication.exe', 'galaxyoverlay.exe',
    'origin.exe', 'eadesktop.exe', 'easteamproxy.exe', 'eaconnect_microsoft.exe',
    'eabackgroundservice.exe',
    'ubisoftconnect.exe', 'upc.exe', 'uplay.exe', 'uplaywebcore.exe',
    'battle.net.exe', 'agent.exe', 'blizzarderror.exe',
    'riotclient.exe', 'riotclientservices.exe', 'riotclientux.exe',
    'rockstar games launcher.exe', 'launcherpatcher.exe',
    # Anti-cheat
    'easyanticheat.exe', 'easyanticheat_eos.exe', 'easyanticheat_setup.exe',
    'be_service.exe', 'beservice.exe', 'beservice_x64.exe',
    'vguardbooter.exe', 'vanguard.exe',
    'faceit.exe', 'faceitclient.exe',
    # Helpers / crash handlers
    'crashpad_handler.exe', 'crashreporter.exe', 'crashsender.exe',
    'unitycrashhandler64.exe', 'unitycrashhandler32.exe', 'ueprereqsetup_x64.exe',
    'unrealcefsubprocess.exe', 'cefshare.exe',
    'directx_setup.exe', 'vc_redist.x64.exe', 'vc_redist.x86.exe',
    'unins000.exe', 'setup.exe', 'install.exe', 'uninstall.exe',
)

# Path-fragment ignores: any process whose exe path contains one of these
# (case-insensitive) is skipped. Useful for installer/redist directories
# that often live under steamapps/common.
IGNORED_PATH_FRAGMENTS = (
    '\\_commonredist\\', '\\directx\\', '\\vcredist\\',
    '\\redist\\', '\\dotnetfx\\',
)

# Default install-root prefixes scanned in strict mode. The store-manifest
# scanners discover real roots at startup and override these with the
# canonical paths from each launcher's metadata, but these defaults give the
# strict-mode resolver something to work with on first run.
WATCHER_DEFAULT_ROOTS = (
    'C:\\Program Files (x86)\\Steam\\steamapps\\common\\',
    'C:\\Program Files\\Epic Games\\',
    'C:\\Program Files (x86)\\GOG Galaxy\\Games\\',
    'C:\\Program Files\\GOG Galaxy\\Games\\',
)
