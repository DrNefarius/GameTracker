"""
UI components for the GamesList application.
Handles main layout creation, popups, and user interface elements.
"""

import re
import PySimpleGUI as sg
from datetime import datetime, timedelta

from constants import STAR_FILLED, STAR_EMPTY, RATING_TAGS, COMPLETED_STYLE, IN_PROGRESS_STYLE, FUTURE_RELEASE_STYLE, DEFAULT_STYLE
from ratings import format_rating, calculate_session_rating_average, show_rating_popup
from utilities import calculate_pixel_width, get_game_table_row_colors, format_timedelta_with_seconds, format_timedelta
from game_statistics import count_total_completed, count_total_entries, calculate_completion_percentage, calculate_total_time

def get_discord_menu_text():
    """Get the current Discord menu text based on enabled status"""
    from config import load_config
    config = load_config()
    discord_enabled = config.get('discord_enabled', True)
    return f"Discord: {'Enabled' if discord_enabled else 'Disabled'}::discord_toggle"

def get_display_row_with_rating(row):
    """Process a data row to add rating display formatting and arrange for table display"""
    # The table expects 8 columns: Name, Release, Platform, Time, Status, Owned, Last Played, Rating
    # But our data has 10 elements: [0-6] basic data, [7] sessions, [8] status_history, [9] rating
    # We need to reorder to put rating at index 7 for the table
    
    # Start with the basic game info (indices 0-6)
    display_row = row[:7].copy()
    
    # Get rating data (from index 9) and format it
    game_rating = None
    is_calculated = False
    if len(row) > 9 and row[9] and isinstance(row[9], dict):
        game_rating = row[9]
        # Check if this is a calculated rating
        is_calculated = game_rating.get('auto_calculated', False)
    
    # If no direct rating OR if it's auto-calculated, try to calculate from sessions (index 7)
    # This ensures auto-calculated ratings stay current with new session ratings
    if (not game_rating or is_calculated) and len(row) > 7 and row[7]:
        sessions_rating = calculate_session_rating_average(row[7])
        if sessions_rating:
            # Create a rating object from the calculated average
            game_rating = {'stars': int(round(sessions_rating)), 'auto_calculated': True}
            is_calculated = True
            
            # Store calculated rating in original row for future use
            while len(row) <= 9:
                row.append(None)
            row[9] = game_rating
    
    # Format the rating as stars for display and add it at index 7
    if game_rating:
        formatted_rating = format_rating(game_rating)
        # Add a symbol to indicate calculated ratings (≈ for calculated, space for manual to align stars)
        if is_calculated:
            formatted_rating = "≈" + formatted_rating
        else:
            formatted_rating = "  " + formatted_rating  # Add space to align with calculated ratings
        display_row.append(formatted_rating)
    else:
        display_row.append("")
        
    # The display row now has 8 elements: [0-6] basic data, [7] formatted rating
    # This matches the table headers: Name, Release, Platform, Time, Status, Owned, Last Played, Rating
    return display_row

def validate_entry_form(values):
    """Validate form input from the entry popup"""
    errors = []
    
    # Validate name (required)
    if not values['-NEW-NAME-'].strip():
        errors.append("Name is required")
    
    # Validate release date format
    release_date = values.get('-NEW-RELEASE-', '')
    if release_date and release_date != '-':
        try:
            datetime.strptime(release_date, '%Y-%m-%d')
        except ValueError:
            errors.append("Release date must be in YYYY-MM-DD format or '-' for unknown")
    
    # Validate time format with stricter validation
    time_value = values['-NEW-TIME-']
    if time_value and time_value not in ['00:00:00', '00:00']:
        # Use the stricter pattern from handle_add_entry for consistency
        time_pattern = re.compile(r'^\d{2,4}:\d{2}:\d{2}$')
        if not time_pattern.match(time_value):
            errors.append("Time must be in hh:mm:ss, hhh:mm:ss, or hhhh:mm:ss format")
        else:
            # Validate time parts are valid numbers
            try:
                parts = time_value.split(':')
                hours, minutes, seconds = map(int, parts)
                if minutes >= 60 or seconds >= 60:
                    errors.append("Minutes and seconds must be less than 60")
            except ValueError:
                errors.append("Time parts must be valid numbers")
    
    # Return errors if any, otherwise None
    return errors if errors else None

