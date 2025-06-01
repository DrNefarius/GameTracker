"""
Session management functionality for the GamesList application.
Handles session tracking, time management, notes, and session statistics.
"""

import time
import os
import io
import tempfile
import PySimpleGUI as sg
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict, Counter

from constants import STAR_FILLED, STAR_EMPTY, RATING_TAGS, NEGATIVE_TAGS, NEUTRAL_TAGS, POSITIVE_TAGS
from utilities import format_timedelta_with_seconds
from ratings import show_rating_popup
from data_management import save_data
from visualizations import isolate_matplotlib_env

def update_time_and_date(row_index, added_time, session, data_with_indices, data_storage=None):
    """Helper function to update time and last tracked date"""
    # Update the last tracked date
    last_tracked_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    data_with_indices[row_index][1][6] = last_tracked_date  # Update last-tracked column

    # Update the time
    current_time_str = data_with_indices[row_index][1][3]
    if current_time_str:
        if isinstance(current_time_str, timedelta):
            current_time = current_time_str
        else:
            try:
                # Try HH:MM:SS format
                h, m, s = map(int, current_time_str.split(':'))
                current_time = timedelta(hours=h, minutes=m, seconds=s)
            except ValueError:
                try:
                    # Try HH:MM format
                    h, m = map(int, current_time_str.split(':'))
                    current_time = timedelta(hours=h, minutes=m)
                except ValueError:
                    current_time = timedelta()
    else:
        current_time = timedelta()
        
    # Calculate the new total time
    added_seconds = added_time.total_seconds()
    added_time = timedelta(seconds=added_seconds)
    total_time = current_time + added_time
    
    # Format the times for display
    new_time = format_timedelta_with_seconds(total_time)
    
    # Update the data
    data_with_indices[row_index][1][3] = new_time
    
    # Add session data if provided
    if session:
        # Initialize sessions array if it doesn't exist
        if len(data_with_indices[row_index][1]) <= 7 or data_with_indices[row_index][1][7] is None:
            data_with_indices[row_index][1].append([])
        
        # Add the new session
        data_with_indices[row_index][1][7].append(session)

    # Update the full dataset when modifying filtered data
    if data_storage:
        original_index = data_with_indices[row_index][0]
        # Find and update the correct entry in data_storage
        for i, (idx, _) in enumerate(data_storage):
            if idx == original_index:
                data_storage[i] = data_with_indices[row_index]
                break

def show_popup(row_index, data_with_indices, window, data_storage=None, save_filename=None):
    """Show the time tracking popup for a game"""
    if row_index >= len(data_with_indices):
        sg.popup("Invalid row index", title="Error")
        return

    name = data_with_indices[row_index][1][0]
    initial_time_str = data_with_indices[row_index][1][3]
    
    try:
        if initial_time_str and initial_time_str not in ['', None, '00:00:00', '00:00']:
            try:
                # Try HH:MM:SS format
                hours, minutes, seconds = map(int, initial_time_str.split(':'))
                initial_time = timedelta(hours=hours, minutes=minutes, seconds=seconds)
            except ValueError:
                try:
                    # Try HH:MM format
                    hours, minutes = map(int, initial_time_str.split(':'))
                    initial_time = timedelta(hours=hours, minutes=minutes)
                except ValueError:
                    # Default to 0 if parsing fails
                    sg.popup(f"Warning: Could not parse time format '{initial_time_str}'. Starting with 0.", title="Time Format Error")
                    initial_time = timedelta(0)
        else:
            initial_time = timedelta(0)
    except Exception as e:
        sg.popup(f"Error parsing time: {str(e)}", title="Error")
        initial_time = timedelta(0)
    
    name_length = len(name) if name else 10
    popup_width = max(400, 10 * name_length) # Adjust width based on name length
    layout_popup = [
        [sg.Text(f'Tracking Time for: {name}', size=(max(30, int(name_length * 1.5)), 1))],
        [sg.Text('Current Time:', size=(15, 1)), sg.Text('00:00:00', key='-TIMER-', size=(10, 1))],
        [sg.Text('Total Time:', size=(15, 1)), sg.Text(format_timedelta_with_seconds(initial_time), key='-TOTAL-TIME-', size=(10, 1))],
        [sg.Button('▶️', key='-PLAY-', button_color=('black', 'green')),
         sg.Button('⏸️', key='-PAUSE-', button_color=('black', 'yellow')),
         sg.Button('⏹️', key='-STOP-', button_color=('black', 'red'))]
    ]

    popup_window = sg.Window('Control Timer', layout_popup, modal=True, icon='gameslisticon.ico')
    
    # Reset the elapsed time for this new session
    elapsed_time = timedelta(0)
    running = False
    start_time = 0
    
    # Session tracking
    session_start_time = None
    session_pauses = []
    current_pause = None  # Track the current incomplete pause

    while True:
        event, _ = popup_window.read(timeout=100)

        if event == sg.WIN_CLOSED:
            # If window is closed while timer is running, stop and save the time
            if running:
                elapsed_time = timedelta(seconds=time.time() - start_time)
                running = False
                
                # Handle incomplete pause if window closed while paused
                if current_pause:
                    current_pause['incomplete'] = True
                    session_pauses.append(current_pause)
                    current_pause = None
                
                # End the session
                session_end_time = datetime.now().isoformat()
                # Create a session record
                session = {
                    'start': session_start_time,
                    'end': session_end_time,
                    'duration': format_timedelta_with_seconds(elapsed_time),
                    'pauses': session_pauses
                }
                
                # Ask for feedback (replaces old separate note/rating prompts)
                if sg.popup_yes_no("Would you like to add feedback for this session?", title="Add Session Feedback") == "Yes":
                    feedback = show_session_feedback_popup()
                    if feedback:
                        session['feedback'] = feedback
                
                # Update the last tracked date and time
                update_time_and_date(row_index, elapsed_time, session, data_with_indices, data_storage)
                
                # Automatically save the data when tracking is stopped
                if save_filename:
                    save_data(data_with_indices, save_filename, data_storage)
            break
            
        elif event == '-PLAY-':
            if not running:
                if session_start_time is None:
                    # Record session start time
                    session_start_time = datetime.now().isoformat()
                
                start_time = time.time() - elapsed_time.total_seconds()
                running = True
                popup_window['-PLAY-'].update(disabled=True)
                
                # Complete current pause if resuming from pause
                if current_pause:
                    current_pause['resumed_at'] = datetime.now().isoformat()
                    
                    # Calculate pause duration
                    try:
                        pause_start = datetime.fromisoformat(current_pause['paused_at'])
                        pause_end = datetime.fromisoformat(current_pause['resumed_at'])
                        pause_duration = pause_end - pause_start
                        
                        # Format as HH:MM:SS
                        hours, remainder = divmod(pause_duration.total_seconds(), 3600)
                        minutes, seconds = divmod(remainder, 60)
                        current_pause['pause_duration'] = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
                    except (ValueError, TypeError):
                        current_pause['pause_duration'] = "00:00:00"
                    
                    session_pauses.append(current_pause)
                    current_pause = None
                    
        elif event == '-PAUSE-':
            if running:
                elapsed_time = timedelta(seconds=time.time() - start_time)
                running = False
                popup_window['-PLAY-'].update(disabled=False)
                
                # Start a new pause
                current_pause = {
                    'paused_at': datetime.now().isoformat(),
                    'elapsed_so_far': format_timedelta_with_seconds(elapsed_time)
                }
                
        elif event == '-STOP-':
            if running:
                elapsed_time = timedelta(seconds=time.time() - start_time)
                running = False
            
            # Handle incomplete pause if stopping while paused
            if current_pause:
                current_pause['incomplete'] = True
                session_pauses.append(current_pause)
                current_pause = None
            
            if session_start_time:
                # End the session
                session_end_time = datetime.now().isoformat()
                # Create a session record
                session = {
                    'start': session_start_time,
                    'end': session_end_time,
                    'duration': format_timedelta_with_seconds(elapsed_time),
                    'pauses': session_pauses
                }
                
                # Ask for feedback (replaces old separate note/rating prompts)
                if sg.popup_yes_no("Would you like to add feedback for this session?", title="Add Session Feedback") == "Yes":
                    feedback = show_session_feedback_popup()
                    if feedback:
                        session['feedback'] = feedback
                
                # Update the time and last tracked date
                update_time_and_date(row_index, elapsed_time, session, data_with_indices, data_storage)
                
                # Automatically save the data when tracking is stopped
                if save_filename:
                    save_data(data_with_indices, save_filename, data_storage)
            break

        # Update the timer text
        if running:
            current_elapsed = timedelta(seconds=time.time() - start_time)
        else:
            current_elapsed = elapsed_time
        popup_window['-TIMER-'].update(format_timedelta_with_seconds(current_elapsed))
        total_run_time = initial_time + current_elapsed
        popup_window['-TOTAL-TIME-'].update(format_timedelta_with_seconds(total_run_time))

    popup_window.close()

def extract_all_sessions(data):
    """Extract all session data from the games list"""
    all_sessions = []
    
    for idx, game_data in data:
        game_name = game_data[0]
        if len(game_data) > 7 and game_data[7]:
            for session in game_data[7]:
                # Add game name to each session for reference
                session_with_game = session.copy()
                session_with_game['game'] = game_name
                all_sessions.append(session_with_game)
    
    return all_sessions

