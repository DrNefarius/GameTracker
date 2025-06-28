"""
Discord Rich Presence integration for GamesList Manager.
Provides dynamic status updates based on app state and current activities.
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

try:
    from pypresence import Presence
    DISCORD_AVAILABLE = True
except ImportError:
    DISCORD_AVAILABLE = False
    print("Discord Rich Presence not available - pypresence not installed")


class DiscordIntegration:
    """Manages Discord Rich Presence for GamesList Manager"""
    
    # Discord Application ID (you'll need to create one at https://discord.com/developers/applications)
    # SETUP REQUIRED: Replace this with your actual Discord Application ID
    # See DISCORD_SETUP.md for detailed setup instructions
    CLIENT_ID = "1234567890123456789"  # Placeholder - needs to be replaced with actual Discord app ID
    
    # GitHub URL for Discord button
    GITHUB_URL = "https://github.com/yourusername/GameTracker"
    
    def __init__(self, enabled=True):
        self.rpc = None
        self.connected = False
        self.enabled = enabled
        self.current_session = None
        self.session_start_time = None
        self.app_start_time = time.time()
        self.current_state = "idle"
        self.current_game = None
        self.total_games = 0
        self.completed_games = 0
        self.session_complete_timer = None  # Track session complete timer
        self.showing_completion = False  # Flag to prevent overriding completion status
        self.current_tab = "Games List"  # Track current tab for timer return
        self.selected_game_stats = None  # Track selected game in statistics tab
        
        # Initialize connection if enabled
        if self.enabled:
            self.initialize()
    
    def initialize(self):
        """Initialize Discord RPC connection"""
        if not DISCORD_AVAILABLE or not self.enabled:
            return False
            
        try:
            self.rpc = Presence(self.CLIENT_ID)
            self.rpc.connect()
            self.connected = True
            
            # Set initial presence to browsing Games List
            self.update_presence_browsing("Games List")
            return True
            
        except Exception as e:
            print(f"Failed to connect to Discord: {str(e)}")
            self.connected = False
            return False
    
    def disconnect(self):
        """Disconnect from Discord RPC"""
        if self.rpc and self.connected:
            try:
                self.rpc.close()
            except:
                pass
            self.connected = False
    
    def is_connected(self):
        """Check if Discord RPC is connected"""
        return self.enabled and self.connected and self.rpc is not None
    
    def enable_discord(self):
        """Enable Discord integration and connect"""
        self.enabled = True
        if not self.connected:
            return self.initialize()
        return True
    
    def disable_discord(self):
        """Disable Discord integration and disconnect"""
        self.enabled = False
        if self.connected:
            self.disconnect()
        return True
    
    def update_game_library_stats(self, total_games: int, completed_games: int):
        """Update library statistics for presence details"""
        self.total_games = total_games
        self.completed_games = completed_games
    

    
    def update_presence_browsing(self, current_tab: str = "Games List"):
        """Set browsing presence - browsing games or viewing tabs"""
        if not self.is_connected():
            return
            
        # Don't override session completion status
        if self.showing_completion:
            return
            
        try:
            # Update current tab tracking
            self.current_tab = current_tab
            
            # Debug: print tab information
            print(f"Discord: Updating presence for tab '{current_tab}' with {self.total_games} games, {self.completed_games} completed")
            
            # Special handling for Statistics tab with selected game
            if current_tab == "Statistics" and self.selected_game_stats:
                self.update_presence_viewing_stats(self.selected_game_stats)
                return
            
            activity_map = {
                "Games List": ("Browsing game library", "📝"),
                "Summary": ("Viewing game statistics", "📊"),
                "Statistics": ("Analyzing gaming data", "📈")
            }
            
            details, emoji = activity_map.get(current_tab, ("Browsing games", "🎮"))
            
            # No timer for browsing - user is not actively playing
            # Show current activity prominently in user list
            activity_icons = {
                "Games List": "📝",
                "Summary": "📊", 
                "Statistics": "📈"
            }
            icon = activity_icons.get(current_tab, "🎮")
            
            self.rpc.update(
                details=f"{icon} {details}",  # Activity with icon shows in user list
                state=f"{self.total_games} games • {self.completed_games} completed",
                large_image="gameslist_logo",
                large_text="GamesList Manager",
                small_image="browsing",
                small_text=f"In {current_tab}",
                buttons=[
                    {"label": "View on GitHub", "url": self.GITHUB_URL}
                ]
            )
            self.current_state = "browsing"
            
        except Exception as e:
            print(f"Error updating Discord presence (browsing): {str(e)}")
    
    def update_presence_playing(self, game_name: str, session_start_time: datetime = None, platform: str = None):
        """Set playing presence - tracking time for a specific game"""
        if not self.is_connected():
            return
            
        try:
            self.current_game = game_name
            self.current_session = session_start_time or datetime.now()
            self.session_start_time = self.current_session.timestamp()
            
            # Truncate game name if too long for Discord
            display_name = game_name[:128] if len(game_name) > 128 else game_name
            
            # Add platform information to the state if available
            state_text = "Playing"
            if platform and platform.strip():
                state_text = f"Playing on {platform}"
            
            # Show timer only when actively playing - this tracks the session duration
            # Make the game name more prominent in user list display
            self.rpc.update(
                details=f"🎮 {display_name}",  # Game name with icon like other statuses
                state=state_text,  # Playing status with platform
                large_image="gameslist_logo",
                large_text="GamesList Manager",
                small_image="playing",
                small_text="In session",
                start=self.session_start_time,  # Timer shows session duration
                buttons=[
                    {"label": "View on GitHub", "url": self.GITHUB_URL}
                ]
            )
            self.current_state = "playing"
            
        except Exception as e:
            print(f"Error updating Discord presence (playing): {str(e)}")
    
    def update_presence_paused(self, game_name: str, platform: str = None):
        """Set paused presence - game session is paused"""
        if not self.is_connected():
            return
            
        try:
            display_name = game_name[:100] if len(game_name) > 100 else game_name
            
            # Add platform information to the state if available
            state_text = "Session paused"
            if platform and platform.strip():
                state_text = f"Paused on {platform}"
            
            # No timer when paused - paused sessions shouldn't show elapsed time
            # Show paused game name prominently
            self.rpc.update(
                details=f"⏸️ {display_name}",  # Paused game shows clearly in user list
                state=state_text,
                large_image="gameslist_logo",
                large_text="GamesList Manager", 
                small_image="paused",
                small_text="Paused",
                buttons=[
                    {"label": "View on GitHub", "url": self.GITHUB_URL}
                ]
            )
            self.current_state = "paused"
            
        except Exception as e:
            print(f"Error updating Discord presence (paused): {str(e)}")
    
    def update_presence_adding_game(self):
        """Set presence for adding a new game"""
        if not self.is_connected():
            return
            
        # Don't override session completion status
        if self.showing_completion:
            return
            
        try:
            self.rpc.update(
                details="➕ Adding new game",
                state="Expanding game library", 
                large_image="gameslist_logo",
                large_text="GamesList Manager",
                small_image="editing",
                small_text="Adding game",
                buttons=[
                    {"label": "View on GitHub", "url": self.GITHUB_URL}
                ]
            )
            self.current_state = "adding"
            
        except Exception as e:
            print(f"Error updating Discord presence (adding): {str(e)}")
    
    def update_presence_editing_game(self, game_name: str):
        """Set presence for editing a game"""
        if not self.is_connected():
            return
            
        # Don't override session completion status
        if self.showing_completion:
            return
            
        try:
            display_name = game_name[:100] if len(game_name) > 100 else game_name
            
            self.rpc.update(
                details=f"✏️ {display_name}",
                state="Editing game details",
                large_image="gameslist_logo",
                large_text="GamesList Manager",
                small_image="editing",
                small_text="Editing",
                buttons=[
                    {"label": "View on GitHub", "url": self.GITHUB_URL}
                ]
            )
            self.current_state = "editing"
            
        except Exception as e:
            print(f"Error updating Discord presence (editing): {str(e)}")
    
    def update_presence_viewing_stats(self, game_name: str = None):
        """Set presence for viewing game statistics"""
        if not self.is_connected():
            return
            
        # Don't override session completion status
        if self.showing_completion:
            return
            
        try:
            # Store selected game for context tracking
            self.selected_game_stats = game_name
            
            if game_name:
                display_name = game_name[:100] if len(game_name) > 100 else game_name
                details = f"📊 {display_name}"
                state = "Viewing game statistics"
                small_text = f"Stats: {game_name[:20]}..."
            else:
                details = "📈 Analyzing gaming data"
                state = f"{self.total_games} games • {self.completed_games} completed"
                small_text = "Statistics view"
            
            self.rpc.update(
                details=details,
                state=state,
                large_image="gameslist_logo",
                large_text="GamesList Manager",
                small_image="statistics",
                small_text=small_text,
                buttons=[
                    {"label": "View on GitHub", "url": self.GITHUB_URL}
                ]
            )
            self.current_state = "viewing_stats"
            
        except Exception as e:
            print(f"Error updating Discord presence (viewing stats): {str(e)}")
    
    def update_presence_session_complete(self, game_name: str, session_duration: str, platform: str = None):
        """Set presence for completed session - shows for 10 seconds then returns to current context"""
        if not self.is_connected():
            return
            
        try:
            display_name = game_name[:100] if len(game_name) > 100 else game_name
            
            # Add platform information to the state if available
            if platform and platform.strip():
                state_text = f"Completed on {platform} • {session_duration}"
            else:
                state_text = f"Played for {session_duration}"
            
            # Set completion flag to prevent other updates
            self.showing_completion = True
            
            # Cancel any existing completion timer
            if self.session_complete_timer:
                self.session_complete_timer.cancel()
            
            self.rpc.update(
                details=f"✅ {display_name}",
                state=state_text,
                large_image="gameslist_logo",
                large_text="GamesList Manager",
                small_image="completed",
                small_text="Session complete",
                buttons=[
                    {"label": "View on GitHub", "url": self.GITHUB_URL}
                ]
            )
            self.current_state = "session_complete"
            
            # Return to current context after 10 seconds
            def complete_and_return_to_current_tab():
                try:
                    self.showing_completion = False
                    if self.current_tab == "Statistics" and self.selected_game_stats:
                        self.update_presence_viewing_stats(self.selected_game_stats)
                    else:
                        self.update_presence_browsing(self.current_tab)
                except Exception as e:
                    print(f"Error returning from session complete: {str(e)}")
            
            self.session_complete_timer = threading.Timer(10.0, complete_and_return_to_current_tab)
            self.session_complete_timer.start()
            
        except Exception as e:
            print(f"Error updating Discord presence (session complete): {str(e)}")
    
    def get_current_state(self):
        """Get current Discord state for debugging"""
        return {
            'connected': self.connected,
            'state': self.current_state,
            'game': self.current_game,
            'total_games': self.total_games,
            'completed_games': self.completed_games
        }

# Global instance for Discord integration
_discord_instance = None

def initialize_discord(enabled=True):
    """Initialize global Discord integration"""
    global _discord_instance
    _discord_instance = DiscordIntegration(enabled=enabled)
    return _discord_instance

def get_discord_integration():
    """Get the global Discord integration instance"""
    return _discord_instance

def cleanup_discord():
    """Cleanup Discord integration"""
    global _discord_instance
    if _discord_instance:
        _discord_instance.disconnect()
        _discord_instance = None 