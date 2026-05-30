"""Help / About / informational dialogs for the Flet UI.

This is a dependency-free Flet port of the legacy PySimpleGUI ``help_dialogs.py``.
The legacy module imports PySimpleGUI (via ``sg``) and the emoji-image helpers, so
it cannot be reused directly. Instead the text/structure of each dialog has been
re-created here as Flet controls.

Public API (each takes the Flet ``page`` and opens a modal dialog):
    open_about_dialog(page)
    open_user_guide(page)
    open_release_notes(page)
    open_data_format_info(page)
    open_troubleshooting(page)
    open_feature_tour(page)
    open_bug_report_info(page)

Dialogs are opened via ``page.show_dialog(...)`` and closed via
``page.pop_dialog()`` to match the pattern in ``ui_flet/game_dialog.py``.

Each dialog's body is built by a private ``_build_<name>_content() -> ft.Control``
helper so the content can be constructed/tested without a live page.
"""

import platform
import sys
import webbrowser
from datetime import datetime

import flet as ft

from constants import VERSION

GITHUB_URL = "https://github.com/DrNefarius/GameTracker"

# Width/height of the scrollable body shared by the long informational dialogs.
_BODY_WIDTH = 580
_BODY_HEIGHT = 460


# --------------------------------------------------------------------------- #
# Small shared builders
# --------------------------------------------------------------------------- #
def _scrollable_markdown(markdown_text: str) -> ft.Control:
    """A fixed-size scrollable container rendering selectable markdown."""
    return ft.Container(
        width=_BODY_WIDTH,
        height=_BODY_HEIGHT,
        content=ft.Column(
            [ft.Markdown(markdown_text, selectable=True)],
            scroll=ft.ScrollMode.AUTO,
            tight=True,
        ),
    )


def _open(page, title: str, content: ft.Control, extra_actions=None) -> ft.AlertDialog:
    """Build and show a standard modal dialog with a Close button.

    Returns the dialog (handy for tests/inspection); ``extra_actions`` are
    prepended before the Close button.
    """
    actions = list(extra_actions or [])
    actions.append(ft.TextButton("Close", on_click=lambda _: page.pop_dialog()))
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(title),
        content=content,
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialog)
    return dialog


# --------------------------------------------------------------------------- #
# About
# --------------------------------------------------------------------------- #
def _build_about_content() -> ft.Control:
    python_version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    os_info = f"{platform.system()} {platform.release()}"
    build_date = datetime.now().strftime("%Y-%m-%d")

    def _section(title: str, lines) -> ft.Control:
        return ft.Container(
            content=ft.Column(
                [ft.Text(title, weight=ft.FontWeight.BOLD)]
                + [ft.Text(line, selectable=True) for line in lines],
                spacing=2,
                tight=True,
            ),
            padding=ft.Padding(0, 6, 0, 6),
        )

    body = ft.Column(
        [
            ft.Text("GameTracker", size=22, weight=ft.FontWeight.BOLD),
            ft.Text(f"Version {VERSION}", size=14),
            ft.Text(
                "A simple application to manage your cross-platform games "
                "library and track your gaming sessions.",
                selectable=True,
            ),
            ft.Text("Track playtime - Rate games - Analyze sessions"),
            ft.Divider(),
            _section(
                "Features",
                [
                    "- Comprehensive game library management",
                    "- Advanced time tracking with session analytics",
                    "- Dual rating system (session + game ratings)",
                    "- Rich data visualizations and statistics",
                    "- Session feedback with notes and tags",
                    "- Excel import and .gmd export capabilities",
                    "- Cross-platform compatibility",
                ],
            ),
            _section(
                "Technical Information",
                [
                    f"Python Version: {python_version}",
                    f"Operating System: {os_info}",
                    "GUI Framework: Flet",
                    "Charts: Matplotlib",
                    "Data Format: JSON (.gmd)",
                    f"Build Date: {build_date}",
                ],
            ),
            _section(
                "Credits",
                [
                    "Developer: @drnefarius",
                    "Discord: @drnefarius",
                    "Support: Available through Discord",
                    "Community: Join us for gaming discussions!",
                ],
            ),
            _section(
                "License & Legal",
                [
                    "(c) 2024 GameTracker",
                    "Licensed under GPL-3.0 License",
                    "This software is provided 'as-is' without warranty.",
                    "Open source components used under their respective licenses.",
                ],
            ),
        ],
        scroll=ft.ScrollMode.AUTO,
        tight=True,
        spacing=4,
    )
    return ft.Container(width=_BODY_WIDTH, height=_BODY_HEIGHT, content=body)