def calculate_session_statistics(all_sessions):
    """Calculate overall statistics for all sessions"""
    stats = {
        'total_count': len(all_sessions),
        'total_time': timedelta(),
        'avg_length': timedelta(),
        'most_active_day': {'day': None, 'count': 0}
    }
    
    if not all_sessions:
        return stats
    
    # Track days with sessions
    days_with_sessions = defaultdict(int)
    
    # Calculate total time across all sessions
    for session in all_sessions:
        try:
            # Convert duration string to timedelta
            duration = session.get('duration', '00:00:00')
            if isinstance(duration, str):
                parts = duration.split(':')
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    duration_td = timedelta(hours=h, minutes=m, seconds=s)
                    stats['total_time'] += duration_td
            
            # Track session days
            if 'start' in session:
                try:
                    start_date = datetime.fromisoformat(session['start']).date()
                    days_with_sessions[start_date] += 1
                except (ValueError, TypeError):
                    pass
        except Exception as e:
            print(f"Error processing session: {str(e)}")
            continue
    
    # Calculate average session length
    if stats['total_count'] > 0:
        stats['avg_length'] = stats['total_time'] / stats['total_count']
    
    # Find most active day
    if days_with_sessions:
        most_active = max(days_with_sessions.items(), key=lambda x: x[1])
        stats['most_active_day'] = {
            'day': most_active[0],
            'count': most_active[1]
        }
    
    return stats

def get_game_sessions(data, game_name):
    """Get all sessions for a specific game"""
    for idx, game_data in data:
        if game_data[0] == game_name:
            if len(game_data) > 7 and game_data[7]:
                return game_data[7]
            break
    
    return []

