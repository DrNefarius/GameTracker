"""
Date-based Activity View for the GamesList application.
Provides functionality to view all gaming activity on a specific selected date.
"""

import PySimpleGUI as sg
from datetime import datetime, date, timedelta
from session_data import extract_all_sessions
from utilities import format_timedelta_with_seconds, calculate_popup_center_location


def get_sessions_for_date(data, target_date):
    """Get all gaming sessions for a specific date, sorted chronologically"""
    sessions_for_date = []
    
    # Extract all sessions from the data
    all_sessions = extract_all_sessions(data)
    
    # Filter sessions by the target date
    for session in all_sessions:
        try:
            if 'start' in session:
                session_start = datetime.fromisoformat(session['start'])
                session_date = session_start.date()
                
                if session_date == target_date:
                    # Add additional info for display
                    session_info = session.copy()
                    session_info['start_time'] = session_start.time()
                    
                    # Calculate end time if we have duration
                    if 'duration' in session:
                        duration_str = session['duration']
                        parts = duration_str.split(':')
                        if len(parts) == 3:
                            h, m, s = map(int, parts)
                            duration = timedelta(hours=h, minutes=m, seconds=s)
                            end_time = session_start + duration
                            session_info['end_time'] = end_time.time()
                            session_info['end_datetime'] = end_time
                        else:
                            session_info['end_time'] = None
                            session_info['end_datetime'] = session_start
                    else:
                        session_info['end_time'] = None
                        session_info['end_datetime'] = session_start
                    
                    sessions_for_date.append(session_info)
                    
        except (ValueError, TypeError) as e:
            print(f"Error processing session for date filtering: {str(e)}")
            continue
    
    # Sort sessions by start time
    sessions_for_date.sort(key=lambda x: x['start_time'])
    
    return sessions_for_date


def format_session_for_date_display(session):
    """Format a session for display in the date activity view"""
    game_name = session.get('game', 'Unknown Game')
    start_time = session.get('start_time')
    end_time = session.get('end_time')
    duration = session.get('duration', '00:00:00')
    
    # Format time range
    if start_time and end_time:
        time_range = f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}"
    elif start_time:
        time_range = f"{start_time.strftime('%H:%M')} - ?"
    else:
        time_range = "Unknown time"
    
    # Get session notes/feedback if available
    notes = ""
    if 'feedback' in session and session['feedback']:
        if 'text' in session['feedback'] and session['feedback']['text']:
            # Replace newlines and other whitespace with placeholder characters to keep single line
            raw_notes = session['feedback']['text'].replace('\n', ' • ').replace('\r', ' • ').replace('\t', ' ')
            # Remove multiple spaces
            raw_notes = ' '.join(raw_notes.split())
            notes = raw_notes[:100] + "..." if len(raw_notes) > 100 else raw_notes
    
    # Get rating if available  
    rating_display = ""
    if 'feedback' in session and session['feedback'] and 'rating' in session['feedback'] and session['feedback']['rating']:
        rating = session['feedback']['rating']
        if 'stars' in rating and rating['stars']:
            stars = rating['stars']
            rating_display = f" ({'★' * stars}{'☆' * (5 - stars)})"
    
    return {
        'game': game_name,
        'time_range': time_range,
        'duration': duration,
        'notes': notes,
        'rating_display': rating_display,
        'raw_session': session
    }


def calculate_daily_summary(sessions_for_date):
    """Calculate summary statistics for a day's gaming activities"""
    if not sessions_for_date:
        return {
            'total_sessions': 0,
            'total_time': timedelta(),
            'games_played': 0,
            'unique_games': []
        }
    
    total_time = timedelta()
    unique_games = set()
    
    for session in sessions_for_date:
        # Add duration to total
        if 'duration' in session:
            duration_str = session['duration']
            parts = duration_str.split(':')
            if len(parts) == 3:
                h, m, s = map(int, parts)
                total_time += timedelta(hours=h, minutes=m, seconds=s)
        
        # Track unique games
        game_name = session.get('game', 'Unknown Game')
        unique_games.add(game_name)
    
    return {
        'total_sessions': len(sessions_for_date),
        'total_time': total_time,
        'games_played': len(unique_games),
        'unique_games': sorted(list(unique_games))
    }


