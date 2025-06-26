# Discord Rich Presence Setup Guide

GamesList Manager now includes Discord Rich Presence integration! This allows your Discord status to show what you're doing in the app, such as playing games, browsing your library, or viewing statistics.

## Features

Discord Rich Presence integration is **optional and can be toggled on/off** from the Options menu.

Your Discord status will dynamically update to show:

- **🎮 Playing Games**: When tracking time for a game, shows "Playing [Game Name]" with elapsed time
- **⏸️ Paused Sessions**: When you pause a gaming session
- **📝 Browsing Library**: When you're in the Games List tab
- **📊 Viewing Statistics**: When you're analyzing your gaming data
- **📈 Analyzing Data**: When you're in specific statistics views
- **➕ Adding Games**: When you're adding new games to your library
- **✏️ Editing Games**: When you're managing game information
- **✅ Session Complete**: Briefly shows when you finish a gaming session

## Setup Instructions

### Step 1: Create a Discord Application

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" 
3. Give it a name like "GamesList Manager"
4. Click "Create"

### Step 2: Get Your Application ID

1. In your new Discord application, go to the "General Information" tab
2. Copy the "Application ID" (it's a long number)
3. Keep this handy - you'll need it in the next step

### Step 3: Upload Rich Presence Assets (Optional)

For better visuals, you can upload custom images:

1. Go to the "Rich Presence" → "Art Assets" tab
2. Upload images for:
   - `gameslist_logo` - Main app icon (512x512 recommended)
   - `idle` - Small icon for idle state
   - `browsing` - Small icon for browsing
   - `playing` - Small icon for playing games
   - `paused` - Small icon for paused sessions
   - `stats` - Small icon for viewing statistics
   - `adding` - Small icon for adding games
   - `editing` - Small icon for editing games
   - `completed` - Small icon for completed sessions

### Step 4: Configure GamesList Manager

1. Open `discord_integration.py` in your GamesList Manager folder
2. Find this line near the top:
   ```python
   CLIENT_ID = "1234567890123456789"  # Placeholder
   ```
3. Replace the placeholder with your actual Application ID:
   ```python
   CLIENT_ID = "YOUR_APPLICATION_ID_HERE"
   ```
4. Save the file

### Step 5: Install Dependencies

Make sure you have the required Discord library installed:

```bash
pip install pypresence
```

Or if you're using the requirements.txt:
```bash
pip install -r requirements.txt
```

### Step 6: Test It Out!

1. Make sure Discord is running on your computer
2. Start GamesList Manager
3. Your Discord status should show "Managing game library"
4. Try different activities:
   - Switch tabs to see status changes
   - Start tracking time for a game
   - Add or edit games
   - View statistics

## Usage & Controls

### Toggle Discord Integration

Discord Rich Presence is **enabled by default** but can be controlled through the application:

**Enable/Disable**: 
- Go to `Options` → `Discord: Enabled/Disabled` in the menu bar
- The menu shows the current status (Enabled/Disabled)
- Changes take effect immediately
- Your preference is saved and remembered between app restarts

**Automatic Behavior**:
- When enabled: Discord integration automatically starts when you launch GamesList Manager (if Discord is running)
- When disabled: No Discord connections are made and your Discord status remains unchanged
- The integration gracefully handles Discord being unavailable

**Manual Control**:
- You can also disable by closing Discord entirely
- Or disable activity status in Discord settings

## Customization

### Updating Status Messages

You can customize the status messages by editing the `discord_integration.py` file:

- `update_presence_browsing()` - When browsing different tabs
- `update_presence_playing()` - When tracking game time
- `update_presence_browsing()` - When in different tabs
- `update_presence_editing_game()` - When editing games
- etc.

### Adding Custom Buttons

The status includes a "View on GitHub" button by default. You can:

1. Change the URL to your own project
2. Add additional buttons (Discord supports up to 2)
3. Remove buttons entirely

**Important**: Buttons have specific requirements:
- Only appear on Discord **desktop app** (not mobile/web)
- Your Discord application may need proper configuration
- Some Discord applications require verification for buttons to work
- Buttons use format: `[{"label": "Button Text", "url": "https://example.com"}]`

## Troubleshooting

### "Discord Rich Presence not available" Message

This means the `pypresence` library isn't installed:
```bash
pip install pypresence
```

### Status Not Showing

1. **Discord not running**: Make sure Discord is open
2. **Wrong Application ID**: Double-check you copied the right ID
3. **Firewall issues**: Make sure Discord can connect (usually not an issue)
4. **Discord activity status**: Check that "Display current activity as a status message" is enabled in Discord Settings → Activity Privacy

### Status Shows but No Images

1. Make sure you uploaded assets to your Discord application
2. Check that the asset names match exactly (case-sensitive)
3. It can take a few minutes for new assets to be available

## Privacy Note

Discord Rich Presence only shares:
- What you're currently doing (playing X game, browsing library, etc.)
- How long you've been doing it
- Your total game count and completion stats
- No personal game data, ratings, or notes are shared

You can disable Rich Presence at any time by:
1. **Using the menu toggle**: `Options` → `Discord: Enabled/Disabled` (recommended)
2. Closing GamesList Manager
3. Disabling activity status in Discord settings  
4. Closing Discord entirely

## Need Help?

If you run into issues:
1. Check that Discord is running and you're logged in
2. Verify your Application ID is correct
3. Make sure `pypresence` is installed
4. Check the console output for error messages

Enjoy showing off your gaming library management on Discord! 🎮 