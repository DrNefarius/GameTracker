"""
Constants used throughout the GamesList application
"""

# Application version - update this single location to change version across the entire app
VERSION = "1.10.1"

_DEBUG = True

# GitHub repository configuration for auto-updater
GITHUB_OWNER = "YourName"
GITHUB_REPO = "GameTracker"
GITHUB_API_BASE = "https://api.github.com"

# Discord Rich Presence configuration
# SETUP REQUIRED: Replace CLIENT_ID with your actual Discord Application ID
# See DISCORD_SETUP.md for detailed setup instructions
DISCORD_CLIENT_ID = "1234567890123456789"  # Placeholder - needs to be replaced with actual Discord app ID
DISCORD_GITHUB_URL = "https://yourname.github.io/GameTracker/"

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