def create_entry_popup(existing_entry=None):
    """Create a popup for adding or editing a game entry"""
    # Prepare default values with validation
    default_name = existing_entry[0] if existing_entry else ''
    default_release = existing_entry[1] if existing_entry else ''
    default_platform = existing_entry[2] if existing_entry else ''
    default_time = existing_entry[3] if existing_entry and existing_entry[3] not in [None, ''] else '00:00:00'
    default_status = existing_entry[4] if existing_entry and existing_entry[4] in ['Pending', 'In progress', 'Completed'] else 'Pending'
    default_owned = (existing_entry[5] == '✅') if existing_entry else False
    default_rating = existing_entry[9] if existing_entry and len(existing_entry) > 9 else None
    
    # Format rating display
    rating_text = format_rating(default_rating) if default_rating else "Not Rated"
    
    layout = [
        [sg.Text('Name*'), sg.InputText(key='-NEW-NAME-', default_text=default_name)],
        [sg.Text('Release Date'), sg.Input(key='-NEW-RELEASE-', size=(10, 1), default_text=default_release), 
         sg.CalendarButton('Choose Date', target='-NEW-RELEASE-', key='-CALENDAR-', format='%Y-%m-%d')],
        [sg.Text('Platform'), sg.InputText(key='-NEW-PLATFORM-', default_text=default_platform)],
        [sg.Text('Time Played'), sg.InputText(key='-NEW-TIME-', default_text=default_time, 
                 tooltip='Format: HH:MM:SS (e.g. 01:30:45 for 1 hour, 30 minutes, 45 seconds)')],
        [sg.Text('Status'), sg.Combo(['Pending', 'In progress', 'Completed'], key='-NEW-STATUS-', 
                 default_value=default_status, readonly=True)],
        [sg.Text('Owned'), sg.Checkbox('', key='-NEW-OWNED-', default=default_owned)],
        [sg.Text('Rating'), sg.Text(rating_text, key='-RATING-TEXT-', size=(15,1)), 
         sg.Button(f"{'Edit' if default_rating else 'Add'} Rating", key='-EDIT-RATING-')],
        [sg.Text('* Required field', font=('Helvetica', 8))],
        [sg.Button('Submit'), sg.Button('Cancel')]
    ]
    
    # Add Delete button only if editing an existing entry
    if existing_entry:
        layout[-1][0:0] = [sg.Button('Delete', button_color=('white', 'red'))]
    
    popup_window = sg.Window('Edit Entry' if existing_entry else 'Add New Entry', layout, modal=True, finalize=True, icon='gameslisticon.ico')
    
    # Variable to store the rating
    current_rating = default_rating
    
    # Event loop
    while True:
        event, values = popup_window.read()
        
        if event in (sg.WIN_CLOSED, 'Cancel'):
            popup_window.close()
            return None, None, None
        
        elif event == '-EDIT-RATING-':
            # Open rating popup
            new_rating = show_rating_popup(current_rating)
            if new_rating:
                current_rating = new_rating
                # Update rating display
                popup_window['-RATING-TEXT-'].update(format_rating(current_rating))
        
        elif event == 'Delete':
            popup_window.close()
            return None, 'Delete', None
        
        elif event == 'Submit':
            # Validate form
            errors = validate_entry_form(values)
            if errors:
                sg.popup('\n'.join(errors), title='Error')
                continue
                
            # Return values for processing
            popup_window.close()
            return values, 'Submit', current_rating
    
    return None, None, None

def show_game_actions_dialog(row_index, data_with_indices):
    """Show a dialog with game action options instead of right-click context menu"""
    if row_index is None or row_index >= len(data_with_indices):
        return None

    game_data = data_with_indices[row_index][1]
    game_name = game_data[0]
    
    # Create actions popup
    actions_popup = sg.Window(f"Actions for {game_name}", 
                            [[sg.Text(f"What would you like to do with '{game_name}'?")],
                            [sg.Button("Track Time"), sg.Button("Edit Game"), sg.Button("Rate Game"), sg.Button("Add Session")],
                            [sg.Button("Cancel")]],
                            modal=True, icon='gameslisticon.ico')
    
    action, _ = actions_popup.read()
    actions_popup.close()
    
    return action