def open_about_dialog(page) -> ft.AlertDialog:
    """Show the About dialog with app name, version, description and credits."""
    extra = [
        ft.TextButton(
            "View Release Notes",
            on_click=lambda _: (page.pop_dialog(), open_release_notes(page)),
        ),
        ft.TextButton(
            "Report Bug",
            on_click=lambda _: (page.pop_dialog(), open_bug_report_info(page)),
        ),
    ]
    return _open(page, "About GameTracker", _build_about_content(), extra_actions=extra)


# --------------------------------------------------------------------------- #
# User guide
# --------------------------------------------------------------------------- #
_USER_GUIDE_MD = """\
# GameTracker - User Guide

### GETTING STARTED
- Add games using the "Add Entry" button
- Track time by clicking on a game and selecting "Track Time"
- Edit games by clicking on them and selecting "Edit Game"
- Rate games using the "Rate Game" option

### MAIN FEATURES

**Games List Tab**
- View all your games in a sortable table
- Search/filter games using the search box
- Click column headers to sort by that column
- Double-click a game for the action menu
- Color coding: Green=Completed, Dark Green=Dropped, Yellow=In progress, Purple=Future Release, Red=Pending

**Time Tracking**
- Click "Track Time" to start a session timer
- Use Play/Pause/Stop controls
- Add session feedback (notes + ratings) when stopping
- Sessions are automatically saved to your game data

**Summary Tab**
- View statistics about your game collection
- Charts showing status distribution, release years, playtime, and ratings
- Refresh charts with the "Refresh Charts" button

**Statistics Tab**
- Detailed session analysis and visualizations
- Select specific games to view their session history
- View session feedback, ratings, and status changes
- Interactive charts: timeline, distribution, heatmap, status changes

### RATINGS SYSTEM
- Rate games 1-5 stars with optional tags and comments
- Session ratings: Rate individual gaming sessions
- Game ratings: Overall rating for the entire game
- Auto-calculated ratings: Automatically calculated from session ratings
- Rating comparison: Compare session-based vs manual ratings

### DATA MANAGEMENT
- Files are saved in .gmd format (JSON-based)
- Auto-save when tracking time or making changes
- Import from Excel files (.xlsx)
- Export/backup using "Save As"

### TIPS
- Use tags in ratings to categorize your experience
- Session feedback helps track your gaming journey
- The heatmap shows your gaming patterns and break habits
- Status changes are automatically tracked with timestamps
"""


def _build_user_guide_content() -> ft.Control:
    return _scrollable_markdown(_USER_GUIDE_MD)


def open_user_guide(page) -> ft.AlertDialog:
    """Show the comprehensive user guide."""
    return _open(page, "User Guide", _build_user_guide_content())


# --------------------------------------------------------------------------- #
# Data format info
# --------------------------------------------------------------------------- #
_DATA_FORMAT_MD = """\
# Data Format Information

### FILE FORMATS

**.GMD FILES (Games Manager Data)**
- Primary format used by GameTracker
- JSON-based structure for easy reading and backup
- Contains all game data, sessions, ratings, and history
- Automatically versioned for future compatibility

**Excel Import (.xlsx)**
- Import existing game lists from Excel spreadsheets
- Expected columns: Name, Release Date, Platform, Time, Status, Owned, Last Played
- Automatically converts to .gmd format after import

### DATA STRUCTURE

Each game entry contains:
- Basic Info: Name, Release Date, Platform, Status, Ownership
- Time Data: Total playtime, Last played date
- Sessions: Individual gaming sessions with timestamps, duration, feedback
- Ratings: Both game-level and session-level ratings with tags and comments
- History: Status change tracking with timestamps

### SESSION DATA
Sessions include:
- Start/End timestamps
- Duration tracking
- Pause/Resume information
- Unified feedback system (notes + ratings)
- Automatic session statistics

### BACKUP RECOMMENDATIONS
- Regular backups using "Save As" to different locations
- .gmd files are human-readable JSON for easy recovery
- Consider cloud storage for automatic backup
- Export important data before major updates

### MIGRATION
- Automatic migration from older data formats
- Unified feedback system migration (notes + ratings combined)
- Backward compatibility maintained where possible
- Migration status shown during file loading

### FILE LOCATIONS
- Default save location: User's home directory
- Config files: Platform-specific application data folders
- Temporary chart files: System temp directory (auto-cleaned)
"""


