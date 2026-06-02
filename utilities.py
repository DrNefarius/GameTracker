"""
Utility functions for the GamesList application.
Contains various helper functions for formatting, calculations, and data processing.
"""

try:
    import tkinter as tk
    import tkinter.font as tkfont
except ImportError:
    # tkinter is NOT bundled in the packaged (flet build) app — serious_python's
    # embedded CPython strips it (the native build even deletes tcl86t/tk86t.dll).
    # Only the legacy PySimpleGUI table-pixel-width and popup-centering helpers
    # below use it, and every one of them already falls back gracefully when it's
    # unavailable, so the Flet UI never needs tkinter at all. The legacy
    # PySimpleGUI entry (main.py) still runs against a normal Python where
    # tkinter is present, so it is unaffected.
    tk = None
    tkfont = None
from datetime import timedelta, datetime

from constants import STAR_FILLED, STAR_EMPTY, COMPLETED_STYLE, DROPPED_STYLE, IN_PROGRESS_STYLE, FUTURE_RELEASE_STYLE, DEFAULT_STYLE

# A single hidden Tk root + per-font Font measurer, shared across all calls to
# calculate_pixel_width so we don't pay the cost of creating/destroying a Tk
# instance for every cell when the table is built.
_pixel_width_root = None
_pixel_width_font_cache = {}


def _get_pixel_width_root():
    global _pixel_width_root
    if _pixel_width_root is None:
        _pixel_width_root = tk.Tk()
        _pixel_width_root.withdraw()
    return _pixel_width_root


def _get_font_measurer(font):
    """Return a cached tkinter.font.Font instance for the given (family, size[, style]) tuple."""
    key = tuple(font) if isinstance(font, (list, tuple)) else (font,)
    cached = _pixel_width_font_cache.get(key)
    if cached is not None:
        return cached
    root = _get_pixel_width_root()
    try:
        if len(key) >= 2:
            measurer = tkfont.Font(root=root, family=key[0], size=key[1])
        else:
            measurer = tkfont.Font(root=root, family=key[0])
    except tk.TclError:
        # Fall back to a default Font if the requested family/size is unavailable.
        measurer = tkfont.Font(root=root)
    _pixel_width_font_cache[key] = measurer
    return measurer

def is_console_platform(platform):
    """Return True if ``platform`` is anything other than PC.

    The app treats the platform string as the source of truth for "is
    this a desktop game we can auto-track?" - if it's PC, the watcher
    looks for a matching process; if it's anything else (PlayStation,
    Switch, an emulator, an arcade cabinet, an unrecognized handheld,
    a homebrew system the keyword list never knew about, ...) the
    title is surfaced in the tray's manual "Start Console Session"
    flow instead.

    We deliberately don't try to enumerate every console - the prior
    keyword list missed plenty (PS1 via "psx", Atari, MSX, anything
    new) and gained nothing for it. A "platform contains 'pc'" check
    catches the only case the watcher actually needs to special-case.

    Empty / missing platform returns False so an unannotated entry
    isn't surfaced in console-only menus by default; the user can
    correct the platform if they want it to appear there.
    """
    if not platform:
        return False
    pl = platform.strip().lower()
    if not pl:
        return False
    return 'pc' not in pl


def iter_game_rows(data):
    """Yield the inner game row (list) from each entry in ``data`` regardless of shape.
    
    The codebase stores game entries in a few shapes depending on context:
    - ``(index, [name, release, platform, time, status, ...])``
    - ``[name, release, platform, time, status, ...]`` (raw row)
    - ``{...}`` dict (legacy)
    
    Rather than repeating the 20-line shape-normalization block in every chart /
    stat function, use this helper to get a stable ``list`` row. Entries that
    don't fit any known shape are skipped silently.
    """
    for entry in data:
        try:
            if isinstance(entry, tuple) and len(entry) > 1:
                row = entry[1]
                if isinstance(row, list):
                    yield row
                    continue
            if isinstance(entry, list):
                yield entry
                continue
            # Unknown shape; skip rather than explode.
        except (IndexError, TypeError):
            continue