def update_table_display(data_with_indices, window):
    """Update the table display with the current data"""
    # Get formatted display values
    display_values = [get_display_row_with_rating(row[1]) for row in data_with_indices]
    
    # Get enhanced row colors
    row_colors = get_game_table_row_colors(data_with_indices)
    
    # Update the table with new data
    window['-TABLE-'].update(
        values=display_values,
        row_colors=row_colors
    )
    
    return display_values

def get_table_column_widths(data_with_indices):
    """Calculate optimal column widths for the table"""
    # Define the headings
    headings = ["Name", "Release", "Platform", "Time", "Status", "Owned", "Last Played", "Rating"]
    
    # Format table data for display
    table_data = []
    for t_row in data_with_indices:
        row = t_row[1]
        display_row = get_display_row_with_rating(row)
        table_data.append(display_row)
    
    # Find the longest string in each column
    max_strings = [max((str(cell) for cell in col), key=len) for col in zip(*([headings] + table_data))]
    
    # Calculate the pixel width of the longest strings
    font = ('Helvetica', 10)
    col_widths = [calculate_pixel_width(max_str, font) for max_str in max_strings]
    
    # Adjust col_widths to a more reasonable character width for PySimpleGUI
    col_widths = [int(width / 7.5) for width in col_widths]
    
    # Make the rating column wider to accommodate 5 stars plus the ≈ symbol
    if len(col_widths) > 7:  # Rating column
        col_widths[7] = 11  # Increase from 6 to 9 to fit "≈★★★★★"
        
    return col_widths, headings

