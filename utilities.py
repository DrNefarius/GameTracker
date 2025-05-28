"""
Utility functions for the GamesList application.
Contains various helper functions for formatting, calculations, and data processing.
"""

import tkinter as tk
from datetime import timedelta, datetime

from constants import STAR_FILLED, STAR_EMPTY, COMPLETED_STYLE, IN_PROGRESS_STYLE, FUTURE_RELEASE_STYLE, DEFAULT_STYLE

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
    """Calculate the width of a string in pixels"""
    root = tk.Tk()
    root.withdraw()
    label = tk.Label(root, text=text, font=font)
    label.pack()
    width = label.winfo_reqwidth()
    root.destroy()
    return width

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