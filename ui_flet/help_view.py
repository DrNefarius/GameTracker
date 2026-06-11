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
                    "- Modern Flet (Flutter) interface with light/dark themes",
                    "- Comprehensive game library management",
                    "- Manual timer + automatic session tracking (process watcher)",
                    "- IGDB metadata: covers, genres, summaries, completion times",
                    "- Dual rating system (session + game ratings) with tags",
                    "- Rich visualizations, statistics and a contributions calendar",
                    "- System tray with start-minimized-to-tray",
                    "- Discord Rich Presence",
                    "- Built-in auto-updates",
                    "- Excel import and .gmd export",
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
                    "(c) 2024-2026 GameTracker",
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
- Add games with the **Add game** button on the Games list
- Click a game's row to open the **Game Hub** - edit, rate, add sessions, run the timer, or jump to its statistics
- Use the toolbar menus for everything else

### TOOLBAR
- **File** - Open / Save / Save As / Import from Excel / Quit
- **Library** - IGDB settings, enrich library from IGDB, and the "Remember filter, page & rows" toggle
- **Watcher** - enable/disable the process watcher, "Start minimized to tray", settings, and rescan game libraries
- **Discord** - toggle Discord Rich Presence
- **Updates** - check for updates and update settings
- **Help** - these guides
- **Theme button** - cycle light / dark / system

### GAMES LIST
- Search/filter live as you type (no need to press Enter)
- Click a column header to sort; click the Status cell for a quick status change
- Click a row to open the Game Hub
- Pagination keeps large libraries fast
- Color coding: Green=Completed, Dark Green=Dropped, Yellow=In progress, Purple=Future Release, Red=Pending
- Optionally remember your search, page and rows-per-page between launches (Library menu)

### GAME HUB
- Cover art and IGDB metadata (genres, summary, time-to-beat); re-fetch or change the match
- Inline **Play / Pause / Stop** timer with a live count-up; you're prompted to rate the session on Stop
- Sessions and status history at a glance, plus Edit, Add session, Rate, View Statistics, Delete
- Link an executable so the watcher tracks the game automatically

### AUTOMATIC TRACKING (PROCESS WATCHER)
- Enable it from the Watcher menu to record sessions when you launch a game (Windows)
- Matches are resolved via learned mappings, store manifests (Steam/Epic/GOG), and fuzzy matching
- Toasts notify you on session start/end and when a match needs confirming
- A system-tray icon lets you pause/stop, open or quit; "Start minimized to tray" boots straight to the tray
- Settings let you tune idle-pause, foreground-only tracking, and the end-of-session grace period

### SUMMARY TAB
- Total play time plus charts: status distribution, top games by playtime, games by year, ratings, genres

### STATISTICS TAB
- Overall stats and a per-game scope picker
- Native contributions calendar (today is outlined in red); click a day for its activity, or use Today / Yesterday / Pick a date
- Rating comparison (auto vs manual), sessions and status-history tables
- Tabbed charts: session timeline, distribution, status timeline, and gaming heatmap

### RATINGS SYSTEM
- Rate games and individual sessions 1-5 stars with tags and comments
- Game ratings can be auto-calculated from your session ratings
- Rating comparison shows auto vs manual side by side

### DATA MANAGEMENT
- Data is stored in .gmd files (human-readable JSON); changes auto-save
- Import from Excel (.xlsx); back up with "Save As"
- Window size/position and the loaded database are remembered between launches

### TIPS
- Use tags in ratings to categorize your experience
- Session feedback helps track your gaming journey over time
- The contributions calendar and gaming heatmap reveal your play patterns
- Status changes are tracked automatically with timestamps
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
- Make sure the whole release folder was extracted (keep GameTracker.exe together with its data/ and DLL files)
- Windows SmartScreen / Smart App Control may block the unsigned app on first launch: choose "More info -> Run anyway", or right-click GameTracker.exe -> Properties -> Unblock. Smart App Control (Windows 11) can be turned off under Windows Security -> App & browser control
- Ensure you have read/write permission in that folder
- Check your antivirus isn't blocking the application
- Only one instance runs at a time - if no window appears, look in the system tray

**File loading errors**
- Verify the .gmd file isn't corrupted (it should be valid JSON - open it in a text editor)
- Check file permissions (read/write access)
- If a file can't be read the app starts with an empty library rather than failing

**Time tracking issues**
- Manual timer: data is saved when you press Stop, and you're prompted to rate the session
- Process watcher: enable it from the Watcher menu (Windows). If a game isn't detected, link its executable from the Game Hub or add a mapping in Watcher settings
- Tune idle-pause, foreground-only tracking and the end-of-session grace in Watcher settings
- A session only concludes (and the "rate it" toast appears) after the full end-grace window has elapsed

**A game is tracked under the wrong name**
- Click "Wrong game?" on the session-start toast, or set a mapping in Watcher settings
- Add the game to your library first if it isn't there yet

**Chart / visualization problems**
- Summary charts: use "Refresh Charts" after large changes
- Statistics charts load automatically - switch the game scope or tab to regenerate them
- Large datasets can take a moment to render

