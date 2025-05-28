"""
Statistics-related functionality for the GamesList application.
Contains functions for statistical calculations and summary functions.
"""

from datetime import datetime, timedelta
from collections import defaultdict

from constants import COMPLETED_STYLE, IN_PROGRESS_STYLE, FUTURE_RELEASE_STYLE, DEFAULT_STYLE
from utilities import format_timedelta_with_seconds, get_game_table_row_colors

def count_total_completed(data_with_indices):
    """Count the total number of completed games"""
    return sum(1 for row in data_with_indices if row[1][4] == 'Completed')

def count_total_entries(data):
    """Count the total number of game entries"""
    return len(data)

def calculate_completion_percentage(total_completed, total_entries):
    """Calculate the percentage of completed games"""
    if total_entries == 0:
        return 0
    return (total_completed / total_entries) * 100

def calculate_total_time(data):
    """Calculate the total time played across all games"""
    total_seconds = 0
    try:
        for entry in data:
            try:
                # Handle different possible data structures
                if isinstance(entry, tuple) and len(entry) > 1:
                    # Case: (index, [data_list])
                    row_data = entry[1]
                    if isinstance(row_data, list) and len(row_data) > 3:
                        time_str = row_data[3]
                    else:
                        continue  # Skip if invalid data
                elif isinstance(entry, list) and len(entry) > 3:
                    # Case: direct data list
                    time_str = entry[3]
                else:
                    continue  # Skip if invalid data
                    
                if not time_str or time_str in ['', '00:00', '00:00:00']:
                    continue
                    
                if isinstance(time_str, timedelta):
                    total_seconds += time_str.total_seconds()
                else:
                    try:
                        # Try HH:MM:SS format
                        parts = time_str.split(':')
                        if len(parts) == 3:
                            hours, minutes, seconds = map(int, parts)
                            total_seconds += hours * 3600 + minutes * 60 + seconds
                        elif len(parts) == 2:
                            hours, minutes = map(int, parts)
                            total_seconds += hours * 3600 + minutes * 60
                    except (ValueError, AttributeError):
                        print(f"Warning: Could not parse time format '{time_str}'")
                        continue
            except (IndexError, TypeError):
                continue  # Skip invalid entries
                
        total_time = timedelta(seconds=total_seconds)
        return format_timedelta_with_seconds(total_time)
    except Exception as e:
        print(f"Error calculating total time: {str(e)}")
        return "00:00:00"

def breakdown_by_year_and_status(data_with_indices):
    """Create a breakdown of games by year and status"""
    breakdown = defaultdict(lambda: {'Completed': 0, 'Pending/In Progress': 0})
    for t_row in data_with_indices:
        row = t_row[1]
        year = row[1].split('-')[0] if row[1] != '-' else 'Unknown'
        if row[4] == 'Completed':
            breakdown[year]['Completed'] += 1
        else:
            breakdown[year]['Pending/In Progress'] += 1
    return dict(breakdown)

def update_summary(data_with_indices, window):
    """Update the summary display with current data"""
    total_completed = count_total_completed(data_with_indices)
    total_entries = count_total_entries(data_with_indices)
    completion_percentage = calculate_completion_percentage(total_completed, total_entries)
    breakdown = breakdown_by_year_and_status(data_with_indices)
    breakdown_lines = [f"{year}: {status['Completed']} Completed, {status['Pending/In Progress']} Pending/In Progress"
                       for year, status in breakdown.items()]
    breakdown_text = '\n'.join(breakdown_lines)

    # Update row colors based on status and rating type
    from ui_components import get_display_row_with_rating
    
    # Get formatted display values
    display_values = [get_display_row_with_rating(row[1]) for row in data_with_indices]
    
    # Get row colors that consider both status and rating type
    row_colors = get_game_table_row_colors(data_with_indices)
    
    # Update the table with formatted data and enhanced colors
    window['-TABLE-'].update(
        values=display_values, 
        row_colors=row_colors
    ) 