def show_date_activity_view(target_date, data, parent_window=None):
    """Show a dialog displaying all gaming activity for a specific date"""
    # Update Discord presence for viewing daily activity
    from discord_integration import get_discord_integration
    discord = get_discord_integration()
    discord.update_presence_viewing_daily_activity(target_date)
    
    # Get sessions for the target date
    sessions_for_date = get_sessions_for_date(data, target_date)
    
    # Calculate daily summary
    daily_summary = calculate_daily_summary(sessions_for_date)
    
    # Format date for display
    date_str = target_date.strftime('%A, %B %d, %Y')
    
    # Create layout for the dialog
    if sessions_for_date:
        # Format sessions for display
        session_table_data = []
        for session in sessions_for_date:
            formatted = format_session_for_date_display(session)
            session_table_data.append([
                formatted['game'],
                formatted['time_range'],
                formatted['duration'],
                formatted['notes'],
                formatted['rating_display']
            ])
        
        # Summary section
        summary_text = (f"Total Sessions: {daily_summary['total_sessions']}  |  "
                       f"Total Time: {format_timedelta_with_seconds(daily_summary['total_time'])}  |  "
                       f"Games Played: {daily_summary['games_played']}")
        
        games_list_text = "Games: " + ", ".join(daily_summary['unique_games'])
        
        layout = [
            [sg.Text(f"Gaming Activity for {date_str}", font=('Helvetica', 14, 'bold'), justification='center')],
            [sg.HorizontalSeparator()],
            [sg.Text(summary_text, font=('Helvetica', 10), justification='center')],
            [sg.Text(games_list_text, font=('Helvetica', 9), justification='center', text_color='white')],
            [sg.HorizontalSeparator()],
            [sg.Table(
                values=session_table_data,
                headings=['Game', 'Time Range', 'Duration', 'Notes', 'Rating'],
                auto_size_columns=False,
                col_widths=[25, 15, 10, 40, 8],
                num_rows=min(15, len(session_table_data)),
                justification='left',
                key='-DATE-SESSIONS-TABLE-',
                enable_events=True,
                expand_x=True,
                expand_y=True,
                alternating_row_color='#1e3a8a'
            )],
            [sg.HorizontalSeparator()],
            [sg.Button('Close', size=(10, 1)), 
             sg.Button('Previous Day', key='-PREV-DAY-', size=(12, 1)),
             sg.Button('Next Day', key='-NEXT-DAY-', size=(12, 1))]
        ]
    else:
        # No sessions found for this date
        layout = [
            [sg.Text(f"Gaming Activity for {date_str}", font=('Helvetica', 14, 'bold'), justification='center')],
            [sg.HorizontalSeparator()],
            [sg.VPush()],
            [sg.Text("No gaming activity recorded for this date", 
                    font=('Helvetica', 12), justification='center', text_color='white')],
            [sg.VPush()],
            [sg.HorizontalSeparator()],
            [sg.Button('Close', size=(10, 1)),
             sg.Button('Previous Day', key='-PREV-DAY-', size=(12, 1)),
             sg.Button('Next Day', key='-NEXT-DAY-', size=(12, 1))]
        ]
    
    # Calculate window location
    if parent_window:
        location = calculate_popup_center_location(parent_window, popup_width=800, popup_height=500)
    else:
        location = None
    
    # Create and show the window
    window = sg.Window(
        f'Daily Activity - {date_str}',
        layout,
        modal=False,  # Changed to non-modal to prevent event interference
        finalize=True,
        resizable=True,
        size=(800, 500),
        location=location,
        keep_on_top=True  # Keep on top instead of modal
    )
    
    current_date = target_date
    
    # Event loop
    while True:
        event, values = window.read()
        
        if event == sg.WIN_CLOSED or event == 'Close':
            break
        elif event == '-PREV-DAY-':
            current_date = current_date - timedelta(days=1)
            window.close()
            # Recursively show previous day (Discord presence will be updated automatically)
            show_date_activity_view(current_date, data, parent_window)
            # Return early since new window handles its own Discord presence
            return
        elif event == '-NEXT-DAY-':
            current_date = current_date + timedelta(days=1)
            window.close()
            # Recursively show next day (Discord presence will be updated automatically)
            show_date_activity_view(current_date, data, parent_window)
            # Return early since new window handles its own Discord presence
            return
        elif event == '-DATE-SESSIONS-TABLE-' and values['-DATE-SESSIONS-TABLE-']:
            # Handle session table click (could add session details popup here)
            try:
                selected_row = values['-DATE-SESSIONS-TABLE-'][0]
                if 0 <= selected_row < len(sessions_for_date):
                    selected_session = sessions_for_date[selected_row]
                    show_session_details_popup(selected_session, current_date, window)
            except (IndexError, TypeError):
                pass
    
    window.close()
    
    # Restore Discord presence back to browsing Statistics tab
    discord.update_presence_browsing("Statistics")
    
    # Ensure focus returns to parent window
    if parent_window:
        try:
            parent_window.TKroot.focus_force()
        except:
            pass