**Search not finding a game**
- Search is live and case-insensitive - no need to press Enter
- Clear the search box to show everything again

**Statistics tab is empty**
- Only games with sessions, ratings or status history appear in the scope picker
- Pick "All games" to see your whole library's activity

### DATA RECOVERY

If your data is lost or corrupted:
1. Look in your save directory for recent .gmd files
2. Check the app config for the last-used file path
3. Re-import from Excel if you have a backup spreadsheet
4. .gmd files are plain JSON and can be inspected/repaired in a text editor

### GETTING HELP

If problems persist:
- Contact @drnefarius on Discord (the primary, recommended support channel)
- Or open an issue on GitHub
- Include your OS, the app version (Help -> About) and any error messages
- Watcher issues: attach the logs via Watcher settings -> Open log folder
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
- Click "Add game"
- Enter the name (required); pick a release date with the calendar button
- Set platform, status and ownership

**2. Organize your collection**
- Status flow: Pending -> In progress -> Completed (or Dropped); Future Release for unreleased titles
- Sort by any column, and click a Status cell to change it quickly
- Live search, plus an optional "remember filter, page & rows" toggle (Library menu)

### TIME TRACKING & SESSIONS

**3. Track your gaming time**
- Open a game and use the inline Play / Pause / Stop timer (with a live count-up)
- You're prompted to rate the session when you press Stop
- Or enable the Process Watcher (Watcher menu) to record sessions automatically when you launch a game

**4. Session feedback**
- Rate each session 1-5 stars, add tags and notes
- Review everything in the Statistics tab

### ANALYTICS & INSIGHTS

**5. Summary dashboard**
- Total play time, status distribution, top games by playtime, games by year, ratings and genres

**6. Detailed statistics**
- Contributions calendar (today is outlined in red) - click a day to see its sessions
- Session timeline, distribution, status timeline and a gaming heatmap
- Per-game scope picker, or "All games"

### RATINGS

**7. Dual rating approach**
- Session ratings for each play, plus an overall game rating
- Game ratings can be auto-calculated from your sessions
- Rating comparison shows auto vs manual side by side

**8. Rich rating data**
- 50+ predefined tags (positive, neutral, negative) plus free-text comments
- Tag frequency analysis and rating trends over time

### METADATA & INTEGRATIONS

**9. IGDB metadata**
- Fetch covers, genres, summaries and average completion times
- Re-fetch or change a match per game, or enrich the whole library in one pass (Library menu)

**10. Discord Rich Presence**
- Optionally show what you're playing or browsing on Discord (toggle in the toolbar)

### BACKGROUND & CONVENIENCE

**11. System tray**
- Pause/stop tracking, open or quit from the tray icon
- "Start minimized to tray" (Watcher menu) boots straight to the tray with tracking running

**12. Stays out of your way**
- Window size/position remembered, charts load on demand, and built-in auto-updates keep you current

### DATA MANAGEMENT

**13. Import / export**
- Import from Excel (.xlsx); your data lives in human-readable .gmd (JSON)
- Back up any time with "Save As"

### PRO TIPS
- Use session ratings to track how you feel about a game over time
- The contributions calendar and gaming heatmap reveal your play patterns
- Tags help surface what you enjoy most
- Link executables so the watcher tracks games hands-free

Ready to explore? Add a few games and track a session to get started!
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
**A complete UI rewrite on Flet (Flutter) - a faster, modern, native desktop app.**
- Brand-new redesigned interface with labeled toolbar menus and light/dark/system themes
- Persistent filtering: optionally remember search, page and rows-per-page between launches (sort is always kept)
- Start minimized to the system tray, with a launch-confirmation toast
- Window size/position/maximized state remembered; the loaded database is shown in the title bar
- Interactive contributions calendar: click any day to jump to that day's activity
- Smarter session watcher: configurable end-of-session grace, instant tracking on confirm, and same-game relaunches continue the session
- Release-date picker when adding/editing games
- Rate prompt after stopping the inline session timer
- Startup splash with charts that load on demand
- PySimpleGUI fully removed; packaged builds bundle their own Python runtime

### VERSION 1.11
- Process watcher: automatic session tracking when you launch a game (Windows)
- Smart matching via learned mappings, Steam/Epic/GOG manifests and fuzzy matching
- IGDB metadata: covers, genres, summaries, critic scores and average completion times
- Per-game re-fetch / change match, plus one-pass library enrichment

### VERSION 1.10
- Daily Activity View - review all gaming activity for any specific date
- Chronological session sorting within each day, with daily summaries
- Today/Yesterday shortcuts plus a date picker
- Session details popup with notes, ratings and feedback

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
- Built with Python and Flet (Flutter)
- Uses matplotlib for visualizations
- JSON-based data storage (.gmd format)
- Windows release is fully packaged (bundles its own Python); buildable from source on Linux/macOS
- Pillow for image handling (cover art)
- Modular architecture for easy maintenance

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
