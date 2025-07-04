"""
Constants used throughout the GamesList application
"""

# Application version - update this single location to change version across the entire app
VERSION = "1.9.4"

_DEBUG = False

# GitHub repository configuration for auto-updater
GITHUB_OWNER = "yourname"
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
IN_PROGRESS_STYLE = ('#000000', '#fcf8e3')  # Light yellow background, black text
FUTURE_RELEASE_STYLE = ('#000000', '#b4acff')   # Light purple background, black text
DEFAULT_STYLE = ('#000000', '#f8d7da')  # Light red background, black text 