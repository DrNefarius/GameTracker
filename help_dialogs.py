"""
Help and information dialog functions.
Extracted from event_handlers.py for better modularity.
"""

import PySimpleGUI as sg
import sys
import platform
import webbrowser
from datetime import datetime
from constants import VERSION
from emoji_utils import emoji_image, get_emoji

def show_user_guide(parent_window=None):
    """Show comprehensive user guide with emoji images"""
    
    # Create a custom window with emoji support
    guide_layout = [
        [sg.Text("GAMES LIST MANAGER - USER GUIDE", font=('Arial', 14, 'bold'), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [sg.Text("=== GETTING STARTED ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Add games using the \"Add Entry\" button")],
            [sg.Text("• Track time by clicking on a game and selecting \"Track Time\"")],
            [sg.Text("• Edit games by clicking on them and selecting \"Edit Game\"")],
            [sg.Text("• Rate games using the \"Rate Game\" option")],
            [sg.Text("")],
            [sg.Text("=== MAIN FEATURES ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [emoji_image(get_emoji('book'), size=16), sg.Text(" GAMES LIST TAB:", font=('Arial', 11, 'bold'))],
            [sg.Text("• View all your games in a sortable table")],
            [sg.Text("• Search/filter games using the search box")],
            [sg.Text("• Click column headers to sort by that column")],
            [sg.Text("• Right-click or left-click games for action menu")],
            [sg.Text("• Color coding: Green=Completed, Yellow=In Progress, Purple=Future Release, Red=Pending")],
            [sg.Text("")],
            [emoji_image(get_emoji('time'), size=16), sg.Text(" TIME TRACKING:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Click \"Track Time\" to start a session timer")],
            [sg.Text("• Use Play/Pause/Stop controls")],
            [sg.Text("• Add session feedback (notes + ratings) when stopping")],
            [sg.Text("• Sessions are automatically saved to your game data")],
            [sg.Text("")],
            [emoji_image(get_emoji('chart'), size=16), sg.Text(" SUMMARY TAB:", font=('Arial', 11, 'bold'))],
            [sg.Text("• View statistics about your game collection")],
            [sg.Text("• Charts showing status distribution, release years, playtime, and ratings")],
            [sg.Text("• Refresh charts with the \"Refresh Charts\" button")],
            [sg.Text("")],
            [emoji_image(get_emoji('stats'), size=16), sg.Text(" STATISTICS TAB:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Detailed session analysis and visualizations")],
            [sg.Text("• Select specific games to view their session history")],
            [sg.Text("• View session feedback, ratings, and status changes")],
            [sg.Text("• Interactive charts: timeline, distribution, heatmap, status changes")],
            [sg.Text("")],
            [sg.Text("=== RATINGS SYSTEM ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Rate games 1-5 stars with optional tags and comments")],
            [sg.Text("• Session ratings: Rate individual gaming sessions")],
            [sg.Text("• Game ratings: Overall rating for the entire game")],
            [sg.Text("• Auto-calculated ratings: Automatically calculated from session ratings")],
            [sg.Text("• Rating comparison: Compare session-based vs manual ratings")],
            [sg.Text("")],
            [sg.Text("=== DATA MANAGEMENT ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Files are saved in .gmd format (JSON-based)")],
            [sg.Text("• Auto-save when tracking time or making changes")],
            [sg.Text("• Import from Excel files (.xlsx)")],
            [sg.Text("• Export/backup using \"Save As\"")],
            [sg.Text("")],
            [emoji_image(get_emoji('light_bulb'), size=16), sg.Text(" TIPS:", font=('Arial', 12, 'bold'))],
            [sg.Text("• Use tags in ratings to categorize your experience")],
            [sg.Text("• Session feedback helps track your gaming journey")],
            [sg.Text("• The heatmap shows your gaming patterns and break habits")],
            [sg.Text("• Status changes are automatically tracked with timestamps")]
        ], scrollable=True, vertical_scroll_only=True, size=(750, 500), expand_x=True, expand_y=True)],
        [sg.Button('Close')]
    ]
    
    # Calculate center position relative to parent window
    guide_location = None
    if parent_window:
        from utilities import calculate_popup_center_location
        guide_location = calculate_popup_center_location(parent_window, popup_width=800, popup_height=600)
    
    guide_window = sg.Window('User Guide', guide_layout, modal=True, size=(800, 600), 
                            icon='gameslisticon.ico', finalize=True, resizable=True, location=guide_location)
    
    while True:
        event, values = guide_window.read()
        if event in (sg.WIN_CLOSED, 'Close'):
            break
    
    guide_window.close()

def show_data_format_info(parent_window=None):
    """Show information about data formats and file structure"""
    format_text = """
DATA FORMAT INFORMATION

=== FILE FORMATS ===

📄 .GMD FILES (Games Manager Data):
• Primary format used by Games List Manager
• JSON-based structure for easy reading and backup
• Contains all game data, sessions, ratings, and history
• Automatically versioned for future compatibility

📊 EXCEL IMPORT (.XLSX):
• Import existing game lists from Excel spreadsheets
• Expected columns: Name, Release Date, Platform, Time, Status, Owned, Last Played
• Automatically converts to .gmd format after import

=== DATA STRUCTURE ===

Each game entry contains:
• Basic Info: Name, Release Date, Platform, Status, Ownership
• Time Data: Total playtime, Last played date
• Sessions: Individual gaming sessions with timestamps, duration, feedback
• Ratings: Both game-level and session-level ratings with tags and comments
• History: Status change tracking with timestamps

=== SESSION DATA ===
Sessions include:
• Start/End timestamps
• Duration tracking
• Pause/Resume information
• Unified feedback system (notes + ratings)
• Automatic session statistics

=== BACKUP RECOMMENDATIONS ===
• Regular backups using "Save As" to different locations
• .gmd files are human-readable JSON for easy recovery
• Consider cloud storage for automatic backup
• Export important data before major updates

=== MIGRATION ===
• Automatic migration from older data formats
• Unified feedback system migration (notes + ratings combined)
• Backward compatibility maintained where possible
• Migration status shown during file loading

=== FILE LOCATIONS ===
• Default save location: User's home directory
• Config files: Platform-specific application data folders
• Temporary chart files: System temp directory (auto-cleaned)
"""
    
    format_location = None
    if parent_window:
        from utilities import calculate_popup_center_location
        format_location = calculate_popup_center_location(parent_window, popup_width=750, popup_height=600)
    sg.popup_scrolled(format_text, title="Data Format Information", size=(75, 30), icon='gameslisticon.ico', location=format_location)

def show_troubleshooting_guide(parent_window=None):
    """Show troubleshooting guide with emoji images"""
    
    # Create a custom window with emoji support
    troubleshooting_layout = [
        [sg.Text("TROUBLESHOOTING GUIDE", font=('Arial', 14, 'bold'), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [sg.Text("=== COMMON ISSUES ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [emoji_image(get_emoji('tools'), size=16), sg.Text(" APPLICATION WON'T START:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Check if gameslisticon.ico is in the same folder as the executable")],
            [sg.Text("• Ensure you have sufficient permissions in the installation directory")],
            [sg.Text("• Try running as administrator (Windows) or with sudo (Linux/Mac)")],
            [sg.Text("• Check antivirus software isn't blocking the application")],
            [sg.Text("")],
            [emoji_image(get_emoji('file'), size=16), sg.Text(" FILE LOADING ERRORS:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Verify the .gmd file isn't corrupted (should be valid JSON)")],
            [sg.Text("• Check file permissions - ensure read/write access")],
            [sg.Text("• Try opening the file in a text editor to verify it's not empty")],
            [sg.Text("• Backup files are created automatically if corruption is detected")],
            [sg.Text("")],
            [emoji_image(get_emoji('time'), size=16), sg.Text(" TIME TRACKING ISSUES:", font=('Arial', 11, 'bold'))],
            [sg.Text("• If timer doesn't start, check if another instance is running")],
            [sg.Text("• Timer data is saved automatically when stopped")],
            [sg.Text("• If session data is lost, check the last saved .gmd file")],
            [sg.Text("• Pause/resume functionality requires proper session start")],
            [sg.Text("")],
            [emoji_image(get_emoji('chart'), size=16), sg.Text(" CHART/VISUALIZATION PROBLEMS:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Charts not loading: Try refreshing with the \"Refresh Charts\" button")],
            [sg.Text("• Missing data: Ensure games have the required data (dates, times, ratings)")],
            [sg.Text("• Performance issues: Large datasets may take time to generate charts")],
            [sg.Text("• Display issues: Try resizing the window or switching tabs")],
            [sg.Text("")],
            [emoji_image(get_emoji('search'), size=16), sg.Text(" SEARCH NOT WORKING:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Ensure you press Enter after typing in the search box")],
            [sg.Text("• Search is case-insensitive and searches all visible columns")],
            [sg.Text("• Use \"Reset\" button to clear search filters")],
            [sg.Text("• Special characters in game names may affect search")],
            [sg.Text("")],
            [emoji_image(get_emoji('stats'), size=16), sg.Text(" STATISTICS TAB ISSUES:", font=('Arial', 11, 'bold'))],
            [sg.Text("• No data showing: Ensure games have session data or status history")],
            [sg.Text("• Game not in list: Only games with sessions/ratings/status changes appear")],
            [sg.Text("• Charts not updating: Use \"Refresh Statistics\" button")],
            [sg.Text("• Performance: Large session datasets may take time to process")],
            [sg.Text("")],
            [sg.Text("=== DATA RECOVERY ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("If your data is lost or corrupted:")],
            [sg.Text("1. Check for backup files (*.backup-YYYYMMDDHHMMSS)")],
            [sg.Text("2. Look in the default save directory for recent .gmd files")],
            [sg.Text("3. Check the application config for the last used file path")],
            [sg.Text("4. Import from Excel if you have a backup spreadsheet")],
            [sg.Text("")],
            [sg.Text("=== PERFORMANCE OPTIMIZATION ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("For better performance with large datasets:")],
            [sg.Text("• Regularly clean up old session data if not needed")],
            [sg.Text("• Use search/filtering to work with smaller subsets")],
            [sg.Text("• Close other applications when generating complex charts")],
            [sg.Text("• Consider splitting very large game collections into multiple files")],
            [sg.Text("")],
            [sg.Text("=== GETTING HELP ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("If problems persist:")],
            [sg.Text("• Contact @drnefarius on Discord for support")],
            [sg.Text("• Discord is the primary and recommended support channel")],
            [sg.Text("• Include your operating system and application version")],
            [sg.Text("• Attach relevant error messages or log files")]
        ], scrollable=True, vertical_scroll_only=True, size=(750, 500), expand_x=True, expand_y=True)],
        [sg.Button('Close')]
    ]
    
    # Calculate center position relative to parent window
    troubleshooting_location = None
    if parent_window:
        from utilities import calculate_popup_center_location
        troubleshooting_location = calculate_popup_center_location(parent_window, popup_width=800, popup_height=600)
    
    troubleshooting_window = sg.Window('Troubleshooting Guide', troubleshooting_layout, modal=True, size=(800, 600), 
                                      icon='gameslisticon.ico', finalize=True, resizable=True, location=troubleshooting_location)
    
    while True:
        event, values = troubleshooting_window.read()
        if event in (sg.WIN_CLOSED, 'Close'):
            break
    
    troubleshooting_window.close()

def show_feature_tour(parent_window=None):
    """Show feature tour/walkthrough"""
    tour_text = """
FEATURE TOUR - DISCOVER WHAT'S POSSIBLE

=== 🎮 BASIC GAME MANAGEMENT ===

1. ADD YOUR FIRST GAME:
   • Click "Add Entry" button
   • Fill in game name (required)
   • Add release date, platform, initial status
   • Set ownership status with checkbox

2. ORGANIZE YOUR COLLECTION:
   • Use status: Pending → In Progress → Completed
   • Track ownership with the checkbox
   • Sort by any column (click headers)
   • Search to find specific games quickly

=== ⏱️ TIME TRACKING & SESSIONS ===

3. TRACK YOUR GAMING TIME:
   • Click any game → "Track Time"
   • Use Play/Pause/Stop controls
   • Add session feedback when done (notes + rating)
   • Time automatically adds to total playtime

4. SESSION FEEDBACK SYSTEM:
   • Rate individual sessions (1-5 stars)
   • Add tags to categorize experience
   • Write detailed notes about your session
   • View all feedback in the Statistics tab

=== 📊 ANALYTICS & INSIGHTS ===

5. SUMMARY DASHBOARD:
   • Status distribution pie chart
   • Games by release year
   • Top games by playtime
   • Rating distribution analysis

6. DETAILED STATISTICS:
   • Session timeline visualization
   • Gaming heatmap (shows when you play)
   • Session length distribution
   • Status change timeline

=== 🌟 ADVANCED RATING SYSTEM ===

7. DUAL RATING APPROACH:
   • Session ratings: Rate each gaming session
   • Game ratings: Overall rating for the entire game
   • Auto-calculated ratings: Computed from session ratings
   • Rating comparison: See how session vs game ratings differ

8. RICH RATING DATA:
   • 50+ predefined tags (positive, neutral, negative)
   • Custom comments for detailed feedback
   • Tag frequency analysis
   • Rating trends over time

=== 📈 DATA VISUALIZATION ===

9. INTERACTIVE CHARTS:
   • Click and explore different visualizations
   • Refresh data with dedicated buttons
   • Export charts (screenshot capability)
   • Responsive design adapts to window size

10. SESSION ANALYSIS:
    • Gaming heatmap shows daily patterns
    • Pause analysis (focused vs interrupted sessions)
    • Session length trends
    • Most active gaming periods

=== 🔧 POWER USER FEATURES ===

11. DATA MANAGEMENT:
    • Import from Excel spreadsheets
    • Export to .gmd format for backup
    • Automatic data migration between versions
    • Human-readable JSON format

12. CUSTOMIZATION:
    • Configurable save locations
    • Persistent window settings
    • Automatic session saving
    • Flexible data filtering

=== 💡 PRO TIPS ===

• Use session ratings to track how you feel about games over time
• The heatmap reveals your gaming habits and optimal play times
• Tags help identify what you enjoy most in games
• Status history shows your gaming journey progression
• Regular backups ensure your gaming history is preserved

Ready to explore? Start with adding a few games and tracking some sessions!
"""
    
    tour_location = None
    if parent_window:
        from utilities import calculate_popup_center_location
        tour_location = calculate_popup_center_location(parent_window, popup_width=850, popup_height=800)
    sg.popup_scrolled(tour_text, title="Feature Tour", size=(85, 40), icon='gameslisticon.ico', location=tour_location)

def show_release_notes(parent_window=None):
    """Show release notes and version history"""
    
    # Create a custom window with emoji support
    release_notes_layout = [
        [sg.Text("RELEASE NOTES", font=('Arial', 14, 'bold'), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [sg.Text(f"=== VERSION {VERSION} (Current) ===", font=('Arial', 12, 'bold'))],
            [emoji_image(get_emoji('star'), size=16), sg.Text(" NEW FEATURES:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Complete auto-updater system with GitHub releases integration")],
            [sg.Text("• One-click update downloads with progress tracking and cancellation")],
            [sg.Text("• Intelligent staging system to handle file locking during updates")],
            [sg.Text("• Cross-platform updater scripts (Windows batch, Unix shell)")],
            [sg.Text("• Existing download detection to avoid re-downloading same versions")],
            [sg.Text("• Post-update success notifications with version information")],
            [sg.Text("• Rich release notes display with image loading and HTML rendering")],
            [sg.Text("")],
            [emoji_image(get_emoji('tools'), size=16), sg.Text(" IMPROVEMENTS:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Startup update checking with configurable settings")],
            [sg.Text("• Manual update checking via Options menu")],
            [sg.Text("• Update settings management with download folder access")],
            [sg.Text("• Robust version comparison supporting various tag formats")],
            [sg.Text("• Clean UI with instant dialog display and on-demand image loading")],
            [sg.Text("• Comprehensive error handling and user feedback")],
            [sg.Text("• Automatic backup creation before updates with rollback support")],
            [sg.Text("")],
            [emoji_image(get_emoji('bug'), size=16), sg.Text(" BUG FIXES:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Fixed threading issues that caused GUI freezing during updates")],
            [sg.Text("• Improved download cancellation without background continuation")],
            [sg.Text("• Resolved PySimpleGUI element reuse errors in image dialogs")],
            [sg.Text("• Enhanced Windows batch file execution reliability")],
            [sg.Text("• Fixed UI color consistency across all update dialogs")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.8 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Manual session addition - Add gaming sessions with custom start/end times")],
            [sg.Text("• Dual input methods for manual sessions (start+end times OR duration+end time)")],
            [sg.Text("• Full session feedback support for manually added sessions (notes, ratings, tags)")],
            [sg.Text("• Streamlined action dialog with single-row button layout")],
            [sg.Text("• Comprehensive import cleanup across all modules")],
            [sg.Text("• Enhanced session management with improved modularity")],
            [sg.Text("• Better code organization with focused module separation")],
            [sg.Text("• Fixed manual session dialog buttons disappearing when toggling checkboxes")],
            [sg.Text("• Resolved window resizing issues in manual session popup")],
            [sg.Text("• Improved dialog stability and user experience")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.7 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Discord Rich Presence integration with platform information")],
            [sg.Text("• Enhanced session tracking with platform-aware Discord status")],
            [sg.Text("• Improved Discord presence messages for gaming sessions")],
            [sg.Text("• Better integration between session management and Discord updates")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.6 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• GitHub-style contributions heatmap visualization")],
            [sg.Text("• Year navigation for contributions view (previous/next year)")],
            [sg.Text("• Enhanced table color refresh after status changes")],
            [sg.Text("• Improved data consistency in filtered views")],
            [sg.Text("• Fixed table row colors not updating after status changes")],
            [sg.Text("• Resolved contributions heatmap display issues")],
            [sg.Text("• Better error handling for contributions visualization")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.5 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Enhanced session distribution charts (scatter plot, box plot)")],
            [sg.Text("• Improved contributions map with full-year display")],
            [sg.Text("• Fixed visualization issues in session statistics")],
            [sg.Text("• Restored comprehensive documentation and comments")],
            [sg.Text("• Maintained 100% backward compatibility during refactoring")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.4 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Unified session feedback system (notes + ratings combined)")],
            [sg.Text("• Enhanced rating comparison widget")],
            [sg.Text("• Improved session visualization with heatmaps")],
            [sg.Text("• Status change timeline tracking")],
            [sg.Text("• Auto-calculated ratings from session data")],
            [sg.Text("• Expanded Help menu with comprehensive guides")],
            [sg.Text("• Emoji rendering system for better visual experience")],
            [sg.Text("• Better data migration system")],
            [sg.Text("• Enhanced chart performance and error handling")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.3 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Added Statistics tab with detailed analytics")],
            [sg.Text("• Session tracking with pause/resume functionality")],
            [sg.Text("• Rating system with tags and comments")],
            [sg.Text("• Data visualization improvements")],
            [sg.Text("• Excel import functionality")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.2 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Summary tab with charts and statistics")],
            [sg.Text("• Enhanced time tracking")],
            [sg.Text("• Improved data management")],
            [sg.Text("• Better search and filtering")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.1 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Basic game management")],
            [sg.Text("• Simple time tracking")],
            [sg.Text("• File save/load functionality")],
            [sg.Text("• Initial release")],
            [sg.Text("")],
            [emoji_image(get_emoji('crystal_ball'), size=16), sg.Text(" UPCOMING FEATURES (Planned):", font=('Arial', 12, 'bold'))],
            [sg.Text("• Cloud sync capabilities")],
            [sg.Text("• Mobile companion app")],
            [sg.Text("• Advanced filtering options")],
            [sg.Text("• Custom chart creation")],
            [sg.Text("• Social features (share collections)")],
            [sg.Text("• Game recommendation engine")],
            [sg.Text("• Achievement tracking")],
            [sg.Text("• Backup automation")],
            [sg.Text("")],
            [sg.Text("=== TECHNICAL NOTES ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Built with Python and PySimpleGUI")],
            [sg.Text("• Uses matplotlib for visualizations")],
            [sg.Text("• JSON-based data storage (.gmd format)")],
            [sg.Text("• Cross-platform compatibility (Windows, Mac, Linux)")],
            [sg.Text("• Modular architecture for easy maintenance")],
            [sg.Text("• Pillow (PIL) for emoji rendering")],
            [sg.Text("")],
            [sg.Text("=== FEEDBACK & CONTRIBUTIONS ===", font=('Arial', 12, 'bold'))],
            [sg.Text("We welcome feedback and contributions!")],
            [sg.Text("• Report bugs via Discord (@drnefarius)")],
            [sg.Text("• Suggest features via Discord (@drnefarius)")],
            [sg.Text("• Share your gaming insights with the community")],
            [sg.Text("• Contribute ideas for new features")],
            [sg.Text("")],
            [sg.Text("Thank you for using Games List Manager!", font=('Arial', 11, 'bold'))]
        ], scrollable=True, vertical_scroll_only=True, size=(750, 500), expand_x=True, expand_y=True)],
        [sg.Button('Close')]
    ]
    
    # Calculate center position relative to parent window
    release_notes_location = None
    if parent_window:
        from utilities import calculate_popup_center_location
        release_notes_location = calculate_popup_center_location(parent_window, popup_width=800, popup_height=600)
    
    release_notes_window = sg.Window('Release Notes', release_notes_layout, modal=True, size=(800, 600), 
                                    icon='gameslisticon.ico', finalize=True, resizable=True, location=release_notes_location)
    
    while True:
        event, values = release_notes_window.read()
        if event in (sg.WIN_CLOSED, 'Close'):
            break
    
    release_notes_window.close()

def show_bug_report_info(parent_window=None):
    """Show bug reporting information with emoji images"""
    
    # Create a custom window with emoji support
    bug_report_layout = [
        [sg.Text("BUG REPORTING & FEEDBACK", font=('Arial', 14, 'bold'), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [emoji_image(get_emoji('bug'), size=18), sg.Text(" REPORTING BUGS", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("When reporting a bug, please include:")],
            [sg.Text("")],
            [emoji_image(get_emoji('book'), size=16), sg.Text(" SYSTEM INFORMATION:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Operating System (Windows 10/11, macOS, Linux distribution)")],
            [sg.Text(f"• Application version (currently {VERSION})")],
            [sg.Text("• Python version (if running from source)")],
            [sg.Text("• Screen resolution and scaling settings")],
            [sg.Text("")],
            [emoji_image(get_emoji('search'), size=16), sg.Text(" BUG DETAILS:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Clear description of what happened")],
            [sg.Text("• Steps to reproduce the issue")],
            [sg.Text("• Expected vs actual behavior")],
            [sg.Text("• Screenshots if applicable")],
            [sg.Text("• Error messages (exact text)")],
            [sg.Text("")],
            [emoji_image(get_emoji('file'), size=16), sg.Text(" DATA INFORMATION:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Size of your .gmd file (number of games/sessions)")],
            [sg.Text("• Whether the issue occurs with new or existing data")],
            [sg.Text("• If the issue started after a specific action")],
            [sg.Text("")],
            [emoji_image(get_emoji('email'), size=16), sg.Text(" HOW TO REPORT:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("DISCORD:")],
            [sg.Text("   • Contact: @drnefarius")],
            [sg.Text("   • Include screenshots and error details")],
            [sg.Text("   • Best for quick questions and clarifications")],
            [sg.Text("   • Include all relevant information listed above")],
            [sg.Text("")],
            [sg.Text("GITHUB ISSUES (Community Support):")],
            [sg.Text("   • Repository: "), sg.Text("https://github.com/DrNefarius/GameTracker", 
                     text_color='blue', enable_events=True, key='-GITHUB-LINK-', 
                     tooltip='Click to open repository in browser')],
            [sg.Text("   • Use for structured bug reports and feature requests")],
            [sg.Text("• Search existing issues before creating new ones")],
            [sg.Text("• Follow the same information guidelines as above")],
            [sg.Text("")],
            [sg.Text("NOTE: There is no in-app bug reporting feature.")],
            [sg.Text("All support requests should go through Discord or GitHub Issues.")],
            [sg.Text("")],
            [emoji_image(get_emoji('rocket'), size=16), sg.Text(" FEATURE REQUESTS:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("Have an idea for improvement?")],
            [sg.Text("• Describe the feature and its benefits")],
            [sg.Text("• Explain your use case")],
            [sg.Text("• Suggest how it might work")],
            [sg.Text("• Consider if it fits the application's scope")],
            [sg.Text("")],
            [emoji_image(get_emoji('handshake'), size=16), sg.Text(" CONTRIBUTING:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("Want to help improve the application?")],
            [sg.Text("• Feature suggestions welcome via Discord")],
            [sg.Text("• Documentation improvements")],
            [sg.Text("• Testing on different platforms")],
            [sg.Text("• UI/UX suggestions")],
            [sg.Text("• Translation assistance")],
            [sg.Text("")],
            [emoji_image(get_emoji('chart'), size=16), sg.Text(" DIAGNOSTIC INFORMATION:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("To help with debugging, you can:")],
            [sg.Text("• Check the console output for error messages")],
            [sg.Text("• Look for backup files if data is corrupted")],
            [sg.Text("• Note the exact sequence of actions that caused the issue")],
            [sg.Text("• Test if the issue occurs with a fresh data file")],
            [sg.Text("")],
            [emoji_image(get_emoji('lightning'), size=16), sg.Text(" QUICK FIXES:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("Before reporting, try these common solutions:")],
            [sg.Text("• Restart the application")],
            [sg.Text("• Check file permissions")],
            [sg.Text("• Verify .gmd file isn't corrupted (open in text editor)")],
            [sg.Text("• Try with a smaller dataset")],
            [sg.Text("• Update to the latest version")],
            [sg.Text("")],
            [emoji_image(get_emoji('pray'), size=16), sg.Text(" THANK YOU:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("Your feedback helps make Games List Manager better for everyone!")],
            [sg.Text("Every bug report and suggestion is valuable for improving the application.")],
            [sg.Text("")],
            [sg.Text("We appreciate your patience and support in making this the best")],
            [sg.Text("game collection manager possible.")]
        ], scrollable=True, vertical_scroll_only=True, size=(750, 500), expand_x=True, expand_y=True)],
        [sg.Button('Close')]
    ]
    
    # Calculate center position relative to parent window
    bug_report_location = None
    if parent_window:
        from utilities import calculate_popup_center_location
        bug_report_location = calculate_popup_center_location(parent_window, popup_width=800, popup_height=600)
    
    bug_report_window = sg.Window('Bug Reporting & Feedback', bug_report_layout, modal=True, size=(800, 600), 
                                 icon='gameslisticon.ico', finalize=True, resizable=True, location=bug_report_location)
    
    while True:
        event, values = bug_report_window.read()
        if event in (sg.WIN_CLOSED, 'Close'):
            break
        elif event == '-GITHUB-LINK-':
            webbrowser.open('https://github.com/DrNefarius/GameTracker')
    
    bug_report_window.close()

def show_about_dialog(parent_window=None):
    """Show enhanced about dialog with emoji images"""
    
    # Get system information
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    os_info = f"{platform.system()} {platform.release()}"
    
    about_layout = [
        [sg.Text("Games List Manager", font=('Arial', 16, 'bold'), justification='center', expand_x=True)],
        [sg.Text(f"Version {VERSION}", font=('Arial', 12), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [emoji_image(get_emoji('game'), size=20), sg.Text(" Manage your game collection with style", justification='center', expand_x=True)],
        [sg.Text("Track playtime • Rate games • Analyze sessions", justification='center', expand_x=True)],
        [sg.VPush()],
        [sg.Frame('Features', [
            [sg.Text("• Comprehensive game library management")],
            [sg.Text("• Advanced time tracking with session analytics")],
            [sg.Text("• Dual rating system (session + game ratings)")],
            [sg.Text("• Rich data visualizations and statistics")],
            [sg.Text("• Session feedback with notes and tags")],
            [sg.Text("• Excel import and .gmd export capabilities")],
            [sg.Text("• Cross-platform compatibility")]
        ], font=('Arial', 10))],
        [sg.VPush()],
        [sg.Frame('Technical Information', [
            [sg.Text(f"Python Version: {python_version}")],
            [sg.Text(f"Operating System: {os_info}")],
            [sg.Text(f"GUI Framework: PySimpleGUI")],
            [sg.Text(f"Charts: Matplotlib")],
            [sg.Text(f"Data Format: JSON (.gmd)")],
            [sg.Text(f"Build Date: {datetime.now().strftime('%Y-%m-%d')}")]
        ], font=('Arial', 9))],
        [sg.VPush()],
        [sg.Frame('Credits', [
            [emoji_image(get_emoji('dev'), size=16), sg.Text(" Developer: @drnefarius", justification='center', expand_x=True)],
            [emoji_image(get_emoji('chat'), size=16), sg.Text(" Discord: @drnefarius", justification='center', expand_x=True)],
            [emoji_image(get_emoji('support'), size=16), sg.Text(" Support: Available through Discord", justification='center', expand_x=True)],
            [emoji_image(get_emoji('community'), size=16), sg.Text(" Community: Join us for gaming discussions!", justification='center', expand_x=True)]
        ], font=('Arial', 10))],
        [sg.VPush()],
        [sg.Frame('License & Legal', [
            [sg.Text("© 2024 Games List Manager", justification='center', expand_x=True)],
            [sg.Text("Licensed under GPL-3.0 License", justification='center', expand_x=True)],
            [sg.Text("This software is provided 'as-is' without warranty.", justification='center', expand_x=True)],
            [sg.Text("Open source components used under their respective licenses.", justification='center', expand_x=True)]
        ], font=('Arial', 9))],
        [sg.VPush()],
        [sg.Button('View Release Notes', key='-RELEASE-NOTES-'), 
         sg.Button('Report Bug', key='-REPORT-BUG-'), 
         sg.Button('Close', key='-CLOSE-')]
    ]
    
    # Calculate center position relative to parent window
    about_location = None
    if parent_window:
        from utilities import calculate_popup_center_location
        about_location = calculate_popup_center_location(parent_window, popup_width=500, popup_height=600)
    
    about_window = sg.Window('About Games List Manager', about_layout, 
                            modal=True, size=(500, 600), icon='gameslisticon.ico', finalize=True, location=about_location)
    
    while True:
        event, values = about_window.read()
        
        if event in (sg.WIN_CLOSED, '-CLOSE-'):
            break
        elif event == '-RELEASE-NOTES-':
            about_window.close()
            show_release_notes(parent_window)
            break
        elif event == '-REPORT-BUG-':
            about_window.close()
            show_bug_report_info(parent_window)
            break
    
    about_window.close() 