def show_session_details_popup(session, session_date, parent_window=None):
    """Show detailed information about a specific session"""
    game_name = session.get('game', 'Unknown Game')
    start_time = session.get('start_time')
    end_time = session.get('end_time')
    duration = session.get('duration', '00:00:00')
    
    # Format session info
    session_info = []
    session_info.append(f"Game: {game_name}")
    
    if start_time and end_time:
        session_info.append(f"Time: {start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M')}")
    elif start_time:
        session_info.append(f"Start: {start_time.strftime('%H:%M')}")
    
    session_info.append(f"Duration: {duration}")
    
    # Add feedback/notes if available
    notes_text = ""
    rating_text = ""
    
    if 'feedback' in session and session['feedback']:
        feedback = session['feedback']
        
        if 'text' in feedback and feedback['text']:
            notes_text = feedback['text']
        
        if 'rating' in feedback and feedback['rating']:
            rating = feedback['rating']
            if 'stars' in rating and rating['stars']:
                stars = rating['stars']
                rating_text = f"Rating: {'★' * stars}{'☆' * (5 - stars)} ({stars}/5)"
            
            if 'tags' in rating and rating['tags']:
                rating_text += f"\nTags: {', '.join(rating['tags'])}"
    
    # Create layout
    layout = [
        [sg.Text(f"Session Details - {session_date.strftime('%B %d, %Y')}", 
                font=('Helvetica', 12, 'bold'))],
        [sg.HorizontalSeparator()],
    ]
    
    # Add session information
    for info in session_info:
        layout.append([sg.Text(info, font=('Helvetica', 10))])
    
    # Add rating if available
    if rating_text:
        layout.extend([
            [sg.HorizontalSeparator()],
            [sg.Text(rating_text, font=('Helvetica', 10))]
        ])
    
    # Add notes if available
    if notes_text:
        layout.extend([
            [sg.HorizontalSeparator()],
            [sg.Text("Notes:", font=('Helvetica', 10, 'bold'))],
            [sg.Multiline(
                notes_text,
                size=(60, 8),
                disabled=True,
                no_scrollbar=False,
                expand_x=True,
                expand_y=True
            )]
        ])
    
    layout.append([sg.HorizontalSeparator()])
    layout.append([sg.Button('Close', size=(10, 1))])
    
    # Calculate window location
    if parent_window:
        location = calculate_popup_center_location(parent_window, popup_width=500, popup_height=400)
    else:
        location = None
    
    # Create and show the window
    detail_window = sg.Window(
        'Session Details',
        layout,
        modal=False,
        finalize=True,
        resizable=True,
        size=(500, 400),
        location=location,
        keep_on_top=True
    )
    
    # Simple event loop
    while True:
        event, values = detail_window.read()
        if event == sg.WIN_CLOSED or event == 'Close':
            break
    
    detail_window.close()


def show_date_picker_dialog(parent_window=None):
    """Show a date picker dialog to allow users to select a specific date"""
    today = date.today()
    
    layout = [
        [sg.Text("Select a date to view gaming activity:", font=('Helvetica', 12))],
        [sg.HorizontalSeparator()],
        [sg.Text("Date (YYYY-MM-DD):")],
        [sg.Input(default_text=today.strftime('%Y-%m-%d'), key='-DATE-INPUT-', size=(15, 1)),
         sg.CalendarButton('Calendar', target='-DATE-INPUT-', format='%Y-%m-%d')],
        [sg.HorizontalSeparator()],
        [sg.Button('View Activity', bind_return_key=True), sg.Button('Cancel')]
    ]
    
    # Calculate window location
    if parent_window:
        location = calculate_popup_center_location(parent_window, popup_width=300, popup_height=150)
    else:
        location = None
    
    # Create and show the window
    window = sg.Window(
        'Select Date',
        layout,
        modal=False,
        finalize=True,
        size=(300, 150),
        location=location,
        keep_on_top=True
    )
    
    selected_date = None
    
    # Event loop
    while True:
        event, values = window.read()
        
        if event == sg.WIN_CLOSED or event == 'Cancel':
            break
        elif event == 'View Activity':
            try:
                # Parse the entered date
                date_str = values['-DATE-INPUT-'].strip()
                selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                break
            except ValueError:
                sg.popup_error('Invalid date format. Please use YYYY-MM-DD format.',
                              title='Invalid Date', location=location)
    
    window.close()
    return selected_date
