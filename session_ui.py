"""
Session UI components.
Handles user interface elements for session management including popups and dialogs.
"""

import time
import PySimpleGUI as sg
from datetime import datetime, timedelta, date
from constants import STAR_FILLED, STAR_EMPTY, RATING_TAGS, NEGATIVE_TAGS, NEUTRAL_TAGS, POSITIVE_TAGS
from utilities import format_timedelta_with_seconds
from session_data import get_latest_session_end_time
from data_management import save_data
from discord_integration import get_discord_integration


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
    popup_width = max(400, 10 * name_length)  # Adjust width based on name length
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
    
    # Get Discord integration
    discord = get_discord_integration()

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
                    # Update Discord presence to show playing
                    discord.update_presence_playing(name, datetime.fromisoformat(session_start_time))
                
                start_time = time.time() - elapsed_time.total_seconds()
                running = True
                popup_window['-PLAY-'].update(disabled=True)
                
                # If resuming from pause, update Discord presence back to playing
                if current_pause:
                    discord.update_presence_playing(name, datetime.fromisoformat(session_start_time))
                
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
                
                # Update Discord presence to show paused
                discord.update_presence_paused(name)
                
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
                
                # Update Discord presence to show session complete
                discord.update_presence_session_complete(name, format_timedelta_with_seconds(elapsed_time))
                
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


def update_time_and_date(row_index, added_time, session, data_with_indices, data_storage=None):
    """Helper function to update time and last tracked date"""
    current_time_str = data_with_indices[row_index][1][3]
    if current_time_str:
        if isinstance(current_time_str, timedelta):
            current_time = current_time_str
        else:
            try:
                h, m, s = map(int, current_time_str.split(':'))
                current_time = timedelta(hours=h, minutes=m, seconds=s)
            except ValueError:
                try:
                    h, m = map(int, current_time_str.split(':'))
                    current_time = timedelta(hours=h, minutes=m)
                except ValueError:
                    current_time = timedelta()
    else:
        current_time = timedelta()
        
    added_seconds = added_time.total_seconds()
    added_time = timedelta(seconds=added_seconds)
    total_time = current_time + added_time
    
    new_time = format_timedelta_with_seconds(total_time)
    data_with_indices[row_index][1][3] = new_time
    
    if session:
        if len(data_with_indices[row_index][1]) <= 7 or data_with_indices[row_index][1][7] is None:
            data_with_indices[row_index][1].append([])
        
        data_with_indices[row_index][1][7].append(session)

    if len(data_with_indices[row_index][1]) > 7 and data_with_indices[row_index][1][7]:
        latest_end_time = get_latest_session_end_time(data_with_indices[row_index][1][7])
        if latest_end_time:
            data_with_indices[row_index][1][6] = latest_end_time.strftime('%Y-%m-%d %H:%M:%S')
        else:
            data_with_indices[row_index][1][6] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    else:
        data_with_indices[row_index][1][6] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if data_storage:
        original_index = data_with_indices[row_index][0]
        for i, (idx, _) in enumerate(data_storage):
            if idx == original_index:
                data_storage[i] = data_with_indices[row_index]
                break


def show_session_feedback_popup(existing_feedback=None):
    """Show a unified popup for session feedback (text + optional rating)"""
    is_edit = existing_feedback is not None
    
    if existing_feedback is None:
        existing_feedback = {'text': '', 'rating': None}
    
    existing_text = existing_feedback.get('text', '')
    existing_rating = existing_feedback.get('rating')
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
            rating_enabled = values['-ENABLE-RATING-']
            popup['-RATING-FRAME-'].update(visible=rating_enabled)
            
        elif event == '-RATING-STARS-':
            stars = int(values['-RATING-STARS-'])
            popup['-STARS-DISPLAY-'].update(STAR_FILLED * stars + STAR_EMPTY * (5 - stars))
            
        elif event == 'Save':
            feedback_text = values['-FEEDBACK-TEXT-'].strip()
            
            feedback = {
                'text': feedback_text if feedback_text else '',
                'timestamp': datetime.now().isoformat()
            }
            
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