def create_main_layout(data_with_indices):
    """Create the main application layout with tabs"""
    # Get column widths and headings
    col_widths, headings = get_table_column_widths(data_with_indices)
    
    # Format the Time column values and create table data
    table_data = []
    for t_row in data_with_indices:
        row = t_row[1]
        # Handle time formatting
        if row[3] and isinstance(row[3], str):
            try:
                h, m, s = map(int, row[3].split(':'))
                row[3] = format_timedelta_with_seconds(timedelta(hours=h, minutes=m, seconds=s))
            except ValueError:
                try:
                    h, m = map(int, row[3].split(':'))
                    row[3] = format_timedelta(timedelta(hours=h, minutes=m))
                except ValueError:
                    row[3] = '00:00:00'
        elif isinstance(row[3], timedelta):
            row[3] = format_timedelta_with_seconds(row[3])
        
        # Handle last played date formatting
        if row[6] and isinstance(row[6], str):
            try:
                row[6] = datetime.strptime(row[6], '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                row[6] = "Not played"
        
        # Process row to handle rating display
        display_row = get_display_row_with_rating(row)
        table_data.append(display_row)
    
    # Get enhanced row colors
    row_colors = get_game_table_row_colors(data_with_indices)

    # Tab 1 - Data Table
    tab1_layout = [
        [sg.InputText(key='-SEARCH-', size=(20, 1)), sg.Button('Search'), sg.Button('Reset'), sg.Button('Save'), sg.Button('Add Entry')],
        [sg.Table(values=table_data, headings=headings, auto_size_columns=False, display_row_numbers=True,
                  justification='left', num_rows=min(25, len(data_with_indices)), key='-TABLE-',
                  enable_events=True, expand_x=True, expand_y=True, col_widths=col_widths,
                  enable_click_events=True, vertical_scroll_only=True,
                  row_colors=row_colors)],
        [sg.Combo(['Pending', 'In progress', 'Completed'], key='-STATUS-', readonly=True, visible=False)]
    ]

    # Tab 2 - Summary with visualizations (scrollable)
    summary_content = [
        [sg.Text("Games Summary Dashboard", font=("Helvetica", 16, "bold"), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        
        # Refresh button at the top for easy access
        [sg.Button("Refresh Charts", key='-REFRESH-CHARTS-', pad=(0, (10, 20)))],
        
        # Key metrics row
        [sg.Frame('Key Metrics', [
            [sg.Text(f"Total Games: {count_total_entries(data_with_indices)}", font=('Helvetica', 12), pad=(10, 5), size=(20, 1)),
             sg.Text(f"Completed: {count_total_completed(data_with_indices)}", font=('Helvetica', 12), pad=(10, 5), size=(15, 1)),
             sg.Text(f"Completion: {calculate_completion_percentage(count_total_completed(data_with_indices), count_total_entries(data_with_indices)):.1f}%", font=('Helvetica', 12), pad=(10, 5), size=(15, 1))],
            [sg.Text(f"Total Play Time: {calculate_total_time(data_with_indices)}", font=('Helvetica', 12), pad=(10, 5), key='-TOTAL-TIME-')]
        ], font=('Helvetica', 12), expand_x=True)],
        
        [sg.VPush()],  # Add some vertical space
        
        # Graphs row - side by side with smaller sizes
        [sg.Column([
            [sg.Text("Status Distribution", font=('Helvetica', 11), justification='center', expand_x=True)],
            [sg.Image(key='-PIE-CHART-', size=(280, 220))]
        ], justification='center'), 
         sg.Column([
            [sg.Text("Top Games by Playtime", font=('Helvetica', 11), justification='center', expand_x=True)],
            [sg.Image(key='-PLAYTIME-CHART-', size=(420, 280))]
        ], justification='center')],
        
        [sg.VPush()],  # Add some vertical space
        
        # Year distribution chart (full width but smaller)
        [sg.Text("Games by Release Year", font=('Helvetica', 11), justification='center', expand_x=True)],
        [sg.Image(key='-YEAR-CHART-', size=(700, 220), pad=(0, (10, 10)))],
        
        [sg.VPush()],  # Add some vertical space
        
        # Ratings distribution chart (full width but smaller)
        [sg.Text("Game Ratings Distribution", font=('Helvetica', 11), justification='center', expand_x=True)],
        [sg.Image(key='-RATING-CHART-', size=(480, 220), pad=(0, (10, 10)))],
        
        [sg.VPush()]  # Add some vertical space
    ]
    
    # Wrap the content in a scrollable column with proper bottom padding
    tab2_layout = [
        [sg.Column(summary_content, 
                   scrollable=True, 
                   vertical_scroll_only=True,
                   size=(None, 700),  # Fixed height of 700 pixels
                   expand_x=True,
                   pad=(10, (10, 40)))]  # Added bottom padding to the column itself
    ]

    # Tab 3 - Game Sessions Statistics (scrollable)
    statistics_content = [
        [sg.Text("Game Session & Status Statistics", font=("Helvetica", 16, "bold"), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        
        # Refresh button at the top for easy access
        [sg.Button("Refresh Statistics", key='-REFRESH-STATS-', pad=(0, (10, 20)))],
        
        # Top row with game selection and rating comparison
        [sg.Column([
            # Game selection section
            [sg.Frame('Select Game', [
                [sg.Input(key='-SESSION-SEARCH-', size=(30, 1), enable_events=True),
                 sg.Button('Search Games', key='-SESSION-SEARCH-BTN-')],
                [sg.Listbox(values=[], size=(40, 6), key='-GAME-LIST-', enable_events=True, 
                           select_mode=sg.LISTBOX_SELECT_MODE_SINGLE)],
                [sg.Button('Show All Games', key='-SHOW-ALL-GAMES-', tooltip='Reset selection and show statistics for all games')]
            ], font=('Helvetica', 11))]
        ], vertical_alignment='top'), 
         sg.VSeperator(),
         sg.Column([
            # Rating comparison section
            [sg.Frame('Rating Comparison', [
                [sg.Column([
                    [sg.Text("Auto-Calculated from Sessions", font=('Helvetica', 10, 'bold'), justification='center')],
                    [sg.Text("", key='-AUTO-RATING-STARS-', font=('Arial', 14), justification='center', pad=((0, 0), (6, 0)))],
                    [sg.Text("", key='-AUTO-RATING-INFO-', font=('Helvetica', 8), justification='center', size=(25, 1))],
                    [sg.Text("Common tags:", font=('Helvetica', 8), justification='center')],
                    [sg.Text("", key='-AUTO-RATING-TAGS-', font=('Helvetica', 8), size=(30, 4), justification='center')]
                ], element_justification='center', vertical_alignment='top'),
                 sg.VSeperator(),
                 sg.Column([
                    [sg.Text("Manual Game Rating", font=('Helvetica', 10, 'bold'), justification='center', expand_x=True)],
                    [sg.Column([
                        [sg.Text("", key='-MANUAL-RATING-STARS-', font=('Arial', 14), justification='center')],
                        [sg.Text("", key='-MANUAL-RATING-INFO-', font=('Helvetica', 8), justification='center', size=(15, 1))],
                        [sg.Text("Tags:", font=('Helvetica', 8), justification='center')],
                        [sg.Text("", key='-MANUAL-RATING-TAGS-', font=('Helvetica', 8), size=(20, 3), justification='center')]
                    ], element_justification='center', vertical_alignment='top'),
                      sg.VSeperator(),
                      sg.Column([
                         [sg.Text("Comment:", font=('Helvetica', 8, 'bold'), justification='left')],
                         [sg.Multiline("", key='-MANUAL-RATING-COMMENT-', font=('Helvetica', 8), size=(25, 4), 
                                      disabled=True, autoscroll=False,
                                      background_color='#f0f0f0', text_color='black', expand_y=True)]
                     ], vertical_alignment='top', expand_y=True)]
                 ], element_justification='center', vertical_alignment='top', expand_x=True)]
            ], font=('Helvetica', 11), visible=False, key='-RATING-COMPARISON-')]
        ], vertical_alignment='top', element_justification='center')],
        
        [sg.VPush()],  # Add some vertical space
        
        # Overall session statistics
        [sg.Frame('Overall Session Statistics', [
            [sg.Text("Total Sessions: 0", key='-TOTAL-SESSIONS-', size=(30, 1)),
             sg.Text("Total Session Time: 00:00:00", key='-TOTAL-SESSION-TIME-', size=(30, 1))],
            [sg.Text("Average Session Length: 00:00:00", key='-AVG-SESSION-', size=(30, 1)),
             sg.Text("Most Active Day: None", key='-MOST-ACTIVE-DAY-', size=(40, 1))]
        ], font=('Helvetica', 11))],
        
        [sg.VPush()],  # Add some vertical space
        
        # Selected game session details
        [sg.Frame('Selected Game Data', [
            [sg.Text("No game selected", font=('Helvetica', 12, 'bold'), key='-SELECTED-GAME-')],
            [sg.Text("Sessions: 0", key='-GAME-SESSIONS-', size=(20, 1)),
             sg.Text("Total Time: 00:00:00", key='-GAME-SESSION-TIME-', size=(20, 1))],
            [sg.Button("View Activity Log", key='-VIEW-ALL-NOTES-', tooltip='Display the activity log for the selected game', size=(15, 1)),
             sg.Button("Add Session", key='-ADD-SESSION-', tooltip='Manually add a gaming session for the selected game', size=(12, 1))],
            [sg.TabGroup([
                [sg.Tab('Sessions', [
                    [sg.Text("Click on a session with [FEEDBACK] to view or edit feedback", font=('Helvetica', 8, 'italic'))],
                    [sg.Table(values=[], headings=['Start Date', 'Duration', 'Details'], 
                             auto_size_columns=False, num_rows=5, key='-SESSIONS-TABLE-',
                             col_widths=[20, 12, 60], justification='left', enable_events=True)]
                ]),
                 sg.Tab('Status Changes', [
                    [sg.Table(values=[], headings=['Date', 'From Status', 'To Status'], 
                             auto_size_columns=False, num_rows=5, key='-STATUS-HISTORY-TABLE-',
                             col_widths=[20, 15, 15], justification='left', enable_events=True)]
                ])]
            ], key='-GAME-DETAILS-TABS-', expand_x=True)]
        ], font=('Helvetica', 11), expand_x=True)],
        
        [sg.VPush()],  # Add some vertical space
        
        # Session visualization
        [sg.Frame('Visualizations', [
            [sg.TabGroup([
                [sg.Tab('Contributions Map', [
                    [sg.Text("GitHub-style gaming activity overview", font=('Helvetica', 9, 'italic'), justification='center', expand_x=True)],
                    [sg.Column([
                        [sg.Text("Year:", font=('Arial', 9)),
                         sg.Button("◀", key='-CONTRIB-YEAR-PREV-', size=(2, 1)),
                         sg.Text("2025", key='-CONTRIB-YEAR-DISPLAY-', font=('Arial', 10, 'bold'), size=(6, 1), justification='center'),
                         sg.Button("▶", key='-CONTRIB-YEAR-NEXT-', size=(2, 1))]
                    ], element_justification='center', pad=(0, (5, 10)))],
                    [sg.Canvas(size=(900, 360), key='-CONTRIBUTIONS-CANVAS-', background_color='white', expand_x=True, expand_y=True)],
                    [sg.Text('', key='-CONTRIBUTIONS-TOOLTIP-', font=('Arial', 9), 
                             background_color='black', text_color='white', 
                             pad=(5, 5), visible=False)]
                ]),
                 sg.Tab('Session Timeline', [
                    [sg.Image(key='-SESSIONS-TIMELINE-', size=(650, 220))]
                ]), 
                 sg.Tab('Session Distribution', [
                    [sg.Text("Session length data visualization", font=('Helvetica', 9, 'italic'), justification='center', expand_x=True)],
                    [sg.Column([
                        [sg.Text("Chart Type:", font=('Arial', 9)),
                         sg.Combo(['Line Chart', 'Scatter Plot', 'Box Plot', 'Histogram'], 
                                  default_value='Line Chart', key='-DISTRIBUTION-CHART-TYPE-', size=(12, 1), enable_events=True,
                                  tooltip='Choose how to visualize session length data')]
                    ], element_justification='center', pad=(0, (5, 10)))],
                    [sg.Image(key='-SESSIONS-DISTRIBUTION-', size=(650, 220))]
                ]),
                 sg.Tab('Gaming Heatmap', [
                    [sg.Text("Time-based gaming session intensity with pause visualization", font=('Helvetica', 9, 'italic'), justification='center', expand_x=True)],
                    [sg.Column([
                        [sg.Text("Window Size:", font=('Arial', 9)),
                         sg.Combo(['1 Month', '3 Months', '6 Months', '1 Year'], 
                                  default_value='1 Month', key='-HEATMAP-WINDOW-SIZE-', size=(10, 1), enable_events=True)],
                        [sg.Button("◀", key='-HEATMAP-PREV-', size=(2, 1)),
                         sg.Text("Recent 1 Month", key='-HEATMAP-PERIOD-DISPLAY-', 
                                 font=('Arial', 10, 'bold'), size=(20, 1), justification='center'),
                         sg.Button("▶", key='-HEATMAP-NEXT-', size=(2, 1))],
                        [sg.Button("Latest", key='-HEATMAP-LATEST-', size=(8, 1)),
                         sg.Button("Most Active", key='-HEATMAP-MOST-ACTIVE-', size=(8, 1))]
                    ], element_justification='center', pad=(0, (5, 10)))],
                    [sg.Image(key='-SESSIONS-HEATMAP-', size=(650, 300))]
                ]),
                 sg.Tab('Status Timeline', [
                    [sg.Image(key='-STATUS-TIMELINE-', size=(650, 250))]
                ])]
            ], key='-SESSIONS-TABS-', expand_x=True)]
        ], font=('Helvetica', 11), expand_x=True)],
        
        [sg.VPush()]  # Add some vertical space
    ]
    
    # Wrap the content in a scrollable column with proper bottom padding
    tab3_layout = [
        [sg.Column(statistics_content, 
                   scrollable=True, 
                   vertical_scroll_only=True,
                   size=(None, 700),  # Fixed height of 700 pixels
                   expand_x=True,
                   pad=(10, (10, 40)))]  # Added bottom padding to the column itself
    ]

    # Main layout with tabs
    layout = [
        [sg.Menu([['File', ['Open', 'Save As', 'Import from Excel', 'Exit']], 
                  ['Options', ['Notes::notes_toggle', get_discord_menu_text()]], 
                  ['Help', ['User Guide', 'Feature Tour', '---', 'Data Format Info', 
                           'Troubleshooting', '---', 'Release Notes', 'Report Bug', '---', 'About']]], key='-MENU-')],
        [sg.TabGroup([
            [sg.Tab('Games List', tab1_layout, key='-TAB1-')],
            [sg.Tab('Summary', tab2_layout, key='-TAB2-')],
            [sg.Tab('Statistics', tab3_layout, key='-TAB3-')]
        ], key='-TABGROUP-', enable_events=True, expand_x=True, expand_y=True)]
    ]
    
    return layout 