def format_timedelta(td):
    """Format timedelta as HH:MM"""
    if td is None:
        return "00:00"
    if not isinstance(td, timedelta):
        try:
            # Try to convert string like "HH:MM" to timedelta
            hours, minutes = map(int, str(td).split(':'))
            td = timedelta(hours=hours, minutes=minutes)
        except (ValueError, TypeError):
            return "00:00"
    
    total_minutes = int(td.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f'{hours:02}:{minutes:02}'

def format_timedelta_with_seconds(td):
    """Format timedelta as HH:MM:SS"""
    if td is None:
        return "00:00:00"
    if not isinstance(td, timedelta):
        try:
            # Try to convert string like "HH:MM:SS" to timedelta
            parts = str(td).split(':')
            if len(parts) == 3:
                hours, minutes, seconds = map(int, parts)
                td = timedelta(hours=hours, minutes=minutes, seconds=seconds)
            elif len(parts) == 2:
                hours, minutes = map(int, parts)
                td = timedelta(hours=hours, minutes=minutes)
            else:
                return "00:00:00"
        except (ValueError, TypeError):
            return "00:00:00"
    
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'

def calculate_pixel_width(text, font=('Helvetica', 10)):
    """Calculate the width of a string in pixels.
    
    Uses a single shared hidden Tk root and cached tkinter.font.Font.measure()
    instead of creating a new Tk instance per call, which was extremely slow
    and could crash on some platforms when called in tight loops.
    """
    try:
        measurer = _get_font_measurer(font)
        return measurer.measure(str(text) if text is not None else "")
    except Exception as e:
        # If Tk is unavailable (headless env, etc.), fall back to a rough estimate.
        print(f"calculate_pixel_width fallback for font={font}: {e}")
        size = font[1] if isinstance(font, (list, tuple)) and len(font) > 1 else 10
        return int(len(str(text) if text is not None else "") * size * 0.6)

def safe_sort_by_date(data, column_index, reverse=False):
    """Safely sort data by date, handling missing and invalid dates"""
    def sort_key(item):
        value = item[1][column_index]
        if not value or value == '-':
            # Sort missing dates to the end by default
            return (1, '9999-12-31') if not reverse else (1, '0001-01-01')
        
        try:
            # Try to parse as date
            date_obj = datetime.strptime(value, '%Y-%m-%d')
            return (0, value)
        except ValueError:
            # If not a valid date, sort as string
            return (2, value)
            
    return sorted(data, key=sort_key, reverse=reverse)

def safe_sort_by_time(data, column_index, reverse=False):
    """Safely sort data by time, handling missing and invalid times"""
    def time_to_seconds(time_str):
        if not time_str or time_str in ['', '00:00', '00:00:00']:
            return 0
            
        try:
            parts = str(time_str).split(':')
            if len(parts) == 3:
                hours, minutes, seconds = map(int, parts)
                return hours * 60 * 60 + minutes * 60 + seconds
            elif len(parts) == 2:
                hours, minutes = map(int, parts)
                return hours * 60 * 60 + minutes * 60
            else:
                return 0
        except (ValueError, AttributeError):
            return 0
            
    return sorted(data, key=lambda x: time_to_seconds(x[1][column_index]), reverse=reverse)

def get_session_row_colors(display_data):
    """Generate row colors for sessions based on their feedback and ratings"""
    row_colors = []
    for i, row in enumerate(display_data):
        if isinstance(row, list) and len(row) > 2:
            details = str(row[2])
            if "[FEEDBACK]" in details and any(star in details for star in [STAR_FILLED, STAR_EMPTY]):
                # Purple background for sessions with both feedback text and ratings
                row_colors.append((i, '#000000', '#e6d0f2'))  # Black text on light purple background
            elif "[FEEDBACK]" in details:
                # Light blue background for sessions with feedback text only
                row_colors.append((i, '#000000', '#d4e6f1'))  # Black text on light blue background
            elif any(star in details for star in [STAR_FILLED, STAR_EMPTY]):
                # Light gold background for sessions with ratings only
                row_colors.append((i, '#000000', '#fef3d1'))  # Black text on light gold background
            else:
                # Default colors for rows without feedback or ratings
                row_colors.append((i, '#000000', '#ffffff'))  # Black text on white background
        else:
            # Fallback colors
            row_colors.append((i, '#000000', '#ffffff'))  # Black text on white background
    return row_colors

def get_game_table_row_colors(data_with_indices):
    """Generate row colors for the main game table based on status only"""
    row_colors = []
    
    for i, (idx, row) in enumerate(data_with_indices):
        # Get base color from status (no special handling for calculated ratings)
        if row[4] == 'Completed':
            base_style = COMPLETED_STYLE
        elif row[4] == 'Dropped':
            base_style = DROPPED_STYLE
        elif row[4] == 'In progress':
            base_style = IN_PROGRESS_STYLE
        else:
            try:
                if row[1] == '-' or datetime.strptime(row[1], '%Y-%m-%d') > datetime.now():
                    base_style = FUTURE_RELEASE_STYLE
                else:
                    base_style = DEFAULT_STYLE
            except ValueError:
                base_style = DEFAULT_STYLE
        
        # Use the standard colors without any modifications
        row_colors.append((i, base_style[0], base_style[1]))
    
    return row_colors

def get_monitor_center_location(popup_width=400, popup_height=300):
    """
    Calculate the center position for a popup window on the monitor.
    
    Args:
        popup_width: Expected width of the popup window in pixels
        popup_height: Expected height of the popup window in pixels
    
    Returns:
        Tuple (x, y) representing the center location for the popup window
    """
    try:
        # Get screen dimensions using tkinter (now packaged with cx_Freeze)
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        
        root.destroy()
        
        # Calculate center position
        popup_x = (screen_width - popup_width) // 2
        popup_y = (screen_height - popup_height) // 2
        
        # Ensure minimum position (in case of negative values)
        popup_x = max(0, popup_x)
        popup_y = max(0, popup_y)
        
        return (popup_x, popup_y)
        
    except Exception as e:
        print(f"Warning: Could not get monitor center position: {e}")
        # Fallback to reasonable default position for common screen resolution
        popup_x = max(100, (1920 - popup_width) // 2)  # Assume 1920x1080
        popup_y = max(100, (1080 - popup_height) // 2)
        return (popup_x, popup_y)

def calculate_popup_center_location(parent_window, popup_width=400, popup_height=300):
    """
    Calculate the center position for a popup window relative to the parent window.
    If parent_window is None or unavailable, center on monitor instead.
    
    Args:
        parent_window: The main window (PySimpleGUI Window object) or None
        popup_width: Expected width of the popup window in pixels
        popup_height: Expected height of the popup window in pixels
    
    Returns:
        Tuple (x, y) representing the location for the popup window
    """
    # If no parent window, use monitor center
    if parent_window is None:
        return get_monitor_center_location(popup_width, popup_height)
    
    try:
        # Get the parent window's position and size
        parent_location = parent_window.current_location()
        parent_size = parent_window.size
        
        if parent_location is None or parent_size is None:
            # If we can't get parent window info, use monitor center
            return get_monitor_center_location(popup_width, popup_height)
        
        # Calculate center position relative to parent
        parent_x, parent_y = parent_location
        parent_width, parent_height = parent_size
        
        # Center the popup on the parent window
        popup_x = parent_x + (parent_width - popup_width) // 2
        popup_y = parent_y + (parent_height - popup_height) // 2
        
        # Get screen dimensions for bounds checking
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            screen_width = root.winfo_screenwidth()
            screen_height = root.winfo_screenheight()
            root.destroy()
            
            # Make sure the popup doesn't go off screen
            popup_x = max(0, min(popup_x, screen_width - popup_width))
            popup_y = max(0, min(popup_y, screen_height - popup_height))
            
        except Exception as e:
            print(f"Warning: Could not get screen dimensions for bounds checking: {e}")
            # Basic bounds checking without screen dimensions
            popup_x = max(100, popup_x)
            popup_y = max(100, popup_y)
        
        return (popup_x, popup_y)
        
    except Exception as e:
        # If anything goes wrong, use monitor center as fallback
        print(f"Warning: Could not calculate popup center position relative to parent: {e}")
        return get_monitor_center_location(popup_width, popup_height)
 