def show_manual_session_popup(game_name):
    """Show a popup for manually adding a gaming session with start/end times and feedback"""
    today = date.today()
    default_start_date = today.strftime('%Y-%m-%d')
    default_start_time = '19:00'
    default_end_date = today.strftime('%Y-%m-%d')
    default_end_time = '21:00'
    
    layout = [
        [sg.Text(f"Add Manual Session for {game_name}", font=('Arial', 14, 'bold'))],
        [sg.HorizontalSeparator()],
        
        [sg.Column([
            [sg.Frame('Method 1: Start + End Times', [
                [sg.Text("Start Date:", size=(12, 1)), sg.Input(default_text=default_start_date, size=(12, 1), key='-START-DATE-'),
                 sg.CalendarButton('📅', target='-START-DATE-', format='%Y-%m-%d', size=(3, 1))],
                [sg.Text("Start Time:", size=(12, 1)), sg.Input(default_text=default_start_time, size=(8, 1), key='-START-TIME-'),
                 sg.Text("(HH:MM format)")],
                [sg.Text("End Date:", size=(12, 1)), sg.Input(default_text=default_end_date, size=(12, 1), key='-END-DATE-'),
                 sg.CalendarButton('📅', target='-END-DATE-', format='%Y-%m-%d', size=(3, 1))],
                [sg.Text("End Time:", size=(12, 1)), sg.Input(default_text=default_end_time, size=(8, 1), key='-END-TIME-'),
                 sg.Text("(HH:MM format)")]
            ], font=('Arial', 10), pad=((0, 0), (10, 15)))],
        ], element_justification='left'),
        sg.Column([
            [sg.Frame('Method 2: Duration + End Time', [
                [sg.Text("Duration:", size=(12, 1)), sg.Input(size=(8, 1), key='-DURATION-', enable_events=True),
                 sg.Text("(HH:MM format)")],
                [sg.Text("End Date:", size=(12, 1)), sg.Input(size=(12, 1), key='-END-DATE-ALT-', enable_events=True),
                 sg.CalendarButton('📅', target='-END-DATE-ALT-', format='%Y-%m-%d', size=(3, 1))],
                [sg.Text("End Time:", size=(12, 1)), sg.Input(size=(8, 1), key='-END-TIME-ALT-', enable_events=True),
                 sg.Text("(HH:MM format)")],
                [sg.Text("Calculated start:", size=(12, 1), font=('Arial', 9, 'italic')), 
                 sg.Text("", size=(20, 1), key='-CALC-START-', font=('Arial', 9, 'italic'))]
            ], font=('Arial', 10), pad=((0, 0), (10, 15)))],
        ], element_justification='left')],
        
        [sg.Checkbox('Add session feedback (notes, rating, tags)', default=False, key='-ENABLE-FEEDBACK-', enable_events=True, pad=((0, 0), (5, 5)))],
        
        [sg.Frame('Session Feedback (Optional)', [
            [sg.Text("Session thoughts/notes:")],
            [sg.Multiline(size=(45, 3), key='-FEEDBACK-TEXT-')],
            [sg.Checkbox('Rate this session', default=False, key='-ENABLE-RATING-', enable_events=True, pad=((0, 0), (5, 5)))],
            [sg.Column([
                [sg.Frame('Rating', [
                    [sg.Text('Stars (1-5):'), sg.Text(STAR_FILLED * 3 + STAR_EMPTY * 2, 
                                                     key='-STARS-DISPLAY-', font=('Arial', 14))],
                    [sg.Slider(range=(1, 5), default_value=3, 
                              orientation='h', size=(30, 15), key='-RATING-STARS-', enable_events=True)],
                    [sg.Text("Tags (optional):")],
                    [sg.Column([
                        [sg.Frame("Negative", [
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in NEGATIVE_TAGS[:5]
                            ]], element_justification='left')],
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in NEGATIVE_TAGS[5:]
                            ]], element_justification='left')]
                        ], font=('Arial', 8), relief=sg.RELIEF_SUNKEN, pad=((2, 2), (2, 2)))],
                        [sg.Frame("Neutral", [
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in NEUTRAL_TAGS[:5]
                            ]], element_justification='left')],
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in NEUTRAL_TAGS[5:]
                            ]], element_justification='left')]
                        ], font=('Arial', 8), relief=sg.RELIEF_SUNKEN, pad=((2, 2), (2, 2)))],
                        [sg.Frame("Positive", [
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in POSITIVE_TAGS[:5]
                            ]], element_justification='left')],
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in POSITIVE_TAGS[5:10]
                            ]], element_justification='left')],
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in POSITIVE_TAGS[10:15]
                            ]], element_justification='left')],
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in POSITIVE_TAGS[15:20]
                            ]], element_justification='left')],
                            [sg.Column([[
                                sg.Checkbox(tag, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1), font=('Arial', 8)) 
                                for tag in POSITIVE_TAGS[20:]
                            ]], element_justification='left')]
                        ], font=('Arial', 8), relief=sg.RELIEF_SUNKEN, pad=((2, 2), (2, 2)))]
                    ], scrollable=True, vertical_scroll_only=True, size=(520, 200), pad=((0, 0), (2, 5)))]
                ], pad=((0, 0), (2, 5)))]
            ], key='-RATING-FRAME-', visible=False, pad=((0, 0), (2, 5)))]
        ], key='-FEEDBACK-FRAME-', visible=False, expand_x=True, pad=((0, 0), (5, 10)))],
        
        [sg.HorizontalSeparator()],
        [sg.Button('Add Session'), sg.Button('Cancel')]
    ]
    
    popup = sg.Window("Add Manual Gaming Session", layout, modal=True, 
                     icon='gameslisticon.ico', finalize=True, resizable=True)
    
    while True:
        event, values = popup.read()
        
        if event in (sg.WIN_CLOSED, 'Cancel'):
            popup.close()
            return None
            
        elif event == '-ENABLE-FEEDBACK-':
            feedback_enabled = values['-ENABLE-FEEDBACK-']
            popup['-FEEDBACK-FRAME-'].update(visible=feedback_enabled)
            
            # Reset rating checkbox when feedback is disabled
            if not feedback_enabled:
                popup['-ENABLE-RATING-'].update(False)
                popup['-RATING-FRAME-'].update(visible=False)
            
        elif event == '-ENABLE-RATING-':
            rating_enabled = values['-ENABLE-RATING-']
            popup['-RATING-FRAME-'].update(visible=rating_enabled)
            
        elif event == '-RATING-STARS-':
            stars = int(values['-RATING-STARS-'])
            popup['-STARS-DISPLAY-'].update(STAR_FILLED * stars + STAR_EMPTY * (5 - stars))
            
        elif event in ['-DURATION-', '-END-DATE-ALT-', '-END-TIME-ALT-']:
            try:
                duration_str = values['-DURATION-'].strip()
                end_date_str = values['-END-DATE-ALT-'].strip()
                end_time_str = values['-END-TIME-ALT-'].strip()
                
                if duration_str and end_date_str and end_time_str:
                    if ':' in duration_str and len(duration_str.split(':')) == 2:
                        hours, minutes = map(int, duration_str.split(':'))
                        duration_td = timedelta(hours=hours, minutes=minutes)
                        
                        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                        end_time_obj = datetime.strptime(end_time_str, '%H:%M').time()
                        end_datetime = datetime.combine(end_date, end_time_obj)
                        
                        start_datetime = end_datetime - duration_td
                        
                        calc_text = f"{start_datetime.strftime('%Y-%m-%d %H:%M')}"
                        popup['-CALC-START-'].update(calc_text)
                        
                        popup['-START-DATE-'].update(start_datetime.strftime('%Y-%m-%d'))
                        popup['-START-TIME-'].update(start_datetime.strftime('%H:%M'))
                        popup['-END-DATE-'].update(end_date_str)
                        popup['-END-TIME-'].update(end_time_str)
                    else:
                        popup['-CALC-START-'].update("Invalid duration format")
                else:
                    popup['-CALC-START-'].update("")
            except (ValueError, TypeError):
                popup['-CALC-START-'].update("Invalid input")
            
        elif event == 'Add Session':
            try:
                start_date_str = values['-START-DATE-'].strip()
                start_time_str = values['-START-TIME-'].strip()
                end_date_str = values['-END-DATE-'].strip()
                end_time_str = values['-END-TIME-'].strip()
                
                if not all([start_date_str, start_time_str, end_date_str, end_time_str]):
                    sg.popup_error("All date and time fields are required!", title="Validation Error")
                    continue
                
                try:
                    start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
                    start_time_obj = datetime.strptime(start_time_str, '%H:%M').time()
                    end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
                    end_time_obj = datetime.strptime(end_time_str, '%H:%M').time()
                except ValueError as e:
                    sg.popup_error(f"Invalid date/time format: {str(e)}\n\nPlease use YYYY-MM-DD for dates and HH:MM for times.", 
                                  title="Format Error")
                    continue
                
                start_datetime = datetime.combine(start_date, start_time_obj)
                end_datetime = datetime.combine(end_date, end_time_obj)
                
                if end_datetime <= start_datetime:
                    sg.popup_error("End time must be after start time!", title="Validation Error")
                    continue
                
                duration_timedelta = end_datetime - start_datetime
                duration_str = format_timedelta_with_seconds(duration_timedelta)
                
                session = {
                    'start': start_datetime.isoformat(),
                    'end': end_datetime.isoformat(),
                    'duration': duration_str,
                    'pauses': []
                }
                
                if values['-ENABLE-FEEDBACK-']:
                    feedback_text = values['-FEEDBACK-TEXT-'].strip()
                    
                    feedback = {
                        'text': feedback_text if feedback_text else '',
                        'timestamp': datetime.now().isoformat()
                    }
                    
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
                    
                    if feedback['text'] or 'rating' in feedback:
                        session['feedback'] = feedback
                
                popup.close()
                return session
                
            except Exception as e:
                sg.popup_error(f"Error creating session: {str(e)}", title="Error")
                continue
    
    return None


def setup_contributions_tooltip_callback(window, canvas_key='-CONTRIBUTIONS-CANVAS-'):
    """Set up tooltip callback for contributions canvas - now disabled since we use canvas-based tooltips"""
    tooltip_key = '-CONTRIBUTIONS-TOOLTIP-'
    
    def tooltip_callback(hovered_square):
        """Callback function - disabled since we use canvas-based tooltips at mouse position"""
        try:
            window[tooltip_key].update(visible=False)
        except Exception as e:
            pass
    
    return tooltip_callback 