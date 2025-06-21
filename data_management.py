"""
Data management for the GamesList application.
Handles loading, saving, and converting game data files.
"""

import json
import os
import openpyxl
from datetime import datetime, timedelta
from utilities import format_timedelta_with_seconds
from config import load_config, save_config

def save_to_gmd(data, filename):
    """Save game data to a .gmd file"""
    # Ensure directory exists
    directory = os.path.dirname(filename)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
        
    games_data = []
    for _, row in data:
        # Handle timedelta objects in time_played
        time_played = row[3]
        if isinstance(time_played, timedelta):
            time_played = format_timedelta_with_seconds(time_played)
        
        # Get sessions for this game if available (element at index 7)
        sessions = row[7] if len(row) > 7 and row[7] is not None else []
        
        # Get status history for this game if available (element at index 8)
        status_history = row[8] if len(row) > 8 and row[8] is not None else []
        
        # Get rating for this game if available (element at index 9)
        rating = row[9] if len(row) > 9 else None
        
        # Ensure values are properly formatted for JSON
        game = {
            'name': row[0] if row[0] else '',
            'release_date': row[1] if row[1] else '',
            'platform': row[2] if row[2] else '',
            'time_played': time_played if time_played else '',
            'status': row[4] if row[4] else 'Pending',
            'owned': row[5] == '✅',
            'last_played': row[6] if row[6] else None,
            'sessions': sessions,  # Add sessions to the JSON (now includes notes)
            'status_history': status_history,  # Add status history to the JSON
            'rating': rating  # Add rating to the JSON
        }
        games_data.append(game)
    
    try:
        with open(filename, 'w') as f:
            json.dump({
                'games': games_data, 
                'last_modified': datetime.now().isoformat(),
                'feedback_format_version': 'unified',  # Flag to indicate unified feedback format
                'pause_format_version': 'integrated'   # Flag to indicate integrated pause format
            }, f, indent=2)
        print(f"Successfully saved {len(games_data)} games to {filename}")
        return True
    except Exception as e:
        print(f"Error saving data to {filename}: {str(e)}")
        return False

def load_from_gmd(filename):
    """Load game data from a .gmd file and return (data, needs_migration)"""
    try:
        with open(filename, 'r') as f:
            data = json.load(f)
            games = data.get('games', [])
            
        # Check if this file needs migration for feedback format or pause format
        feedback_format_version = data.get('feedback_format_version', 'legacy')
        pause_format_version = data.get('pause_format_version', 'legacy')
        needs_migration = feedback_format_version != 'unified' or pause_format_version != 'integrated'
            
        # Convert the data back to the format expected by the program
        formatted_data = []
        for i, game in enumerate(games):
            try:
                # Validate required fields
                name = game.get('name', '')
                release_date = game.get('release_date', '')
                platform = game.get('platform', '')
                time_played = game.get('time_played', '')
                status = game.get('status', 'Pending')
                owned = game.get('owned', False)
                last_played = game.get('last_played')
                sessions = game.get('sessions', [])  # Load sessions from JSON
                status_history = game.get('status_history', [])  # Load status history from JSON
                rating = game.get('rating')  # Load rating from JSON
                
                # Additional validation
                if status not in ['Pending', 'In progress', 'Completed']:
                    status = 'Pending'
                
                # Format time_played if needed
                if time_played and isinstance(time_played, str) and ':' in time_played:
                    # Ensure proper time format
                    parts = time_played.split(':')
                    if len(parts) == 2:  # HH:MM
                        hours, minutes = map(int, parts)
                        time_played = f"{hours:02d}:{minutes:02d}:00"
                    elif len(parts) != 3:  # Not HH:MM:SS
                        time_played = "00:00:00"
                
                row = [name, release_date, platform, time_played, status, 
                       '✅' if owned else '', last_played, sessions, status_history, rating]
                
                formatted_data.append((i, row))
            except Exception as e:
                print(f"Warning: Skipping game with invalid data: {str(e)}")
                continue
        
        print(f"Successfully loaded {len(formatted_data)} games from {filename}")
        return formatted_data, needs_migration
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from {filename}: {str(e)}")
        # If the file exists but is corrupted, rename it and create a new one
        if os.path.exists(filename):
            backup_name = f"{filename}.backup-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            print(f"Renaming corrupted file to {backup_name}")
            os.rename(filename, backup_name)
        raise FileNotFoundError(f"Invalid JSON in {filename}")
    except Exception as e:
        print(f"Error loading data from {filename}: {str(e)}")
        raise

def get_data_from_sheet(sheet):
    """Extract game data from an Excel sheet"""
    data = []
    for row in sheet.iter_rows(min_row=6, values_only=True):
        if not all(cell is None for cell in row[1:6]):
            release_date = row[2]
            if isinstance(release_date, datetime):
                release_date = release_date.strftime('%Y-%m-%d')
            ownership_status = '✅' if row[6] != 'x' else ''
            last_tracked_date = row[7] if row[7] else None
            if isinstance(last_tracked_date, datetime):
                last_tracked_date = last_tracked_date.strftime('%Y-%m-%d %H:%M:%S')
            data.append([row[1], release_date, row[3], row[4], row[5], ownership_status, last_tracked_date])
    return data

def convert_excel_to_gmd(excel_file, gmd_file):
    """Convert an existing Excel file to .gmd format"""
    try:
        workbook = openpyxl.load_workbook(excel_file)
        sheet = workbook.active
        data = get_data_from_sheet(sheet)
        data_with_indices = [(index, row) for index, row in enumerate(data)]
        
        if save_to_gmd(data_with_indices, gmd_file):
            print(f"Successfully converted Excel file {excel_file} to GMD format: {gmd_file}")
            return data_with_indices
        else:
            print(f"Failed to save converted data to {gmd_file}")
            return []
    except Exception as e:
        print(f"Error converting Excel file {excel_file} to GMD: {str(e)}")
        return []

def save_data(data_with_idx, filename, data_storage=None):
    """Save game data to the .gmd file"""
    # If we're working with filtered data, make sure to save the complete dataset
    if data_storage is not None:
        # Make sure any changes in the filtered view are reflected in data_storage
        for filtered_idx, (original_idx, row_data) in enumerate(data_with_idx):
            # Find the corresponding entry in data_storage
            for i, (idx, _) in enumerate(data_storage):
                if idx == original_idx:
                    # Update the entry with the latest data from the filtered view
                    data_storage[i] = (idx, row_data)
                    break
        
        # Save the complete dataset
        save_to_gmd(data_storage, filename)
    else:
        # We're working with the complete dataset
        save_to_gmd(data_with_idx, filename)
    
    # Update config with the saved file path
    config = load_config()
    config['last_file'] = filename
    save_config(config)
    
    return True 