def format_session_for_display(sessions):
    """Format session data for display in the table"""
    display_data = []
    
    for session in sessions:
        try:
            # Parse start time
            start_time = "Unknown"
            if 'start' in session:
                try:
                    dt = datetime.fromisoformat(session['start'])
                    start_time = dt.strftime('%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    pass
            
            # Get duration
            duration = session.get('duration', '00:00:00')
            
            # Create details string
            details = []
            if 'pauses' in session and session['pauses']:
                pause_count = len(session['pauses'])
                if pause_count > 0:
                    details.append(f"{pause_count} pause(s)")
            
            if 'end' in session:
                try:
                    start_dt = datetime.fromisoformat(session['start'])
                    end_dt = datetime.fromisoformat(session['end'])
                    session_span = end_dt - start_dt
                    hours, remainder = divmod(session_span.total_seconds(), 3600)
                    minutes, seconds = divmod(remainder, 60)
                    details.append(f"Session span: {int(hours)}h {int(minutes)}m")
                except (ValueError, TypeError, KeyError):
                    pass
            
            # Handle unified feedback structure
            has_feedback = 'feedback' in session and session['feedback']
            has_feedback_text = has_feedback and session['feedback'].get('text', '').strip()
            has_rating = has_feedback and 'rating' in session['feedback']
            
            # Create prefix for details
            prefix_parts = []
            if has_feedback_text:
                prefix_parts.append("[FEEDBACK]")
            if has_rating:
                stars = session['feedback']['rating'].get('stars', 0)
                prefix_parts.append(f"[{STAR_FILLED * stars}{STAR_EMPTY * (5 - stars)}]")
                
                # Add rating tags if they exist
                tags = session['feedback']['rating'].get('tags', [])
                if tags:
                    details.append(f"Tags: {', '.join(tags[:3])}" + ("..." if len(tags) > 3 else ""))
            
            details_prefix = " ".join(prefix_parts) + " " if prefix_parts else ""
            details_str = details_prefix + (", ".join(details) if details else "No additional details")
            
            # Create row data
            row_data = [start_time, duration, details_str]
            
            display_data.append(row_data)
        except Exception as e:
            print(f"Error formatting session: {str(e)}")
            continue
    
    return display_data

def get_status_history(data, game_name):
    """Get status history for a specific game"""
    for idx, game_data in data:
        if game_data[0] == game_name:
            if len(game_data) > 8 and game_data[8]:
                return game_data[8]
            break
    
    return []

def format_status_history_for_display(history):
    """Format status history data for display in a table"""
    display_data = []
    
    for change in history:
        try:
            # Format timestamp
            timestamp = "Unknown"
            if 'timestamp' in change:
                try:
                    dt = datetime.fromisoformat(change['timestamp'])
                    timestamp = dt.strftime('%Y-%m-%d %H:%M:%S')
                except (ValueError, TypeError):
                    pass
            
            from_status = change.get('from', 'Unknown')
            to_status = change.get('to', 'Unknown')
            
            display_data.append([timestamp, from_status, to_status])
        except Exception as e:
            print(f"Error formatting status change: {str(e)}")
            continue
    
    # Sort by timestamp (newest first)
    display_data.sort(reverse=True)
    return display_data

def display_all_game_notes(game_name, sessions, data_with_indices):
    """Display all feedback for a game in chronological order along with status changes"""
    # Collect all sessions with feedback, keeping track of their original indices
    entries = []
    
    # Add game-level rating if it exists
    for idx, game_data in data_with_indices:
        if game_data[0] == game_name:
            if len(game_data) > 9 and game_data[9] and isinstance(game_data[9], dict):
                game_rating = game_data[9]
                # Check if there's actual content to display
                has_comment = 'comment' in game_rating and game_rating['comment'].strip()
                has_stars = 'stars' in game_rating and game_rating['stars'] > 0
                has_tags = 'tags' in game_rating and game_rating['tags']
                
                if has_comment or has_stars or has_tags:
                    # Get timestamp for the game rating
                    timestamp_obj = datetime.min
                    timestamp = "Unknown time"
                    if 'timestamp' in game_rating:
                        try:
                            timestamp_obj = datetime.fromisoformat(game_rating['timestamp'])
                            timestamp = timestamp_obj.strftime('%Y-%m-%d %H:%M:%S')
                        except (ValueError, TypeError):
                            pass
                    
                    # Build the rating content to display
                    rating_content = ""
                    
                    # Add star rating
                    if has_stars:
                        stars = game_rating['stars']
                        rating_content += f"Overall Rating: {STAR_FILLED * stars}{STAR_EMPTY * (5 - stars)}"
                        
                        # Add auto-calculated indicator if applicable
                        if game_rating.get('auto_calculated', False):
                            rating_content += " (Auto-calculated from sessions)"
                        
                        if has_tags:
                            rating_content += f"\nTags: {', '.join(game_rating['tags'])}"
                    
                    # Add comment if exists
                    if has_comment:
                        if rating_content:  # Add separator if we already have rating info
                            rating_content += "\n\n"
                        rating_content += f"Comment: {game_rating['comment']}"
                    
                    # Add to collection
                    entries.append({
                        'type': 'game_rating',
                        'timestamp': timestamp,
                        'timestamp_obj': timestamp_obj,
                        'rating_content': rating_content,
                        'auto_calculated': game_rating.get('auto_calculated', False)
                    })
            break
    
    # Add session feedback
    for idx, session in enumerate(sessions):
        if 'feedback' in session and session['feedback']:
            feedback = session['feedback']
            
            # Check if there's actual text content or rating
            has_text = feedback.get('text', '').strip()
            has_rating = 'rating' in feedback
            
            if has_text or has_rating:
                # Extract timestamp and format it
                start_time = "Unknown time"
                timestamp_obj = datetime.min
                if 'start' in session:
                    try:
                        timestamp_obj = datetime.fromisoformat(session['start'])
                        start_time = timestamp_obj.strftime('%Y-%m-%d %H:%M:%S')
                    except (ValueError, TypeError):
                        pass
                
                # Build the feedback content to display
                feedback_content = ""
                
                # Add rating info first (if exists)
                if has_rating:
                    rating = feedback['rating']
                    stars = rating.get('stars', 0)
                    rating_info = f"Rating: {STAR_FILLED * stars}{STAR_EMPTY * (5 - stars)}"
                    if rating.get('tags'):
                        rating_info += f" Tags: {', '.join(rating['tags'])}"
                    feedback_content += rating_info
                    
                # Add text content (if exists)
                if has_text:
                    if feedback_content:  # Add separator if we already have rating info
                        feedback_content += "\n\n"
                    feedback_content += feedback['text']
                
                # Add to collection with original index (session number)
                entries.append({
                    'type': 'feedback',
                    'original_index': idx + 1,  # Add 1 to make it 1-based instead of 0-based
                    'timestamp': start_time,
                    'timestamp_obj': timestamp_obj,
                    'feedback': feedback_content,
                    'duration': session.get('duration', '00:00:00')
                })
    
    # Add status changes
    status_history = get_status_history(data_with_indices, game_name)
    for status_change in status_history:
        timestamp_obj = datetime.min
        timestamp = "Unknown time"
        if 'timestamp' in status_change:
            try:
                timestamp_obj = datetime.fromisoformat(status_change['timestamp'])
                timestamp = timestamp_obj.strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                pass
        
        entries.append({
            'type': 'status',
            'timestamp': timestamp,
            'timestamp_obj': timestamp_obj,
            'from_status': status_change.get('from'),
            'to_status': status_change.get('to')
        })
    
    # If no entries found
    if not entries:
        sg.popup(f"No feedback, ratings, or status changes found for {game_name}", title="No Entries", icon='gameslisticon.ico')
        return
    
    # Sort by timestamp (chronological order)
    entries.sort(key=lambda x: x['timestamp_obj'])
    
    # Create the display text
    notes_text = f"Game Activity Log for {game_name}\n\n"
    for entry in entries:
        if entry['type'] == 'feedback':
            notes_text += f"--- SESSION FEEDBACK {entry['original_index']} - {entry['timestamp']} (Duration: {entry['duration']}) ---\n"
            notes_text += f"{entry['feedback']}\n\n"
        elif entry['type'] == 'game_rating':
            marker = "OVERALL GAME RATING"
            if entry['auto_calculated']:
                marker += " (AUTO-CALCULATED)"
            notes_text += f"*** {marker} - {entry['timestamp']} ***\n"
            notes_text += f"{entry['rating_content']}\n\n"
        else:  # status change
            from_status = entry['from_status'] if entry['from_status'] else "None"
            to_status = entry['to_status']
            notes_text += f">>> STATUS CHANGE - {entry['timestamp']} <<<\n"
            notes_text += f"Changed from '{from_status}' to '{to_status}'\n\n"
    
    # Display in a scrolled popup
    sg.popup_scrolled(notes_text, title=f"Activity Log for {game_name}", size=(70, 30), icon='gameslisticon.ico')

# Session visualization functions
def create_session_timeline_chart(sessions, game_name=None):
    """Create a timeline chart of gaming sessions"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    # Prepare the data
    dates = []
    durations = []
    
    for session in sessions:
        try:
            if 'start' in session and 'duration' in session:
                # Parse start time
                try:
                    start_date = datetime.fromisoformat(session['start'])
                    
                    # Parse duration
                    duration_str = session['duration']
                    parts = duration_str.split(':')
                    if len(parts) == 3:
                        h, m, s = map(int, parts)
                        duration_hours = h + m/60 + s/3600
                        
                        dates.append(start_date)
                        durations.append(duration_hours)
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            print(f"Error processing session for timeline: {str(e)}")
            continue
    
    # Create the chart
    fig, ax = plt.subplots(figsize=(7, 2.5))
    
    if dates and durations:
        # Sort data by date
        sorted_data = sorted(zip(dates, durations))
        dates, durations = zip(*sorted_data)
        
        # Plot the data
        ax.bar(dates, durations, width=0.6, color='#5cb85c')
        
        # Format the x-axis
        ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%Y-%m-%d'))
        plt.xticks(rotation=45, ha='right')
        
        # Add labels and title
        title = f"Session Timeline for {game_name}" if game_name else "All Gaming Sessions Timeline"
        ax.set_title(title, fontsize=12)
        ax.set_ylabel('Hours Played', fontsize=10)
        
        # Adjust for better appearance
        plt.tight_layout()
    else:
        ax.text(0.5, 0.5, "No session data available for timeline", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Session Timeline", fontsize=12)
    
    # Save to a buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf

def create_session_distribution_chart(sessions, game_name=None):
    """Create a chart showing distribution of session lengths"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    # Prepare the data
    durations = []
    
    for session in sessions:
        try:
            if 'duration' in session:
                # Parse duration
                duration_str = session['duration']
                parts = duration_str.split(':')
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    duration_minutes = h * 60 + m + s/60
                    durations.append(duration_minutes)
        except Exception as e:
            print(f"Error processing session for distribution: {str(e)}")
            continue
    
    # Create the chart
    fig, ax = plt.subplots(figsize=(7, 2.5))
    
    if durations:
        # Create bins for histogram (in minutes)
        if max(durations) <= 60:  # All sessions under an hour
            bins = range(0, int(max(durations)) + 10, 5)  # 5-minute bins
            xlabel = 'Session Length (minutes)'
        else:
            # Convert to hours for better visualization
            durations = [d/60 for d in durations]
            max_duration = max(durations)
            bin_size = max(0.5, max_duration / 10)  # Dynamic bin size
            bins = np.arange(0, max_duration + bin_size, bin_size)
            xlabel = 'Session Length (hours)'
        
        # Plot histogram
        ax.hist(durations, bins=bins, color='#6f42c1', edgecolor='black', alpha=0.7)
        
        # Add labels and title
        title = f"Session Length Distribution for {game_name}" if game_name else "All Games Session Length Distribution"
        ax.set_title(title, fontsize=12)
        ax.set_xlabel(xlabel, fontsize=10)
        ax.set_ylabel('Number of Sessions', fontsize=10)
        
        # Adjust for better appearance
        plt.tight_layout()
    else:
        ax.text(0.5, 0.5, "No session data available for distribution analysis", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Session Distribution", fontsize=12)
    
    # Save to a buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf

def create_session_heatmap(sessions, game_name=None, window_months=6, end_date=None):
    """Create a heatmap visualization showing gaming intensity and pauses with time-based windowing"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    # Check if we have session data
    if not sessions:
        fig, ax = plt.subplots(figsize=(9, 2.5))
        ax.text(0.5, 0.5, "No session data available for heatmap", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Session Activity Heatmap", fontsize=12)
        
        # Save to a buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    # Determine date range for windowing
    if end_date is None:
        # Find the latest session date, or use current date if no sessions
        latest_session_date = None
        for session in sessions:
            try:
                session_date = datetime.fromisoformat(session['start']).date()
                if latest_session_date is None or session_date > latest_session_date:
                    latest_session_date = session_date
            except:
                continue
        end_date = latest_session_date if latest_session_date else datetime.now().date()
    
    # Calculate start date based on window size (approximate months to days)
    days_in_window = window_months * 30
    start_date = end_date - timedelta(days=days_in_window)
    
    # Filter sessions to the current window
    windowed_sessions = []
    for session in sessions:
        try:
            if 'start' in session:
                session_date = datetime.fromisoformat(session['start']).date()
                if start_date <= session_date <= end_date:
                    windowed_sessions.append(session)
        except Exception as e:
            print(f"Error filtering session for heatmap window: {str(e)}")
            continue
    
    # If no sessions in the current window, show appropriate message
    if not windowed_sessions:
        fig, ax = plt.subplots(figsize=(9, 2.5))
        period_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        ax.text(0.5, 0.5, f"No session data available for period:\n{period_str}", 
                ha='center', va='center', fontsize=10)
        title = f"Gaming Heatmap for {game_name}" if game_name else "Gaming Sessions Heatmap"
        title += f"\n({period_str})"
        ax.set_title(title, fontsize=12)
        
        # Save to a buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    # Process session data to extract gaming intensity and pauses
    session_data = []
    
    for session in windowed_sessions:
        try:
            if 'start' in session and 'end' in session and 'pauses' in session:
                start_time = datetime.fromisoformat(session['start'])
                end_time = datetime.fromisoformat(session['end'])
                
                # Basic session info
                session_info = {
                    'start': start_time,
                    'end': end_time,
                    'date': start_time.date(),
                    'pauses': []
                }
                
                # Process pauses - now they're already in integrated format
                pause_periods = []
                if session['pauses']:
                    for pause in session['pauses']:
                        if 'paused_at' in pause and 'resumed_at' in pause:
                            # Complete pause with both start and end times
                            pause_time = datetime.fromisoformat(pause['paused_at'])
                            resume_time = datetime.fromisoformat(pause['resumed_at'])
                            pause_length = (resume_time - pause_time).total_seconds() / 60  # minutes
                            pause_periods.append({
                                'start': pause_time,
                                'end': resume_time,
                                'duration': pause_length
                            })
                        # Skip incomplete pauses (session ended while paused)
                
                session_info['pauses'] = pause_periods
                session_data.append(session_info)
        except Exception as e:
            print(f"Error processing session for heatmap: {str(e)}")
            continue
    
    # Sort sessions by date
    session_data.sort(key=lambda x: x['start'])
    
    # Group sessions by date
    date_sessions = {}
    for s in session_data:
        date_str = s['date'].strftime('%Y-%m-%d')
        if date_str not in date_sessions:
            date_sessions[date_str] = []
        date_sessions[date_str].append(s)
    
    # Create the heatmap visualization
    fig, ax = plt.subplots(figsize=(9, 3.5))
    
    if date_sessions:
        # Prepare data for the heatmap
        dates = sorted(date_sessions.keys())
        
        # Prepare canvas for drawing
        y_pos = len(dates)
        
        # Set up plot layout
        ax.set_ylim(0, len(dates))
        ax.set_xlim(0, 24)  # 24 hours in a day
        
        # Draw hour lines
        for hour in range(1, 24):
            ax.axvline(x=hour, color='lightgray', linestyle='-', alpha=0.5, linewidth=0.5)
        
        # Add hour labels
        ax.set_xticks(range(0, 25, 3))
        ax.set_xticklabels([f"{i:02d}:00" for i in range(0, 25, 3)], fontsize=8)
        
        # For each date, draw sessions as blocks
        for i, date in enumerate(dates):
            # Draw date label
            ax.text(-0.5, i + 0.5, date, ha='right', va='center', fontsize=8)
            
            # Draw sessions for this date
            for session in date_sessions[date]:
                start_hour = session['start'].hour + session['start'].minute / 60
                end_hour = session['end'].hour + session['end'].minute / 60
                
                # Handle sessions that span midnight
                if end_hour < start_hour:
                    end_hour = 24
                
                # Draw the base session block
                rect = plt.Rectangle((start_hour, i), end_hour - start_hour, 0.8, 
                                   alpha=0.7, edgecolor='none',
                                   facecolor='#5cb85c')  # Green for gaming time
                ax.add_patch(rect)
                
                # Draw pauses as overlays
                for pause in session['pauses']:
                    pause_start = pause['start'].hour + pause['start'].minute / 60
                    pause_end = pause['end'].hour + pause['end'].minute / 60
                    
                    # Make sure pause is within session bounds
                    if pause_start >= start_hour and pause_end <= end_hour:
                        # Draw pause indicator
                        pause_rect = plt.Rectangle((pause_start, i), pause_end - pause_start, 0.8,
                                                 alpha=0.8, edgecolor='none',
                                                 facecolor='#ff9933')  # Orange for pauses
                        ax.add_patch(pause_rect)
                
                # Add emoji indicators for fun
                mid_point = (start_hour + end_hour) / 2
                if len(session['pauses']) == 0:
                    # No pauses - hardcore gamer!
                    ax.text(mid_point, i + 0.4, "★", ha='center', va='center', fontsize=12, fontweight='bold')
                elif len(session['pauses']) == 1:
                    # One pause - normal
                    ax.text(mid_point, i + 0.4, "◉", ha='center', va='center', fontsize=12)
                elif len(session['pauses']) < 4:
                    # A few pauses
                    ax.text(mid_point, i + 0.4, "◯", ha='center', va='center', fontsize=12)
                else:
                    # Many pauses! Distracted gamer
                    ax.text(mid_point, i + 0.4, "×", ha='center', va='center', fontsize=12)
        
        # Remove y-axis ticks
        ax.set_yticks([])
        
        # Grid
        ax.grid(True, which='major', axis='x', linestyle='-', alpha=0.3)
        
        # Title and labels with period information
        period_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        title = f"Gaming Heatmap for {game_name}" if game_name else "Gaming Sessions Heatmap"
        title += f"\n({period_str})"
        ax.set_title(title, fontsize=12)
        ax.set_xlabel("Time of Day", fontsize=10)
        
        # Add session count information
        total_sessions = sum(len(sessions) for sessions in date_sessions.values())
        ax.text(1.05, 0.15, f"Sessions: {total_sessions}", transform=ax.transAxes, 
                fontsize=10, horizontalalignment='left', verticalalignment='top',
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue", alpha=0.8))
        
        # Add legend
        legend_elements = [
            plt.Rectangle((0, 0), 1, 1, facecolor='#5cb85c', alpha=0.7, label='Active Gaming'),
            plt.Rectangle((0, 0), 1, 1, facecolor='#ff9933', alpha=0.8, label='Pauses'),
            plt.Line2D([0], [0], marker='$★$', color='black', label='Focused Session (No Pauses)', linestyle='',
                      markerfacecolor='k', markersize=7),
            plt.Line2D([0], [0], marker='$◉$', color='black', label='Brief Pause (1 Pause)', linestyle='', 
                      markerfacecolor='k', markersize=7),
            plt.Line2D([0], [0], marker='$◯$', color='black', label='Few Breaks (2-3 Pauses)', linestyle='', 
                      markerfacecolor='k', markersize=7),
            plt.Line2D([0], [0], marker='$×$', color='black', label='Many Interruptions (4+ Pauses)', linestyle='', 
                      markerfacecolor='k', markersize=7)
        ]
        ax.legend(handles=legend_elements, bbox_to_anchor=(1.05, 1), loc='upper left', fontsize=8, framealpha=0.7)
    else:
        period_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        ax.text(0.5, 0.5, f"No session data available with pause information for period:\n{period_str}", 
                ha='center', va='center', fontsize=10)
        title = f"Gaming Heatmap for {game_name}" if game_name else "Gaming Sessions Heatmap"
        title += f"\n({period_str})"
        ax.set_title(title, fontsize=12)
    
    plt.tight_layout()
    
    # Save to a buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf

def create_status_timeline_chart(history, game_name=None):
    """Create a timeline visualization of status changes"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    if not history:
        fig, ax = plt.subplots(figsize=(7, 2.5))
        ax.text(0.5, 0.5, "No status change data available", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Status Change Timeline", fontsize=12)
        
        # Save to a buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    # Prepare the data
    timestamps = []
    statuses = []
    colors = []
    
    # Status color mapping
    status_colors = {
        'Pending': '#d9534f',    # Red
        'In progress': '#f0ad4e', # Yellow/Orange
        'Completed': '#5cb85c'    # Green
    }
    
    for change in sorted(history, key=lambda x: x.get('timestamp', '')):
        try:
            if 'timestamp' in change and 'to' in change:
                timestamp = datetime.fromisoformat(change['timestamp'])
                status = change['to']
                
                timestamps.append(timestamp)
                statuses.append(status)
                colors.append(status_colors.get(status, '#777777'))  # Default gray for unknown status
        except (ValueError, TypeError):
            continue
    
    # Create figure
    fig, ax = plt.subplots(figsize=(7, 3))
    
    if timestamps:
        # Plot the data as a step chart
        for i in range(len(timestamps)):
            # Plot a colored point at each status change
            ax.scatter(timestamps[i], i, color=colors[i], s=100, zorder=3)
            
            # Add text label for the status
            ax.text(timestamps[i], i + 0.1, statuses[i], ha='center', va='bottom', fontsize=9)
            
            # Connect points with lines
            if i > 0:
                ax.plot([timestamps[i-1], timestamps[i]], [i-1, i], 'k-', alpha=0.3, zorder=1)
        
        # Add today marker
        now = datetime.now()
        if timestamps[0] <= now:
            ax.axvline(x=now, color='blue', linestyle='--', alpha=0.5, label='Today')
        
        # Format x-axis as dates
        plt.gcf().autofmt_xdate()
        date_format = plt.matplotlib.dates.DateFormatter('%Y-%m-%d')
        ax.xaxis.set_major_formatter(date_format)
        
        # Set labels and title
        title = f"Status Timeline for {game_name}" if game_name else "Status Change Timeline"
        ax.set_title(title, fontsize=12)
        ax.set_xlabel('Date', fontsize=10)
        
        # Remove y-axis ticks and labels
        ax.set_yticks([])
        
        # Add legend for status colors
        legend_elements = [
            plt.Line2D([0], [0], marker='o', color='w', label='Pending',
                      markerfacecolor=status_colors['Pending'], markersize=10),
            plt.Line2D([0], [0], marker='o', color='w', label='In progress',
                      markerfacecolor=status_colors['In progress'], markersize=10),
            plt.Line2D([0], [0], marker='o', color='w', label='Completed',
                      markerfacecolor=status_colors['Completed'], markersize=10)
        ]
        ax.legend(handles=legend_elements, loc='upper right', fontsize=8)
        
        plt.tight_layout()
    else:
        ax.text(0.5, 0.5, "No status change data available", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Status Change Timeline", fontsize=12)
    
    # Save to a buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf 

def get_game_rating_comments(data, game_name):
    """Get all rating comments for a specific game (both game-level and session-level)"""
    comments = []
    
    for idx, game_data in data:
        if game_data[0] == game_name:
            # Get game-level rating comment
            if len(game_data) > 9 and game_data[9] and isinstance(game_data[9], dict):
                game_rating = game_data[9]
                if 'comment' in game_rating and game_rating['comment']:
                    comments.append({
                        'type': 'game',
                        'stars': game_rating.get('stars', 0),
                        'tags': game_rating.get('tags', []),
                        'comment': game_rating['comment'],
                        'timestamp': game_rating.get('timestamp', 'Unknown'),
                        'auto_calculated': game_rating.get('auto_calculated', False)
                    })
            
            # Get session-level rating comments from unified feedback structure
            if len(game_data) > 7 and game_data[7]:
                for i, session in enumerate(game_data[7]):
                    if 'feedback' in session and session['feedback'] and 'rating' in session['feedback'] and session['feedback']['rating']:
                        session_rating = session['feedback']['rating']
                        # Check if there's a rating comment (note: comments are typically stored at the text level now)
                        rating_comment = session_rating.get('comment', '')
                        if rating_comment:
                            comments.append({
                                'type': 'session',
                                'session_index': i + 1,
                                'session_date': session.get('start', 'Unknown'),
                                'duration': session.get('duration', '00:00:00'),
                                'stars': session_rating.get('stars', 0),
                                'tags': session_rating.get('tags', []),
                                'comment': rating_comment,
                                'timestamp': session_rating.get('timestamp', 'Unknown')
                            })
            break
    
    return comments

def format_rating_comments_for_display(comments):
    """Format rating comments for display in a table"""
    display_data = []
    
    for comment_data in comments:
        try:
            # Format timestamp
            timestamp = "Unknown"
            if comment_data['timestamp'] != 'Unknown':
                try:
                    dt = datetime.fromisoformat(comment_data['timestamp'])
                    timestamp = dt.strftime('%Y-%m-%d %H:%M')
                except (ValueError, TypeError):
                    pass
            
            # Format type and source
            if comment_data['type'] == 'game':
                source = "Overall Game Rating"
                if comment_data.get('auto_calculated', False):
                    source += " (Auto-calc)"
            else:
                session_date = "Unknown"
                if comment_data['session_date'] != 'Unknown':
                    try:
                        dt = datetime.fromisoformat(comment_data['session_date'])
                        session_date = dt.strftime('%Y-%m-%d %H:%M')
                    except (ValueError, TypeError):
                        pass
                source = f"Session #{comment_data['session_index']} ({session_date})"
            
            # Format rating
            stars = comment_data['stars']
            rating_display = STAR_FILLED * stars + STAR_EMPTY * (5 - stars)
            
            # Format tags
            tags_display = ", ".join(comment_data['tags']) if comment_data['tags'] else "No tags"
            
            # Format comment (truncate if too long)
            comment_text = comment_data['comment']
            if len(comment_text) > 100:
                comment_text = comment_text[:97] + "..."
            
            display_data.append([timestamp, source, rating_display, tags_display, comment_text])
            
        except Exception as e:
            print(f"Error formatting rating comment: {str(e)}")
            continue
    
    # Sort by timestamp (newest first)
    display_data.sort(reverse=True)
    return display_data

def create_comments_word_cloud_visualization(comments):
    """Create a word frequency visualization from rating comments"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    if not comments:
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.text(0.5, 0.5, "No rating comments available for analysis", 
                ha='center', va='center', fontsize=12)
        ax.set_title("Rating Comments Analysis", fontsize=14)
        
        # Save to a buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    # Extract all words from comments
    all_words = []
    for comment_data in comments:
        comment_text = comment_data['comment'].lower()
        # Simple word extraction (remove punctuation and split)
        import re
        words = re.findall(r'\b[a-zA-Z]{3,}\b', comment_text)  # Only words with 3+ letters
        all_words.extend(words)
    
    # Filter out common stop words
    stop_words = {'the', 'and', 'but', 'for', 'are', 'was', 'were', 'been', 'have', 'has', 'had', 
                  'will', 'would', 'could', 'should', 'can', 'may', 'might', 'must', 'shall',
                  'this', 'that', 'these', 'those', 'with', 'from', 'they', 'them', 'their',
                  'you', 'your', 'yours', 'she', 'her', 'hers', 'his', 'him', 'its', 'our',
                  'ours', 'very', 'just', 'more', 'some', 'all', 'any', 'most', 'much',
                  'many', 'few', 'little', 'big', 'small', 'good', 'bad', 'best', 'worst',
                  'really', 'quite', 'pretty', 'too', 'also', 'only', 'even', 'still',
                  'get', 'got', 'make', 'made', 'take', 'took', 'come', 'came', 'see', 'saw',
                  'know', 'knew', 'think', 'thought', 'say', 'said', 'tell', 'told',
                  'game', 'play', 'played', 'playing', 'games'}
    
    filtered_words = [word for word in all_words if word not in stop_words and len(word) > 2]
    
    if not filtered_words:
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.text(0.5, 0.5, "No meaningful words found in comments for analysis", 
                ha='center', va='center', fontsize=12)
        ax.set_title("Rating Comments Analysis", fontsize=14)
        
        # Save to a buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    # Count word frequencies
    word_counts = Counter(filtered_words)
    
    # Get top 15 words
    top_words = word_counts.most_common(15)
    
    # Create visualization
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    
    # Word frequency bar chart
    if top_words:
        words, counts = zip(*top_words)
        colors = plt.cm.viridis(np.linspace(0, 1, len(words)))
        bars = ax1.barh(range(len(words)), counts, color=colors)
        ax1.set_yticks(range(len(words)))
        ax1.set_yticklabels(words)
        ax1.set_xlabel('Frequency')
        ax1.set_title('Most Common Words in Comments')
        ax1.invert_yaxis()
        
        # Add count labels on bars
        for i, (bar, count) in enumerate(zip(bars, counts)):
            ax1.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2, 
                    str(count), va='center', fontsize=9)
    
    # Sentiment by rating visualization
    rating_sentiment = defaultdict(list)
    for comment_data in comments:
        stars = comment_data['stars']
        comment_words = re.findall(r'\b[a-zA-Z]{3,}\b', comment_data['comment'].lower())
        positive_words = {'good', 'great', 'amazing', 'awesome', 'fantastic', 'excellent', 
                         'wonderful', 'brilliant', 'outstanding', 'superb', 'perfect',
                         'love', 'loved', 'enjoy', 'enjoyed', 'fun', 'exciting', 'thrilling'}
        negative_words = {'bad', 'terrible', 'awful', 'horrible', 'boring', 'annoying',
                         'frustrating', 'disappointing', 'worst', 'hate', 'hated', 'sucks',
                         'broken', 'buggy', 'glitchy', 'repetitive', 'tedious'}
        
        positive_count = sum(1 for word in comment_words if word in positive_words)
        negative_count = sum(1 for word in comment_words if word in negative_words)
        sentiment_score = positive_count - negative_count
        rating_sentiment[stars].append(sentiment_score)
    
    # Calculate average sentiment by rating
    avg_sentiment = {}
    for rating, sentiments in rating_sentiment.items():
        if sentiments:
            avg_sentiment[rating] = sum(sentiments) / len(sentiments)
    
    if avg_sentiment:
        ratings = sorted(avg_sentiment.keys())
        sentiments = [avg_sentiment[r] for r in ratings]
        
        colors = ['red' if s < 0 else 'green' if s > 0 else 'gray' for s in sentiments]
        ax2.bar([f"{r}★" for r in ratings], sentiments, color=colors, alpha=0.7)
        ax2.set_ylabel('Average Sentiment Score')
        ax2.set_title('Comment Sentiment by Rating')
        ax2.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        ax2.grid(True, alpha=0.3)
    else:
        ax2.text(0.5, 0.5, "Insufficient data for sentiment analysis", 
                ha='center', va='center', transform=ax2.transAxes)
        ax2.set_title('Comment Sentiment by Rating')
    
    plt.tight_layout()
    
    # Save to a buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf 

def show_session_feedback_popup(existing_feedback=None):
    """Show a unified popup for session feedback (text + optional rating)"""
    is_edit = existing_feedback is not None
    
    if existing_feedback is None:
        existing_feedback = {'text': '', 'rating': None}
    
    existing_text = existing_feedback.get('text', '')
    existing_rating = existing_feedback.get('rating')
    
    # Determine if rating section should be enabled
    has_rating = existing_rating is not None
    
    layout = [
        [sg.Text(f"{'Edit' if is_edit else 'Add'} Session Feedback", font=('Arial', 12, 'bold'))],
        [sg.Text("Session thoughts/notes:")],
        [sg.Multiline(default_text=existing_text, size=(60, 8), key='-FEEDBACK-TEXT-')],
        [sg.VPush()],
        [sg.Checkbox('Rate this session', default=has_rating, key='-ENABLE-RATING-', enable_events=True)],
        [sg.Frame('Rating', [
            [sg.Text('Stars (1-5):'), sg.Text(STAR_FILLED * (existing_rating.get('stars', 3) if existing_rating else 3) + 
                                             STAR_EMPTY * (5 - (existing_rating.get('stars', 3) if existing_rating else 3)), 
                                             key='-STARS-DISPLAY-', font=('Arial', 16))],
            [sg.Slider(range=(1, 5), default_value=existing_rating.get('stars', 3) if existing_rating else 3, 
                      orientation='h', size=(40, 15), key='-RATING-STARS-', enable_events=True)],
            [sg.Text("Tags (optional):")],
            [sg.Frame("Negative", [
                [sg.Column([[
                    sg.Checkbox(tag, default=tag in (existing_rating.get('tags', []) if existing_rating else []), 
                               key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(12, 1)) 
                    for tag in NEGATIVE_TAGS[:5]
                ]], vertical_alignment='top')],
                [sg.Column([[
                    sg.Checkbox(tag, default=tag in (existing_rating.get('tags', []) if existing_rating else []), 
                               key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(12, 1)) 
                    for tag in NEGATIVE_TAGS[5:]
                ]], vertical_alignment='top')]
            ], font=('Arial', 9), relief=sg.RELIEF_SUNKEN, pad=((5, 5), (2, 2)))],
            [sg.Frame("Neutral", [
                [sg.Column([[
                    sg.Checkbox(tag, default=tag in (existing_rating.get('tags', []) if existing_rating else []), 
                               key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(12, 1)) 
                    for tag in NEUTRAL_TAGS[:5]
                ]], vertical_alignment='top')],
                [sg.Column([[
                    sg.Checkbox(tag, default=tag in (existing_rating.get('tags', []) if existing_rating else []), 
                               key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(12, 1)) 
                    for tag in NEUTRAL_TAGS[5:]
                ]], vertical_alignment='top')]
            ], font=('Arial', 9), relief=sg.RELIEF_SUNKEN, pad=((5, 5), (2, 2)))],
            [sg.Frame("Positive", [
                [sg.Column([[
                    sg.Checkbox(tag, default=tag in (existing_rating.get('tags', []) if existing_rating else []), 
                               key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(12, 1)) 
                    for tag in POSITIVE_TAGS[:5]
                ]], vertical_alignment='top')],
                [sg.Column([[
                    sg.Checkbox(tag, default=tag in (existing_rating.get('tags', []) if existing_rating else []), 
                               key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(12, 1)) 
                    for tag in POSITIVE_TAGS[5:10]
                ]], vertical_alignment='top')],
                [sg.Column([[
                    sg.Checkbox(tag, default=tag in (existing_rating.get('tags', []) if existing_rating else []), 
                               key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(12, 1)) 
                    for tag in POSITIVE_TAGS[10:15]
                ]], vertical_alignment='top')],
                [sg.Column([[
                    sg.Checkbox(tag, default=tag in (existing_rating.get('tags', []) if existing_rating else []), 
                               key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(12, 1)) 
                    for tag in POSITIVE_TAGS[15:]
                ]], vertical_alignment='top')]
            ], font=('Arial', 9), relief=sg.RELIEF_SUNKEN, pad=((5, 5), (2, 2)))]
        ], key='-RATING-FRAME-', visible=has_rating)],
        [sg.VPush()],
        [sg.Button('Save'), sg.Button('Cancel')]
    ]
    
    popup = sg.Window(f"{'Edit' if is_edit else 'Add'} Session Feedback", layout, modal=True, 
                     icon='gameslisticon.ico', finalize=True)
    
    while True:
        event, values = popup.read()
        
        if event in (sg.WIN_CLOSED, 'Cancel'):
            popup.close()
            return None
            
        elif event == '-ENABLE-RATING-':
            # Show/hide rating section
            rating_enabled = values['-ENABLE-RATING-']
            popup['-RATING-FRAME-'].update(visible=rating_enabled)
            
        elif event == '-RATING-STARS-':
            # Update stars display
            stars = int(values['-RATING-STARS-'])
            popup['-STARS-DISPLAY-'].update(STAR_FILLED * stars + STAR_EMPTY * (5 - stars))
            
        elif event == 'Save':
            feedback_text = values['-FEEDBACK-TEXT-'].strip()
            
            # Build feedback object
            feedback = {
                'text': feedback_text if feedback_text else '',
                'timestamp': datetime.now().isoformat()
            }
            
            # Add rating if enabled
            if values['-ENABLE-RATING-']:
                stars = int(values['-RATING-STARS-'])
                tags = []
                for i, tag in enumerate(RATING_TAGS):
                    if values[f'-TAG-{i}-']:
                        tags.append(tag)
                
                feedback['rating'] = {
                    'stars': stars,
                    'tags': tags,
                    'timestamp': datetime.now().isoformat()
                }
            
            popup.close()
            return feedback
    
    return None 

def migrate_pauses_to_integrated_structure(session_pauses):
    """Convert old pause structure (separate pause/resume events) to new integrated structure"""
    if not session_pauses:
        return []
    
    integrated_pauses = []
    current_pause = None
    
    for event in session_pauses:
        if 'paused_at' in event:
            # Start of a new pause
            current_pause = {
                'paused_at': event['paused_at'],
                'elapsed_so_far': event.get('elapsed_so_far', '00:00:00')
            }
        elif 'resumed_at' in event and current_pause:
            # End of current pause
            current_pause['resumed_at'] = event['resumed_at']
            
            # Calculate pause duration
            try:
                from datetime import datetime
                pause_start = datetime.fromisoformat(current_pause['paused_at'])
                pause_end = datetime.fromisoformat(current_pause['resumed_at'])
                pause_duration = pause_end - pause_start
                
                # Format as HH:MM:SS
                hours, remainder = divmod(pause_duration.total_seconds(), 3600)
                minutes, seconds = divmod(remainder, 60)
                current_pause['pause_duration'] = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
            except (ValueError, TypeError):
                current_pause['pause_duration'] = "00:00:00"
            
            integrated_pauses.append(current_pause)
            current_pause = None
    
    # Handle edge case: session ended while paused (no resume event)
    if current_pause:
        # Mark as incomplete pause (no resume time)
        current_pause['incomplete'] = True
        integrated_pauses.append(current_pause)
    
    return integrated_pauses

def migrate_session_to_unified_feedback(session):
    """Migrate session from old format (notes + rating) to new unified feedback format"""
    # Check if already migrated
    if 'feedback' in session:
        return session
    
    # Build new feedback structure
    feedback_parts = []
    feedback_obj = {
        'text': '',
        'timestamp': session.get('start', datetime.now().isoformat())
    }
    
    # Collect existing data
    existing_note = session.get('note', '')
    existing_rating = session.get('rating')
    
    # Store rating in new format (keep separate from text)
    if existing_rating and 'stars' in existing_rating:
        feedback_obj['rating'] = {
            'stars': existing_rating['stars'],
            'tags': existing_rating.get('tags', []),
            'timestamp': existing_rating.get('timestamp', feedback_obj['timestamp'])
        }
    
    # Build text content (without embedding rating info)
    # 1. Add existing note text (if it doesn't already contain rating info)
    if existing_note:
        # Check if the note contains rating information that we should strip out
        import re
        # Remove any embedded rating lines from the note to avoid duplication
        note_lines = existing_note.split('\n')
        clean_note_lines = []
        
        for line in note_lines:
            line_stripped = line.strip()
            # Skip lines that look like embedded rating info
            if not (re.match(f"^Rating:\s*[{STAR_FILLED}{STAR_EMPTY}]+.*Tags:", line_stripped) or 
                   line_stripped.startswith("Rating:") and any(star in line_stripped for star in [STAR_FILLED, STAR_EMPTY])):
                clean_note_lines.append(line)
        
        clean_note = '\n'.join(clean_note_lines).strip()
        if clean_note:
            feedback_parts.append(clean_note)
    
    # 2. Add rating comment (if exists and different from note)
    if existing_rating and 'comment' in existing_rating and existing_rating['comment']:
        rating_comment = existing_rating['comment']
        # Only add if it's different from the cleaned note to avoid duplication
        clean_note_text = feedback_parts[0] if feedback_parts else ''
        if rating_comment != clean_note_text and rating_comment not in clean_note_text:
            feedback_parts.append(f"Rating comment: {rating_comment}")
    
    # Combine text parts (no rating info embedded)
    if feedback_parts:
        feedback_obj['text'] = '\n\n'.join(feedback_parts)
    
    # Create new session structure
    new_session = {}
    for key, value in session.items():
        if key not in ['note', 'rating']:  # Remove old fields
            new_session[key] = value
    
    # Add unified feedback
    if feedback_obj['text'] or 'rating' in feedback_obj:
        new_session['feedback'] = feedback_obj
    
    return new_session

def migrate_all_game_sessions(data_with_indices):
    """Migrate all sessions in the dataset to unified feedback format and integrated pause structure.
    
    This function handles both types of migration:
    1. Legacy feedback format (separate notes + rating) -> Unified feedback format
    2. Legacy pause format (separate pause/resume events) -> Integrated pause format
    
    Both migrations are checked and applied as needed, regardless of version flags.
    """
    migrated_data = []
    
    for idx, game_data in data_with_indices:
        new_game_data = game_data.copy()
        
        # Migrate sessions if they exist
        if len(new_game_data) > 7 and new_game_data[7]:
            migrated_sessions = []
            for session in new_game_data[7]:
                # First migrate to unified feedback
                migrated_session = migrate_session_to_unified_feedback(session)
                
                # Always check and migrate pause structure if it's in old format
                if 'pauses' in migrated_session and migrated_session['pauses']:
                    # Check if pauses are in old format (separate pause/resume events)
                    needs_pause_migration = False
                    for pause_event in migrated_session['pauses']:
                        if isinstance(pause_event, dict):
                            # If we find any event with only 'paused_at' OR only 'resumed_at', it's old format
                            has_paused_at = 'paused_at' in pause_event
                            has_resumed_at = 'resumed_at' in pause_event
                            if (has_paused_at and not has_resumed_at) or (has_resumed_at and not has_paused_at):
                                needs_pause_migration = True
                                break
                    
                    if needs_pause_migration:
                        print(f"Migrating pause structure for session in {game_data[0] if game_data else 'unknown game'}")
                        migrated_session['pauses'] = migrate_pauses_to_integrated_structure(migrated_session['pauses'])
                
                migrated_sessions.append(migrated_session)
            new_game_data[7] = migrated_sessions
        
        migrated_data.append((idx, new_game_data))
    
    return migrated_data 

def create_github_style_contributions_heatmap(sessions, game_name=None):
    """Create a GitHub-style contributions heatmap showing gaming activity over time"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    # Check if we have session data
    if not sessions:
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.text(0.5, 0.5, "No session data available for contributions heatmap", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Gaming Contributions", fontsize=12)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        
        # Save to a buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    # Process session data to get daily activity
    daily_activity = {}
    
    for session in sessions:
        try:
            if 'start' in session and 'duration' in session:
                start_time = datetime.fromisoformat(session['start'])
                date_key = start_time.date()
                
                # Parse duration to get minutes
                duration_str = session['duration']
                parts = duration_str.split(':')
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    duration_minutes = h * 60 + m + s/60
                    
                    # Add to daily total
                    if date_key not in daily_activity:
                        daily_activity[date_key] = {'sessions': 0, 'total_minutes': 0}
                    daily_activity[date_key]['sessions'] += 1
                    daily_activity[date_key]['total_minutes'] += duration_minutes
                    
        except Exception as e:
            print(f"Error processing session for contributions heatmap: {str(e)}")
            continue
    
    if not daily_activity:
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.text(0.5, 0.5, "No valid session data for contributions heatmap", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Gaming Contributions", fontsize=12)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        
        # Save to a buffer
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    # Get date range (show last 365 days or all data if less)
    end_date = max(daily_activity.keys())
    start_date = min(daily_activity.keys())
    
    # For GitHub-style, we want to show weeks in rows and days in columns
    # Calculate how many weeks we need
    date_range = (end_date - start_date).days + 1
    weeks_to_show = min(52, (date_range + 6) // 7)  # Max 52 weeks (1 year)
    
    # If we have more than a year of data, show the most recent year
    if date_range > 365:
        start_date = end_date - timedelta(days=364)  # Show last 365 days
        weeks_to_show = 53  # A bit more than 52 to handle edge cases
    
    # Create date grid (7 days × weeks)
    # Start from the Monday of the week containing start_date
    calendar_start = start_date - timedelta(days=start_date.weekday())
    
    # Create the activity matrix
    activity_matrix = np.zeros((7, weeks_to_show))  # 7 days, weeks_to_show weeks
    date_matrix = []
    
    for week in range(weeks_to_show):
        week_dates = []
        for day in range(7):  # Monday=0, Sunday=6
            current_date = calendar_start + timedelta(days=week*7 + day)
            week_dates.append(current_date)
            
            # Get activity for this date
            if current_date in daily_activity:
                # Use total minutes as the intensity measure
                activity_matrix[day, week] = daily_activity[current_date]['total_minutes']
        
        date_matrix.append(week_dates)
    
    # Create the plot with GitHub-like proportions
    # GitHub uses small squares - calculate size based on weeks
    square_size = 12  # Size in pixels
    gap_size = 2     # Gap between squares in pixels
    
    # Calculate figure size based on actual square layout
    chart_width = weeks_to_show * (square_size + gap_size) / 72  # Convert to inches (72 DPI)
    chart_height = 7 * (square_size + gap_size) / 72 + 1.5  # 7 days + space for labels/legend
    
    fig, ax = plt.subplots(figsize=(max(chart_width, 8), chart_height))
    
    # Define color scheme exactly like GitHub
    colors = ['#ebedf0', '#9be9a8', '#40c463', '#30a14e', '#216e39']
    
    # Use total minutes to determine intensity
    max_activity = np.max(activity_matrix) if np.max(activity_matrix) > 0 else 1
    
    # Store square data for tooltips
    square_data = []
    
    # Create small squares for each day (GitHub style)
    square_size_plot = 0.9  # Size in plot coordinates
    gap_size_plot = 0.1
    
    for week in range(weeks_to_show):
        for day in range(7):
            activity = activity_matrix[day, week]
            current_date = date_matrix[week][day]
            
            x = week * (square_size_plot + gap_size_plot)
            y = (6 - day) * (square_size_plot + gap_size_plot)
            
            # Determine color based on activity level
            if start_date <= current_date <= end_date:
                if activity == 0:
                    color = colors[0]  # Light gray for no activity
                    intensity_level = 0
                else:
                    # Scale activity to color intensity (1-4)
                    intensity_level = min(4, max(1, int((activity / max_activity) * 4)))
                    color = colors[intensity_level]
            else:
                # Use very light gray for dates outside our data range
                color = '#f6f8fa'
                intensity_level = -1  # Indicate out of range
            
            # Draw the small square
            rect = plt.Rectangle((x, y), square_size_plot, square_size_plot, 
                               facecolor=color, edgecolor=color, linewidth=0)
            ax.add_patch(rect)
            
            # Store data for tooltip functionality
            sessions_count = daily_activity.get(current_date, {}).get('sessions', 0) if start_date <= current_date <= end_date else 0
            square_info = {
                'rect': rect,
                'date': current_date,
                'activity': activity,
                'sessions': sessions_count,
                'x': x,
                'y': y,
                'size': square_size_plot,
                'in_range': start_date <= current_date <= end_date
            }
            square_data.append(square_info)
    
    # Set up hover tooltip functionality
    annotation = ax.annotate('', xy=(0, 0), xytext=(20, 20), textcoords="offset points",
                           bbox=dict(boxstyle="round", fc="black", alpha=0.8),
                           arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=0"),
                           color='white', fontsize=9, visible=False)
    
    def on_hover(event):
        if event.inaxes == ax:
            for square_info in square_data:
                x, y, size = square_info['x'], square_info['y'], square_info['size']
                if (x <= event.xdata <= x + size and y <= event.ydata <= y + size):
                    if square_info['in_range']:
                        date_str = square_info['date'].strftime('%B %d, %Y')
                        if square_info['activity'] > 0:
                            hours = int(square_info['activity'] // 60)
                            minutes = int(square_info['activity'] % 60)
                            time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                            tooltip_text = f"{square_info['sessions']} session(s) on {date_str}\n{time_str} total"
                        else:
                            tooltip_text = f"No gaming on {date_str}"
                    else:
                        tooltip_text = f"No data for {square_info['date'].strftime('%B %d, %Y')}"
                    
                    annotation.xy = (event.xdata, event.ydata)
                    annotation.set_text(tooltip_text)
                    annotation.set_visible(True)
                    fig.canvas.draw_idle()
                    return
            
            # Hide tooltip if not hovering over any square
            annotation.set_visible(False)
            fig.canvas.draw_idle()
    
    # Connect hover event
    fig.canvas.mpl_connect('motion_notify_event', on_hover)
    
    # Calculate plot boundaries
    max_x = weeks_to_show * (square_size_plot + gap_size_plot)
    max_y = 7 * (square_size_plot + gap_size_plot)
    
    # Customize the plot to look like GitHub
    ax.set_xlim(-0.5, max_x + 0.5)
    ax.set_ylim(-1, max_y + 0.5)
    
    # Set day labels like GitHub (show all days)
    day_labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    day_positions = []
    day_labels_to_show = []
    
    # Only show some day labels to avoid clutter
    for i, label in enumerate(['Mon', 'Wed', 'Fri']):
        day_idx = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].index(label)
        y_pos = (6 - day_idx) * (square_size_plot + gap_size_plot) + square_size_plot/2
        day_positions.append(y_pos)
        day_labels_to_show.append(label)
    
    ax.set_yticks(day_positions)
    ax.set_yticklabels(day_labels_to_show, fontsize=8)
    
    # Month labels on x-axis (GitHub style)
    month_positions = []
    month_labels = []
    current_month = None
    
    for week in range(weeks_to_show):
        if week < len(date_matrix):
            week_start_date = date_matrix[week][0]
            if week_start_date.month != current_month:
                current_month = week_start_date.month
                x_pos = week * (square_size_plot + gap_size_plot) + square_size_plot/2
                month_positions.append(x_pos)
                month_labels.append(week_start_date.strftime('%b'))
    
    ax.set_xticks(month_positions)
    ax.set_xticklabels(month_labels, fontsize=8)
    
    # Remove spines and ticks
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(left=False, bottom=False)
    
    # Set title
    title = f"Gaming Contributions - {game_name}" if game_name else "Gaming Contributions"
    ax.set_title(title, fontsize=12, pad=15)
    
    # Add activity summary at the bottom
    total_days = len([d for d in daily_activity.keys() if start_date <= d <= end_date])
    max_streak = calculate_gaming_streak(daily_activity, start_date, end_date)
    total_sessions = sum(data['sessions'] for data in daily_activity.values())
    
    summary_text = f"{total_sessions} sessions in the last {date_range} days • Longest streak: {max_streak} days"
    ax.text(max_x/2, -0.8, summary_text, ha='center', va='center', fontsize=9, 
            color='#586069')
    
    # Add GitHub-style legend
    if max_x > 6:  # Only add legend if there's enough space
        legend_start_x = max_x - 5
        legend_y = max_y + 0.2
        
        ax.text(legend_start_x - 0.5, legend_y, "Less", fontsize=8, ha='right', va='center', color='#586069')
        
        # Create small legend squares
        for i, color in enumerate(colors):
            legend_x = legend_start_x + i * 0.7
            rect = plt.Rectangle((legend_x, legend_y - 0.3), 0.6, 0.6, 
                               facecolor=color, edgecolor=color, linewidth=0)
            ax.add_patch(rect)
        
        ax.text(legend_start_x + len(colors)*0.7 + 0.2, legend_y, "More", fontsize=8, ha='left', va='center', color='#586069')
    
    # Adjust layout for better spacing
    plt.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.12)
    
    # Save to a buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', dpi=100)
    buf.seek(0)
    plt.close(fig)
    
    return buf

def calculate_gaming_streak(daily_activity, start_date, end_date):
    """Calculate the longest consecutive gaming streak"""
    max_streak = 0
    current_streak = 0
    
    # Go through each day in the range
    current_date = start_date
    while current_date <= end_date:
        if current_date in daily_activity:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
        current_date += timedelta(days=1)
    
    return max_streak 

def create_github_contributions_canvas(sessions, game_name=None, canvas_key='-CONTRIBUTIONS-CANVAS-', year=None):
    """Create a GitHub-style contributions heatmap using PySimpleGUI Canvas for better interactivity"""
    
    # Process session data to get daily activity
    daily_activity = {}
    
    for session in sessions:
        try:
            if 'start' in session and 'duration' in session:
                start_time = datetime.fromisoformat(session['start'])
                date_key = start_time.date()
                
                # Parse duration to get minutes
                duration_str = session['duration']
                parts = duration_str.split(':')
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    duration_minutes = h * 60 + m + s/60
                    
                    # Add to daily total with game details
                    if date_key not in daily_activity:
                        daily_activity[date_key] = {'sessions': 0, 'total_minutes': 0, 'games': {}}
                    daily_activity[date_key]['sessions'] += 1
                    daily_activity[date_key]['total_minutes'] += duration_minutes
                    
                    # Track per-game activity
                    # Use the provided game_name if we're viewing a specific game, otherwise get from session
                    if game_name:
                        game_name_from_session = game_name
                    else:
                        game_name_from_session = session.get('game', 'Unknown Game')
                    
                    if game_name_from_session not in daily_activity[date_key]['games']:
                        daily_activity[date_key]['games'][game_name_from_session] = {'sessions': 0, 'minutes': 0}
                    daily_activity[date_key]['games'][game_name_from_session]['sessions'] += 1
                    daily_activity[date_key]['games'][game_name_from_session]['minutes'] += duration_minutes
                    
        except Exception as e:
            print(f"Error processing session for contributions canvas: {str(e)}")
            continue
    
    if not daily_activity:
        # Return drawing function for "no data" message
        def draw_no_data_message(canvas_element):
            canvas = canvas_element.Widget
            canvas.delete("all")
            # Draw "no data" message
            title_text = f"No gaming sessions for {game_name}" if game_name else "No gaming sessions recorded"
            canvas.create_text(400, 120, text=title_text, font=('Arial', 12, 'bold'), fill='#586069')
            canvas.create_text(400, 150, text="Start tracking time to see your contributions map!", 
                             font=('Arial', 10), fill='#586069')
        
        return {
            'draw_function': draw_no_data_message,
            'canvas_key': canvas_key,
            'square_data': {}
        }
    
    # Always show a full year like GitHub (52-53 weeks)
    weeks_to_show = 53  # Always show full year
    
    # Determine the year to show
    if year is not None:
        show_year = year
    elif daily_activity:
        latest_date = max(daily_activity.keys())
        show_year = latest_date.year
    else:
        show_year = datetime.now().year
    
    # Calculate start and end dates for the full year
    end_date = datetime(show_year, 12, 31).date()
    start_date = datetime(show_year, 1, 1).date()
    date_range = (end_date - start_date).days + 1
    
    # Create date grid (7 days × weeks)
    calendar_start = start_date - timedelta(days=start_date.weekday())
    
    # GitHub-style configuration
    square_size = 11  # Size of each square in pixels
    gap_size = 2      # Gap between squares
    margin_left = 60  # Space for day labels (increased)
    margin_top = 55   # Space for month labels and title (increased more)
    margin_bottom = 60 # Space for summary and legend (increased)
    margin_right = 20 # Space on the right side
    
    # Calculate canvas dimensions
    canvas_width = margin_left + weeks_to_show * (square_size + gap_size) + 200  # More space for legend
    canvas_height = margin_top + 7 * (square_size + gap_size) + margin_bottom
    
    # Create activity matrix and square data
    activity_matrix = np.zeros((7, weeks_to_show))
    square_data = {}  # Dictionary to store square info by canvas ID
    date_matrix = []
    
    for week in range(weeks_to_show):
        week_dates = []
        for day in range(7):  # Monday=0, Sunday=6
            current_date = calendar_start + timedelta(days=week*7 + day)
            week_dates.append(current_date)
            
            # Get activity for this date
            if current_date in daily_activity:
                activity_matrix[day, week] = daily_activity[current_date]['total_minutes']
        
        date_matrix.append(week_dates)
    
    # Define GitHub color scheme
    colors = ['#ebedf0', '#9be9a8', '#40c463', '#30a14e', '#216e39']
    max_activity = np.max(activity_matrix) if np.max(activity_matrix) > 0 else 1
    
    # Note: We don't create layout anymore, just return the drawing function
    
    # Function to draw the heatmap on canvas
    def draw_heatmap_on_canvas(canvas_element):
        canvas = canvas_element.Widget
        canvas.delete("all")  # Clear canvas
        
        # Variable to store current tooltip canvas items
        tooltip_items = []
        
        # Bind mouse motion event for tooltips
        def on_mouse_motion(event):
            # Clear existing tooltip
            for item in tooltip_items:
                canvas.delete(item)
            tooltip_items.clear()
            
            # Find which square we're hovering over
            x, y = event.x, event.y
            hovered_square = None
            for square_id, square_info in square_data.items():
                if (square_info['x1'] <= x <= square_info['x2'] and 
                    square_info['y1'] <= y <= square_info['y2']):
                    hovered_square = square_info
                    break
            
            # Show tooltip near mouse position if hovering over a square
            if hovered_square and hovered_square['in_range']:
                date_str = hovered_square['date'].strftime('%B %d, %Y')
                if hovered_square['activity'] > 0:
                    hours = int(hovered_square['activity'] // 60)
                    minutes = int(hovered_square['activity'] % 60)
                    time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                    
                    # Build tooltip with game details
                    tooltip_lines = [f"{hovered_square['sessions']} session(s) on {date_str}"]
                    tooltip_lines.append(f"{time_str} total")
                    
                    # Add per-game breakdown if available
                    if 'games' in hovered_square and hovered_square['games']:
                        tooltip_lines.append("")  # Empty line for separation
                        game_details = []
                        for game, data in sorted(hovered_square['games'].items(), 
                                               key=lambda x: x[1]['minutes'], reverse=True):
                            game_hours = int(data['minutes'] // 60)
                            game_minutes = int(data['minutes'] % 60)
                            if game_hours > 0:
                                game_time_str = f"{game_hours}h {game_minutes}m"
                            else:
                                game_time_str = f"{game_minutes}m"
                            game_details.append(f"• {game}: {game_time_str}")
                        
                        # Limit to top 3 games to keep tooltip reasonable
                        if len(game_details) > 3:
                            tooltip_lines.extend(game_details[:3])
                            tooltip_lines.append(f"• ... and {len(game_details) - 3} more")
                        else:
                            tooltip_lines.extend(game_details)
                    
                    tooltip_text = "\n".join(tooltip_lines)
                else:
                    tooltip_text = f"No gaming on {date_str}"
                
                # Calculate tooltip dimensions first
                text_lines = tooltip_text.split('\n')
                max_line_length = max(len(line) for line in text_lines) if text_lines else 0
                tooltip_width = max(max_line_length * 7 + 15, 150)  # Increased width for game details, minimum width
                tooltip_height = len(text_lines) * 12 + 10  # Estimate height
                
                # Smart tooltip positioning - switch to left side when in right third of canvas
                if x > canvas_width * 0.66:  # If in right third, show tooltip to the left
                    tooltip_x = max(x - tooltip_width - 15, 10)  # Show to the left of mouse with proper width
                else:
                    tooltip_x = min(x + 15, canvas_width - tooltip_width - 20)  # Show to the right of mouse with proper width
                tooltip_y = max(y - 30, 10)  # Above mouse with boundary check
                
                bg_item = canvas.create_rectangle(
                    tooltip_x - 5, tooltip_y - 5,
                    tooltip_x + tooltip_width, tooltip_y + tooltip_height,
                    fill='black', outline='gray', width=1
                )
                tooltip_items.append(bg_item)
                
                # Draw tooltip text
                for i, line in enumerate(text_lines):
                    text_item = canvas.create_text(
                        tooltip_x, tooltip_y + i * 12,
                        text=line, fill='white', font=('Arial', 9),
                        anchor='nw'
                    )
                    tooltip_items.append(text_item)
            
            # No need for external tooltip since we have canvas-based tooltip
        
        canvas.bind('<Motion>', on_mouse_motion)
        
        # Clear tooltip when mouse leaves canvas
        def on_mouse_leave(event):
            for item in tooltip_items:
                canvas.delete(item)
            tooltip_items.clear()
        
        canvas.bind('<Leave>', on_mouse_leave)
        
        # Draw title (positioned higher to avoid overlap with month labels)
        title_text = f"Gaming Contributions - {game_name}" if game_name else "Gaming Contributions"
        canvas.create_text(canvas_width//2, 18, text=title_text, font=('Arial', 12, 'bold'), fill='black')
        
        # Draw day labels
        day_labels = ['Mon', 'Wed', 'Fri']
        day_indices = [0, 2, 4]  # Monday, Wednesday, Friday
        
        for i, (label, day_idx) in enumerate(zip(day_labels, day_indices)):
            y_pos = margin_top + day_idx * (square_size + gap_size) + square_size // 2
            canvas.create_text(margin_left - 10, y_pos, text=label, font=('Arial', 8), fill='#586069', anchor='e')
        
        # Draw month labels (positioned lower to avoid overlap with title)
        current_month = None
        for week in range(weeks_to_show):
            if week < len(date_matrix):
                week_start_date = date_matrix[week][0]
                if week_start_date.month != current_month:
                    current_month = week_start_date.month
                    x_pos = margin_left + week * (square_size + gap_size)
                    canvas.create_text(x_pos, margin_top - 18, text=week_start_date.strftime('%b'), 
                                     font=('Arial', 8), fill='#586069', anchor='w')
        
        # Draw squares
        square_data.clear()
        for week in range(weeks_to_show):
            for day in range(7):
                activity = activity_matrix[day, week]
                current_date = date_matrix[week][day]
                
                x1 = margin_left + week * (square_size + gap_size)
                y1 = margin_top + day * (square_size + gap_size)
                x2 = x1 + square_size
                y2 = y1 + square_size
                
                # Determine color based on activity level
                if start_date <= current_date <= end_date:
                    if activity == 0:
                        color = colors[0]  # Light gray for no activity
                    else:
                        # Scale activity to color intensity (1-4)
                        intensity = min(4, max(1, int((activity / max_activity) * 4)))
                        color = colors[intensity]
                else:
                    # Use very light gray for dates outside our data range
                    color = '#f6f8fa'
                
                # Draw the square
                square_id = canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=color, width=0)
                
                # Store square data for tooltip including game details
                day_data = daily_activity.get(current_date, {}) if start_date <= current_date <= end_date else {}
                sessions_count = day_data.get('sessions', 0)
                games_data = day_data.get('games', {})
                
                square_data[square_id] = {
                    'date': current_date,
                    'activity': activity,
                    'sessions': sessions_count,
                    'games': games_data,
                    'in_range': start_date <= current_date <= end_date,
                    'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2
                }
        
        # Draw legend (positioned properly within canvas bounds)
        legend_x = margin_left + 50  # Position legend properly within canvas
        legend_y = margin_top + 7 * (square_size + gap_size) + 25
        
        canvas.create_text(legend_x - 15, legend_y, text="Less", font=('Arial', 8), fill='#586069', anchor='e')
        
        for i, color in enumerate(colors):
            square_x = legend_x + i * (square_size + 2)
            canvas.create_rectangle(square_x, legend_y - 5, square_x + square_size, legend_y + 5, 
                                  fill=color, outline=color, width=0)
        
        canvas.create_text(legend_x + len(colors) * (square_size + 2) + 15, legend_y, 
                          text="More", font=('Arial', 8), fill='#586069', anchor='w')
        
        # Draw summary (positioned properly to avoid overlap and clipping)
        total_sessions = sum(data['sessions'] for data in daily_activity.values())
        max_streak = calculate_gaming_streak(daily_activity, start_date, end_date)
        summary_text = f"{total_sessions} sessions in the last {date_range} days • Longest streak: {max_streak} days"
        
        # Position summary text below legend with proper spacing and centering
        summary_y = legend_y + 25
        # Center the text within the full canvas width for better visibility
        canvas.create_text(canvas_width//2, summary_y, text=summary_text, 
                          font=('Arial', 9), fill='#586069')
    
    return {
        'draw_function': draw_heatmap_on_canvas,
        'square_data': square_data,
        'canvas_key': canvas_key
    }

def setup_contributions_tooltip_callback(window, canvas_key='-CONTRIBUTIONS-CANVAS-'):
    """Set up tooltip callback for contributions canvas - now disabled since we use canvas-based tooltips"""
    tooltip_key = '-CONTRIBUTIONS-TOOLTIP-'
    
    def tooltip_callback(hovered_square):
        """Callback function - disabled since we use canvas-based tooltips at mouse position"""
        try:
            # Keep the external tooltip hidden since we use canvas-based tooltips
            window[tooltip_key].update(visible=False)
        except Exception as e:
            # Silently handle any errors to prevent crashes
            pass
    
    return tooltip_callback

def find_most_active_period(sessions, window_months=6):
    """Find the most active gaming period in the session data"""
    if not sessions:
        return datetime.now().date()
    
    # Convert window to days
    window_days = window_months * 30
    
    # Get all session dates
    session_dates = []
    for session in sessions:
        try:
            if 'start' in session:
                session_date = datetime.fromisoformat(session['start']).date()
                session_dates.append(session_date)
        except:
            continue
    
    if not session_dates:
        return datetime.now().date()
    
    session_dates.sort()
    
    # If we have less data than window size, return the latest date
    if len(session_dates) == 0:
        return datetime.now().date()
    
    # Find the period with the most sessions
    max_sessions = 0
    best_end_date = session_dates[-1]  # Default to latest
    
    # Try different end dates (sliding window approach)
    earliest_date = session_dates[0]
    latest_date = session_dates[-1]
    
    # Sample different end dates to find most active period
    current_date = latest_date
    while current_date >= earliest_date:
        start_date = current_date - timedelta(days=window_days)
        
        # Count sessions in this window
        sessions_in_window = sum(1 for date in session_dates 
                               if start_date <= date <= current_date)
        
        if sessions_in_window > max_sessions:
            max_sessions = sessions_in_window
            best_end_date = current_date
        
        # Move back by 30 days for next sample
        current_date -= timedelta(days=30)
    
    return best_end_date