def _build_data_format_info_content() -> ft.Control:
    return _scrollable_markdown(_DATA_FORMAT_MD)


def open_data_format_info(page) -> ft.AlertDialog:
    """Show information about data formats and file structure."""
    return _open(page, "Data Format Information", _build_data_format_info_content())


# --------------------------------------------------------------------------- #
# Troubleshooting
# --------------------------------------------------------------------------- #
_TROUBLESHOOTING_MD = """\
# Troubleshooting Guide

### COMMON ISSUES

**Application won't start**
- Check if gameslisticon.ico is in the same folder as the executable
- Ensure you have sufficient permissions in the installation directory
- Try running as administrator (Windows) or with sudo (Linux/Mac)
- Check antivirus software isn't blocking the application

**File loading errors**
- Verify the .gmd file isn't corrupted (should be valid JSON)
- Check file permissions - ensure read/write access
- Try opening the file in a text editor to verify it's not empty
- Backup files are created automatically if corruption is detected

**Time tracking issues**
- If timer doesn't start, check if another instance is running
- Timer data is saved automatically when stopped
- If session data is lost, check the last saved .gmd file
- Pause/resume functionality requires proper session start

**Chart/visualization problems**
- Charts not loading: Try refreshing with the "Refresh Charts" button
- Missing data: Ensure games have the required data (dates, times, ratings)
- Performance issues: Large datasets may take time to generate charts
- Display issues: Try resizing the window or switching tabs

**Search not working**
- Ensure you press Enter after typing in the search box
- Search is case-insensitive and searches all visible columns
- Use "Reset" button to clear search filters
- Special characters in game names may affect search

**Statistics tab issues**
- No data showing: Ensure games have session data or status history
- Game not in list: Only games with sessions/ratings/status changes appear
- Charts not updating: Use "Refresh Statistics" button
- Performance: Large session datasets may take time to process

### DATA RECOVERY

If your data is lost or corrupted:
1. Check for backup files (*.backup-YYYYMMDDHHMMSS)
2. Look in the default save directory for recent .gmd files
3. Check the application config for the last used file path
4. Import from Excel if you have a backup spreadsheet

### PERFORMANCE OPTIMIZATION

For better performance with large datasets:
- Regularly clean up old session data if not needed
- Use search/filtering to work with smaller subsets
- Close other applications when generating complex charts
- Consider splitting very large game collections into multiple files

### GETTING HELP

If problems persist:
- Contact @drnefarius on Discord for support
- Discord is the primary and recommended support channel
- Include your operating system and application version
- Attach relevant error messages or log files
"""


def _build_troubleshooting_content() -> ft.Control:
    return _scrollable_markdown(_TROUBLESHOOTING_MD)


def open_troubleshooting(page) -> ft.AlertDialog:
    """Show the troubleshooting guide."""
    return _open(page, "Troubleshooting Guide", _build_troubleshooting_content())


