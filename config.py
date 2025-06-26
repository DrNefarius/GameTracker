"""
Configuration management for GamesList application.
Handles loading, saving, and accessing application settings.
"""

import os
import json
import platform

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
        'discord_enabled': True
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
            return default_config
    else:
        return default_config

def save_config(config):
    """Save configuration to config file."""
    config_file = get_config_file()
    try:
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving config: {str(e)}")
        return False 