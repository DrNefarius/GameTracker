"""
Session data operations and extraction functions.
Handles session data retrieval, statistics calculation, and basic session operations.
"""

from datetime import datetime, timedelta
from collections import defaultdict, Counter
from utilities import format_timedelta_with_seconds


def get_latest_session_end_time(sessions):
    """Helper function to find the latest session end time from a list of sessions"""
    latest_end_time = None
    for session in sessions:
        if 'end' in session:
            try:
                session_end = datetime.fromisoformat(session['end'])
                if latest_end_time is None or session_end > latest_end_time:
                    latest_end_time = session_end
            except (ValueError, TypeError):
                continue
    return latest_end_time


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


def get_status_history(data, game_name):
    """Get status history for a specific game"""
    for idx, game_data in data:
        if game_data[0] == game_name:
            if len(game_data) > 8 and game_data[8]:
                return game_data[8]
            break
    
    return []


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


def add_manual_session_to_game(game_name, session, data_with_indices, data_storage=None):
    """Add a manually created session to a game's session list"""
    # Find the game in the data
    for idx, (original_idx, game_data) in enumerate(data_with_indices):
        if game_data[0] == game_name:
            # Initialize sessions array if it doesn't exist
            if len(game_data) <= 7 or game_data[7] is None:
                game_data.append([])
            
            # Add the new session
            game_data[7].append(session)
            
            # Update the game's total time
            try:
                # Parse session duration
                duration_str = session['duration']
                parts = duration_str.split(':')
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    session_duration = timedelta(hours=h, minutes=m, seconds=s)
                    
                    # Get current time
                    current_time_str = game_data[3]
                    if current_time_str:
                        if isinstance(current_time_str, timedelta):
                            current_time = current_time_str
                        else:
                            try:
                                h2, m2, s2 = map(int, current_time_str.split(':'))
                                current_time = timedelta(hours=h2, minutes=m2, seconds=s2)
                            except ValueError:
                                current_time = timedelta()
                    else:
                        current_time = timedelta()
                    
                    # Add session duration to total time
                    new_total_time = current_time + session_duration
                    game_data[3] = format_timedelta_with_seconds(new_total_time)
                    
                    # Update last played date to the latest session end time
                    latest_end_time = get_latest_session_end_time(game_data[7])
                    if latest_end_time:
                        game_data[6] = latest_end_time.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        # Fallback to current time if no valid session end times found
                        game_data[6] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
            except Exception as e:
                print(f"Error updating game time for manual session: {str(e)}")
            
            # Update the full dataset when modifying filtered data
            if data_storage:
                # Find and update the correct entry in data_storage
                for i, (storage_idx, _) in enumerate(data_storage):
                    if storage_idx == original_idx:
                        data_storage[i] = (original_idx, game_data)
                        break
            
            return True
    
    return False


def find_most_active_period(sessions, window_months=1):
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