# --------------------------------------------------------------------------- #
# Feature tour
# --------------------------------------------------------------------------- #
_FEATURE_TOUR_MD = """\
# Feature Tour - Discover What's Possible

### BASIC GAME MANAGEMENT

**1. Add your first game**
- Click "Add Entry" button
- Fill in game name (required)
- Add release date, platform, initial status
- Set ownership status with checkbox

**2. Organize your collection**
- Use status: Pending -> In progress -> Completed (or Dropped)
- Track ownership with the checkbox
- Sort by any column (click headers)
- Search to find specific games quickly

### TIME TRACKING & SESSIONS

**3. Track your gaming time**
- Click any game -> "Track Time"
- Use Play/Pause/Stop controls
- Add session feedback when done (notes + rating)
- Time automatically adds to total playtime

**4. Session feedback system**
- Rate individual sessions (1-5 stars)
- Add tags to categorize experience
- Write detailed notes about your session
- View all feedback in the Statistics tab

### ANALYTICS & INSIGHTS

**5. Summary dashboard**
- Status distribution pie chart
- Games by release year
- Top games by playtime
- Rating distribution analysis

**6. Detailed statistics**
- Session timeline visualization
- Gaming heatmap (shows when you play)
- Session length distribution
- Status change timeline

### ADVANCED RATING SYSTEM

**7. Dual rating approach**
- Session ratings: Rate each gaming session
- Game ratings: Overall rating for the entire game
- Auto-calculated ratings: Computed from session ratings
- Rating comparison: See how session vs game ratings differ

**8. Rich rating data**
- 50+ predefined tags (positive, neutral, negative)
- Custom comments for detailed feedback
- Tag frequency analysis
- Rating trends over time

### DATA VISUALIZATION

**9. Interactive charts**
- Click and explore different visualizations
- Refresh data with dedicated buttons
- Export charts (screenshot capability)
- Responsive design adapts to window size

**10. Session analysis**
- Gaming heatmap shows daily patterns
- Pause analysis (focused vs interrupted sessions)
- Session length trends
- Most active gaming periods

### POWER USER FEATURES

**11. Data management**
- Import from Excel spreadsheets
- Export to .gmd format for backup
- Automatic data migration between versions
- Human-readable JSON format

**12. Customization**
- Configurable save locations
- Persistent window settings
- Automatic session saving
- Flexible data filtering

### PRO TIPS
- Use session ratings to track how you feel about games over time
- The heatmap reveals your gaming habits and optimal play times
- Tags help identify what you enjoy most in games
- Status history shows your gaming journey progression
- Regular backups ensure your gaming history is preserved

Ready to explore? Start with adding a few games and tracking some sessions!
"""


def _build_feature_tour_content() -> ft.Control:
    return _scrollable_markdown(_FEATURE_TOUR_MD)


def open_feature_tour(page) -> ft.AlertDialog:
    """Show the feature tour / walkthrough."""
    return _open(page, "Feature Tour", _build_feature_tour_content())


# --------------------------------------------------------------------------- #
# Release notes
# --------------------------------------------------------------------------- #
_RELEASE_NOTES_MD = f"""\
# Release Notes

### VERSION {VERSION} (Current)
**New features**
- Daily Activity View - View all gaming activity for any specific date
- Chronological session sorting by time within each day
- Date picker for selecting any date to review gaming history
- Daily summaries with total time played and session count
- Multiple access points: Today/Yesterday shortcuts and date picker
- Session details popup with notes, ratings, and feedback
- Discord Rich Presence integration for daily activity viewing

### VERSION 1.9
- Complete auto-updater system with GitHub releases integration
- One-click update downloads with progress tracking and cancellation
- Intelligent staging system to handle file locking during updates
- Cross-platform updater scripts (Windows batch, Unix shell)
- Existing download detection to avoid re-downloading same versions
- Post-update success notifications with version information
- Rich release notes display with image loading and HTML rendering

### VERSION 1.8
- Manual session addition - Add gaming sessions with custom start/end times
- Dual input methods for manual sessions (start+end times OR duration+end time)
- Full session feedback support for manually added sessions (notes, ratings, tags)
- Streamlined action dialog with single-row button layout
- Comprehensive import cleanup across all modules
- Enhanced session management with improved modularity
- Better code organization with focused module separation
- Fixed manual session dialog buttons disappearing when toggling checkboxes
- Resolved window resizing issues in manual session popup
- Improved dialog stability and user experience

### VERSION 1.7
- Discord Rich Presence integration with platform information
- Enhanced session tracking with platform-aware Discord status
- Improved Discord presence messages for gaming sessions
- Better integration between session management and Discord updates

### VERSION 1.6
- GitHub-style contributions heatmap visualization
- Year navigation for contributions view (previous/next year)
- Enhanced table color refresh after status changes
- Improved data consistency in filtered views
- Fixed table row colors not updating after status changes
- Resolved contributions heatmap display issues
- Better error handling for contributions visualization

### VERSION 1.5
- Enhanced session distribution charts (scatter plot, box plot)
- Improved contributions map with full-year display
- Fixed visualization issues in session statistics
- Restored comprehensive documentation and comments
- Maintained 100% backward compatibility during refactoring

### VERSION 1.4
- Unified session feedback system (notes + ratings combined)
- Enhanced rating comparison widget
- Improved session visualization with heatmaps
- Status change timeline tracking
- Auto-calculated ratings from session data
- Expanded Help menu with comprehensive guides
- Emoji rendering system for better visual experience
- Better data migration system
- Enhanced chart performance and error handling

### VERSION 1.3
- Added Statistics tab with detailed analytics
- Session tracking with pause/resume functionality
- Rating system with tags and comments
- Data visualization improvements
- Excel import functionality

### VERSION 1.2
- Summary tab with charts and statistics
- Enhanced time tracking
- Improved data management
- Better search and filtering

### VERSION 1.1
- Basic game management
- Simple time tracking
- File save/load functionality
- Initial release

### UPCOMING FEATURES (Planned)
- Cloud sync capabilities
- Mobile companion app
- Advanced filtering options
- Custom chart creation
- Social features (share collections)
- Game recommendation engine
- Achievement tracking
- Backup automation

### TECHNICAL NOTES
- Built with Python and Flet
- Uses matplotlib for visualizations
- JSON-based data storage (.gmd format)
- Cross-platform compatibility (Windows, Mac, Linux)
- Modular architecture for easy maintenance
- Pillow (PIL) for emoji rendering

### FEEDBACK & CONTRIBUTIONS
We welcome feedback and contributions!
- Report bugs via Discord (@drnefarius)
- Suggest features via Discord (@drnefarius)
- Share your gaming insights with the community
- Contribute ideas for new features

Thank you for using GameTracker!
"""


