"""
Configuration management for GamesList application.
Handles loading, saving, and accessing application settings.
"""

import os
import json
import platform
from datetime import datetime

def get_config_dir():
    """Get the configuration directory for the application."""
    if platform.system() == 'Windows':
        config_dir = os.path.join(os.environ['APPDATA'], 'GamesListManager')
    elif platform.system() == 'Darwin':  # macOS
        config_dir = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'GamesListManager')
    else:  # Linux and others
        config_dir = os.path.join(os.path.expanduser('~'), '.config', 'GamesListManager')
    
    # Ensure the directory exists
    os.makedirs(config_dir, exist_ok=True)
    return config_dir

def get_config_file():
    """Get the path to the config file."""
    return os.path.join(get_config_dir(), 'config.json')

def load_config():
    """Load configuration from config file."""
    config_file = get_config_file()
    default_config = {
        'last_file': None,
        'default_save_dir': os.path.expanduser('~'),
        'notes_enabled': False,
        'discord_enabled': True,
        'igdb_client_id': '',
        'igdb_client_secret': '',
        'igdb_enabled': False,
        'igdb_auto_match_on_add': False,
        # --- Process watcher (opt-in) ---
        'watcher_enabled': False,
        'watcher_strict_mode': True,            # only watch known library roots
        'watcher_user_roots': [],               # extra folders the user added
        'watcher_ignore_list': [],              # user-added ignored basenames
        'watcher_process_map': {},              # learned: exe_path -> game name
        'watcher_installdir_map': {},           # learned: install_dir -> game name
        'watcher_store_index': {},              # cached manifest scan results
        'watcher_idle_pause_minutes': 10,       # 0 disables idle pause
        'watcher_foreground_only': False,
        'watcher_per_game_excluded': [],        # game names opted out
        # --- Notifications ---
        'notifications_on_start': True,
        'notifications_on_end': True,
        'notifications_on_match_needed': True,
        'notifications_quiet_hours': None,      # e.g. ["23:00", "08:00"]
        # When True, escalate watcher toasts to the Reminder scenario so
        # they break through Windows' Focus Assist (which auto-suppresses
        # popups while a game is in fullscreen). Trade-off: the toast
        # stays on-screen until the user dismisses it.
        'notifications_bypass_focus_assist': False,
        # --- Tray ---
        'tray_icon_enabled': True,
        # --- Crash-safe persistence (written by watcher every ~30s) ---
        'active_session_state': None,
        # --- Watcher logging ---
        # One of: DEBUG, INFO, WARNING, ERROR. DEBUG includes per-tick detail
        # (resolver layers tried, fuzzy scores, idle seconds, candidate ages).
        'watcher_log_level': 'INFO',
    }
    
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f)
            # Ensure all default keys exist in loaded config
            for key, value in default_config.items():
                if key not in config:
                    config[key] = value
            return config
        except Exception as e:
            print(f"Error loading config: {str(e)}")
            # Preserve the broken config so it isn't overwritten by the next save,
            # which would otherwise silently erase user settings forever.
            try:
                backup_name = f"{config_file}.backup-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                os.rename(config_file, backup_name)
                print(f"Backed up corrupt config to {backup_name}")
            except OSError as backup_err:
                print(f"Failed to back up corrupt config: {backup_err}")
            return default_config
    else:
        return default_config

def save_config(config):
    """Save configuration to config file atomically (write to tmp, then os.replace)."""
    config_file = get_config_file()
    tmp_file = f"{config_file}.tmp"
    try:
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass
        os.replace(tmp_file, config_file)
        return True
    except Exception as e:
        print(f"Error saving config: {str(e)}")
        try:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)
        except OSError:
            pass
        return False 