"""
Session display and formatting functions.
Handles formatting session data for UI display and presentation.
"""

import PySimpleGUI as sg
from datetime import datetime
from constants import STAR_FILLED, STAR_EMPTY
from session_data import get_status_history


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