def _build_release_notes_content() -> ft.Control:
    return _scrollable_markdown(_RELEASE_NOTES_MD)


def open_release_notes(page) -> ft.AlertDialog:
    """Show release notes and version history."""
    return _open(page, "Release Notes", _build_release_notes_content())


# --------------------------------------------------------------------------- #
# Bug report info
# --------------------------------------------------------------------------- #
_BUG_REPORT_MD = f"""\
# Bug Reporting & Feedback

## REPORTING BUGS

When reporting a bug, please include:

**System information**
- Operating System (Windows 10/11, macOS, Linux distribution)
- Application version (currently {VERSION})
- Python version (if running from source)
- Screen resolution and scaling settings

**Bug details**
- Clear description of what happened
- Steps to reproduce the issue
- Expected vs actual behavior
- Screenshots if applicable
- Error messages (exact text)

**Data information**
- Size of your .gmd file (number of games/sessions)
- Whether the issue occurs with new or existing data
- If the issue started after a specific action

## HOW TO REPORT

**Discord**
- Contact: @drnefarius
- Include screenshots and error details
- Best for quick questions and clarifications
- Include all relevant information listed above

**GitHub Issues (Community Support)**
- Repository: {GITHUB_URL}
- Use for structured bug reports and feature requests
- Search existing issues before creating new ones
- Follow the same information guidelines as above

NOTE: There is no in-app bug reporting feature.
All support requests should go through Discord or GitHub Issues.

## FEATURE REQUESTS

Have an idea for improvement?
- Describe the feature and its benefits
- Explain your use case
- Suggest how it might work
- Consider if it fits the application's scope

## CONTRIBUTING

Want to help improve the application?
- Feature suggestions welcome via Discord
- Documentation improvements
- Testing on different platforms
- UI/UX suggestions
- Translation assistance

## DIAGNOSTIC INFORMATION

To help with debugging, you can:
- Check the console output for error messages
- Look for backup files if data is corrupted
- Note the exact sequence of actions that caused the issue
- Test if the issue occurs with a fresh data file

## QUICK FIXES

Before reporting, try these common solutions:
- Restart the application
- Check file permissions
- Verify .gmd file isn't corrupted (open in text editor)
- Try with a smaller dataset
- Update to the latest version

## THANK YOU

Your feedback helps make GameTracker better for everyone!
Every bug report and suggestion is valuable for improving the application.

We appreciate your patience and support in making this the best
game collection manager possible.
"""


def _build_bug_report_info_content() -> ft.Control:
    return ft.Container(
        width=_BODY_WIDTH,
        height=_BODY_HEIGHT,
        content=ft.Column(
            [
                ft.Markdown(
                    _BUG_REPORT_MD,
                    selectable=True,
                    on_tap_link=lambda e: webbrowser.open(e.data),
                ),
                ft.Button(
                    "Open GitHub Repository",
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=lambda _: webbrowser.open(GITHUB_URL),
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            tight=True,
        ),
    )


def open_bug_report_info(page) -> ft.AlertDialog:
    """Show bug reporting and feedback information."""
    return _open(page, "Bug Reporting & Feedback", _build_bug_report_info_content())
