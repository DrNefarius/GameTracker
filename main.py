"""
GamesList Manager - Main Entry Point

This application helps track your video game collection, 
including play time, completion status, ratings, and notes.
"""

import os
import PySimpleGUI as sg
from datetime import datetime, timedelta

# Import from our modules
from constants import _DEBUG, QT_ENTER_KEY1, QT_ENTER_KEY2, STAR_FILLED, STAR_EMPTY
from config import load_config, save_config
from data_management import load_from_gmd, save_data
from utilities import format_timedelta_with_seconds
from game_statistics import update_summary, count_total_completed, count_total_entries, calculate_total_time
from ui_components import create_main_layout, get_display_row_with_rating
from event_handlers import (
    handle_menu_events, handle_table_event, handle_game_action, 
    handle_session_table_click, handle_add_entry
)
from visualizations import update_summary_charts
from session_management import display_all_game_notes, get_game_sessions, migrate_all_game_sessions

def force_scrollable_refresh(window):
    """Force PySimpleGUI to recalculate scrollable areas by temporarily resizing the window"""
    try:
        # Get current window size
        current_size = window.size
        if current_size:
            # Temporarily resize window by 1 pixel to force recalculation
            window.size = (current_size[0] + 1, current_size[1])
            window.read(timeout=1)  # Give PySimpleGUI time to process the resize
            # Restore original size
            window.size = current_size
            window.read(timeout=1)  # Give PySimpleGUI time to process the resize back
    except Exception as e:
        print(f"Warning: Could not force scrollable refresh: {str(e)}")

def main():
    """Main entry point for the application"""
    # Load config to get settings
    config = load_config()
    last_file = config.get('last_file')
    default_dir = config.get('default_save_dir', os.path.expanduser('~'))

    # Set default filename if no last file
    if not last_file or not os.path.exists(last_file):
        if _DEBUG:
            fn = os.path.join(default_dir, 'games_debug.gmd')
        else:
            fn = os.path.join(default_dir, 'games.gmd')
    else:
        fn = last_file

    # Initialize data
    try:
        data_with_indices, needs_migration = load_from_gmd(fn)
        if not data_with_indices:  # If file exists but is empty
            print(f"Empty .gmd file found at {fn}. Starting with empty database.")
            needs_migration = False
    except FileNotFoundError:
        print(f"GMD file not found at {fn}. Starting with empty database.")
        # Create a new default file
        data_with_indices = []
        needs_migration = False
        # Create an empty .gmd file in the default location
        from data_management import save_to_gmd
        save_to_gmd(data_with_indices, fn)
        # Save this as the last used file
        config['last_file'] = fn
        save_config(config)
    except Exception as e:
        print(f"Unexpected error initializing data: {str(e)}")
        print("Starting with empty database.")
        data_with_indices = []
        needs_migration = False
        # Try to create a new .gmd file
        from data_management import save_to_gmd
        save_to_gmd(data_with_indices, fn)
        # Save this as the last used file
        config['last_file'] = fn
        save_config(config)
    
    # Migrate existing data to unified feedback format only if needed
    if data_with_indices and needs_migration:
        print("Migrating data to unified feedback format...")
        migrated_data = migrate_all_game_sessions(data_with_indices)
        data_with_indices = migrated_data
        # Save migrated data (this will set the unified feedback flag)
        save_data(data_with_indices, fn)
        print("Data migration completed and saved")
        
    # Sort the data (if any exists)
    if data_with_indices:
        data_with_indices = sorted(data_with_indices, key=lambda x: (x[1][1] == "-", x[1][1]))

    # Create the main window layout
    layout = create_main_layout(data_with_indices)
    
    # Create the Window with reasonable default size
    window = sg.Window(f'Games List Manager - {os.path.basename(fn)}', layout, 
                      resizable=True, return_keyboard_events=True, finalize=True, 
                      icon='gameslisticon.ico', size=(1300, 700))
    window['-TABGROUP-'].Widget.select(0)  # Ensure first tab is selected by default

    # Bind right-click event to the table
    window['-TABLE-'].bind('<Button-3>', 'Right')  # Bind right-click event

    # Track which tabs have been loaded
    tabs_loaded = {0: True, 1: False, 2: False}

    # State for sorting direction
    sort_directions = {i: True for i in range(8)}  # 8 columns

    # Variables for data management
    data_storage = None  # For storing complete dataset when filtering
    selected_game_for_stats = None
    
    # Heatmap navigation state
    main.heatmap_end_date = None  # Track current heatmap end date for navigation

    # Event loop
    while True:
        event, values = window.read()
        
        if event == sg.WIN_CLOSED or event == 'Exit':
            break
            
        # Handle menu events
        elif event in ['Notes::notes_toggle', 'Open', 'Save As', 'Import from Excel', 'User Guide', 
                      'Feature Tour', 'Data Format Info', 'Troubleshooting', 
                      'Release Notes', 'Report Bug', 'About']:
            result = handle_menu_events(event, window, data_with_indices, fn)
            if result:
                if result.get('action') == 'file_loaded':
                    data_with_indices = result['data']
                    fn = result['filename']
                    data_storage = None  # Reset data storage
                    from ui_components import update_table_display
                    update_table_display(data_with_indices, window)
                    update_summary(data_with_indices, window)
                    if values['-TABGROUP-'] == 'Summary':
                        update_summary_charts(data_with_indices)
                        # Update charts after loading data
                        charts = update_summary_charts(data_with_indices)
                        if charts:
                            window['-PIE-CHART-'].update(filename=charts['pie_chart'])
                            window['-YEAR-CHART-'].update(filename=charts['year_chart'])
                            window['-PLAYTIME-CHART-'].update(filename=charts['playtime_chart'])
                            window['-RATING-CHART-'].update(filename=charts['rating_chart'])
                            force_scrollable_refresh(window)
                    elif values['-TABGROUP-'] == 'Statistics':
                        update_statistics_tab(window, data_with_indices, selected_game=None, update_game_list=True)
                        force_scrollable_refresh(window)
                elif result.get('action') == 'file_saved':
                    fn = result['filename']
                elif result.get('action') == 'file_converted':
                    data_with_indices = result['data']
                    fn = result['filename']
                    data_storage = None  # Reset data storage
                    from ui_components import update_table_display
                    update_table_display(data_with_indices, window)
                    update_summary(data_with_indices, window)
                    if values['-TABGROUP-'] == 'Summary':
                        charts = update_summary_charts(data_with_indices)
                        if charts:
                            window['-PIE-CHART-'].update(filename=charts['pie_chart'])
                            window['-YEAR-CHART-'].update(filename=charts['year_chart'])
                            window['-PLAYTIME-CHART-'].update(filename=charts['playtime_chart'])
                            window['-RATING-CHART-'].update(filename=charts['rating_chart'])
                            force_scrollable_refresh(window)
                    elif values['-TABGROUP-'] == 'Statistics':
                        update_statistics_tab(window, data_with_indices, selected_game=None, update_game_list=True)
                        force_scrollable_refresh(window)
                        
        # Handle tab changes
        elif event == '-TABGROUP-':  # Tab changed
            current_tab = values['-TABGROUP-']
            if current_tab == 'Summary' and not tabs_loaded[1]:
                # First time loading the Summary tab - generate charts
                charts = update_summary_charts(data_with_indices)
                if charts:
                    window['-PIE-CHART-'].update(filename=charts['pie_chart'])
                    window['-YEAR-CHART-'].update(filename=charts['year_chart'])
                    window['-PLAYTIME-CHART-'].update(filename=charts['playtime_chart'])
                    window['-RATING-CHART-'].update(filename=charts['rating_chart'])
                    force_scrollable_refresh(window)
                tabs_loaded[1] = True
            elif current_tab == 'Statistics' and not tabs_loaded[2]:
                # First time loading the Statistics tab - update statistics
                from event_handlers import update_statistics_tab
                
                # Initialize year display to current year or latest data year
                from datetime import datetime
                current_year = datetime.now().year
                window['-CONTRIB-YEAR-DISPLAY-'].update(str(current_year))
                
                update_statistics_tab(window, data_with_indices, selected_game=None, update_game_list=True)
                force_scrollable_refresh(window)
                tabs_loaded[2] = True
                
        # Handle chart refresh
        elif event == '-REFRESH-CHARTS-':
            charts = update_summary_charts(data_with_indices)
            if charts:
                window['-PIE-CHART-'].update(filename=charts['pie_chart'])
                window['-YEAR-CHART-'].update(filename=charts['year_chart'])
                window['-PLAYTIME-CHART-'].update(filename=charts['playtime_chart'])
                window['-RATING-CHART-'].update(filename=charts['rating_chart'])
                force_scrollable_refresh(window)
                
        # Handle statistics refresh
        elif event == '-REFRESH-STATS-':
            from event_handlers import update_statistics_tab
            # Get currently selected game if any
            selected_game = None
            if values['-GAME-LIST-']:
                selected_game = values['-GAME-LIST-'][0]
            update_statistics_tab(window, data_with_indices, selected_game)
            
        # Handle contributions year navigation
        elif event == '-CONTRIB-YEAR-PREV-':
            try:
                current_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                new_year = current_year - 1
                window['-CONTRIB-YEAR-DISPLAY-'].update(str(new_year))
                
                # Refresh contributions map with new year
                from event_handlers import update_statistics_tab
                selected_game = None
                if values['-GAME-LIST-']:
                    selected_game = values['-GAME-LIST-'][0]
                update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False, contributions_year=new_year)
            except Exception as e:
                print(f"Error changing year: {str(e)}")
                
        elif event == '-CONTRIB-YEAR-NEXT-':
            try:
                current_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                new_year = current_year + 1
                window['-CONTRIB-YEAR-DISPLAY-'].update(str(new_year))
                
                # Refresh contributions map with new year
                from event_handlers import update_statistics_tab
                selected_game = None
                if values['-GAME-LIST-']:
                    selected_game = values['-GAME-LIST-'][0]
                update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False, contributions_year=new_year)
            except Exception as e:
                print(f"Error changing year: {str(e)}")

        # Handle heatmap window size change
        elif event == '-HEATMAP-WINDOW-SIZE-':
            try:
                from event_handlers import update_statistics_tab
                from datetime import datetime
                
                # Convert window size to months
                window_text = values['-HEATMAP-WINDOW-SIZE-']
                window_months = {'1 Month': 1, '3 Months': 3, '6 Months': 6, '1 Year': 12}.get(window_text, 6)
                
                # Get current selected game
                selected_game = None
                if values['-GAME-LIST-']:
                    selected_game = values['-GAME-LIST-'][0]
                
                # Get current contributions year
                contributions_year = None
                try:
                    contributions_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                except:
                    contributions_year = datetime.now().year
                
                # Update heatmap with new window size
                update_statistics_tab(window, data_with_indices, selected_game, 
                                    update_game_list=False, contributions_year=contributions_year,
                                    heatmap_window_months=window_months)
            except Exception as e:
                print(f"Error changing heatmap window size: {str(e)}")

        # Handle distribution chart type change
        elif event == '-DISTRIBUTION-CHART-TYPE-':
            try:
                from event_handlers import update_statistics_tab
                from datetime import datetime
                
                # Convert chart type text to parameter
                chart_type_text = values['-DISTRIBUTION-CHART-TYPE-']
                chart_type_map = {
                    'Line Chart': 'line',
                    'Scatter Plot': 'scatter', 
                    'Box Plot': 'box',
                    'Histogram': 'histogram'
                }
                chart_type = chart_type_map.get(chart_type_text, 'line')
                
                # Get current selected game
                selected_game = None
                if values['-GAME-LIST-']:
                    selected_game = values['-GAME-LIST-'][0]
                
                # Get current contributions year and heatmap settings
                contributions_year = None
                try:
                    contributions_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                except:
                    contributions_year = datetime.now().year
                
                window_text = values['-HEATMAP-WINDOW-SIZE-']
                window_months = {'1 Month': 1, '3 Months': 3, '6 Months': 6, '1 Year': 12}.get(window_text, 6)
                
                heatmap_end_date = getattr(main, 'heatmap_end_date', None)
                
                # Update statistics with new chart type
                update_statistics_tab(window, data_with_indices, selected_game, 
                                    update_game_list=False, contributions_year=contributions_year,
                                    heatmap_window_months=window_months, heatmap_end_date=heatmap_end_date,
                                    distribution_chart_type=chart_type)
            except Exception as e:
                print(f"Error changing distribution chart type: {str(e)}")
                import traceback
                traceback.print_exc()
                
        # Handle heatmap navigation
        elif event == '-HEATMAP-PREV-':
            try:
                from event_handlers import update_statistics_tab
                from session_management import extract_all_sessions
                from datetime import datetime, timedelta
                
                # Get current window size
                window_text = values['-HEATMAP-WINDOW-SIZE-']
                window_months = {'1 Month': 1, '3 Months': 3, '6 Months': 6, '1 Year': 12}.get(window_text, 6)
                
                # Get current end date from the display or use current date
                current_period = window['-HEATMAP-PERIOD-DISPLAY-'].get()
                
                # Calculate new end date (move back by window size)
                if hasattr(main, 'heatmap_end_date') and main.heatmap_end_date:
                    new_end_date = main.heatmap_end_date - timedelta(days=window_months * 30)
                else:
                    # First time navigating, start from current date
                    new_end_date = datetime.now().date() - timedelta(days=window_months * 30)
                
                # Store the new end date
                main.heatmap_end_date = new_end_date
                
                # Get current selected game and contributions year
                selected_game = None
                if values['-GAME-LIST-']:
                    selected_game = values['-GAME-LIST-'][0]
                
                contributions_year = None
                try:
                    contributions_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                except:
                    contributions_year = datetime.now().year
                
                # Update heatmap with new date range
                update_statistics_tab(window, data_with_indices, selected_game,
                                    update_game_list=False, contributions_year=contributions_year,
                                    heatmap_window_months=window_months, heatmap_end_date=new_end_date)
            except Exception as e:
                print(f"Error navigating heatmap backwards: {str(e)}")
                
        elif event == '-HEATMAP-NEXT-':
            try:
                from event_handlers import update_statistics_tab
                from datetime import datetime, timedelta
                
                # Get current window size
                window_text = values['-HEATMAP-WINDOW-SIZE-']
                window_months = {'1 Month': 1, '3 Months': 3, '6 Months': 6, '1 Year': 12}.get(window_text, 6)
                
                # Calculate new end date (move forward by window size)
                if hasattr(main, 'heatmap_end_date') and main.heatmap_end_date:
                    new_end_date = main.heatmap_end_date + timedelta(days=window_months * 30)
                else:
                    # First time navigating, start from current date
                    new_end_date = datetime.now().date()
                
                # Don't go beyond current date
                if new_end_date > datetime.now().date():
                    new_end_date = datetime.now().date()
                
                # Store the new end date
                main.heatmap_end_date = new_end_date
                
                # Get current selected game and contributions year
                selected_game = None
                if values['-GAME-LIST-']:
                    selected_game = values['-GAME-LIST-'][0]
                
                contributions_year = None
                try:
                    contributions_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                except:
                    contributions_year = datetime.now().year
                
                # Update heatmap with new date range
                update_statistics_tab(window, data_with_indices, selected_game,
                                    update_game_list=False, contributions_year=contributions_year,
                                    heatmap_window_months=window_months, heatmap_end_date=new_end_date)
            except Exception as e:
                print(f"Error navigating heatmap forwards: {str(e)}")
                
        elif event == '-HEATMAP-LATEST-':
            try:
                from event_handlers import update_statistics_tab
                from datetime import datetime
                
                # Reset to latest data (current date)
                main.heatmap_end_date = None  # Reset to use latest data
                
                # Get current window size
                window_text = values['-HEATMAP-WINDOW-SIZE-']
                window_months = {'1 Month': 1, '3 Months': 3, '6 Months': 6, '1 Year': 12}.get(window_text, 6)
                
                # Get current selected game and contributions year
                selected_game = None
                if values['-GAME-LIST-']:
                    selected_game = values['-GAME-LIST-'][0]
                
                contributions_year = None
                try:
                    contributions_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                except:
                    contributions_year = datetime.now().year
                
                # Update heatmap to latest period
                update_statistics_tab(window, data_with_indices, selected_game,
                                    update_game_list=False, contributions_year=contributions_year,
                                    heatmap_window_months=window_months, heatmap_end_date=None)
            except Exception as e:
                print(f"Error jumping to latest heatmap period: {str(e)}")
                
        elif event == '-HEATMAP-MOST-ACTIVE-':
            try:
                from event_handlers import update_statistics_tab
                from session_management import extract_all_sessions, get_game_sessions, find_most_active_period
                from datetime import datetime
                
                # Get current window size
                window_text = values['-HEATMAP-WINDOW-SIZE-']
                window_months = {'1 Month': 1, '3 Months': 3, '6 Months': 6, '1 Year': 12}.get(window_text, 6)
                
                # Get sessions to analyze
                selected_game = None
                if values['-GAME-LIST-']:
                    selected_game = values['-GAME-LIST-'][0]
                    sessions = get_game_sessions(data_with_indices, selected_game)
                else:
                    sessions = extract_all_sessions(data_with_indices)
                
                # Find most active period
                most_active_end_date = find_most_active_period(sessions, window_months)
                main.heatmap_end_date = most_active_end_date
                
                # Get current contributions year
                contributions_year = None
                try:
                    contributions_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                except:
                    contributions_year = datetime.now().year
                
                # Update heatmap to most active period
                update_statistics_tab(window, data_with_indices, selected_game,
                                    update_game_list=False, contributions_year=contributions_year,
                                    heatmap_window_months=window_months, heatmap_end_date=most_active_end_date)
            except Exception as e:
                print(f"Error jumping to most active heatmap period: {str(e)}")

        # Handle game list selection in Statistics tab
        elif event == '-GAME-LIST-':
            try:
                if values['-GAME-LIST-'] and len(values['-GAME-LIST-']) > 0:
                    selected_game_for_stats = values['-GAME-LIST-'][0]
                    
                    # Get current chart type selection
                    chart_type_text = values.get('-DISTRIBUTION-CHART-TYPE-', 'Line Chart')
                    chart_type_map = {
                        'Line Chart': 'line',
                        'Scatter Plot': 'scatter', 
                        'Box Plot': 'box',
                        'Histogram': 'histogram'
                    }
                    chart_type = chart_type_map.get(chart_type_text, 'line')
                    
                    # Get other current settings
                    from datetime import datetime
                    contributions_year = None
                    try:
                        contributions_year = int(window['-CONTRIB-YEAR-DISPLAY-'].get())
                    except:
                        contributions_year = datetime.now().year
                    
                    window_text = values.get('-HEATMAP-WINDOW-SIZE-', '6 Months')
                    window_months = {'1 Month': 1, '3 Months': 3, '6 Months': 6, '1 Year': 12}.get(window_text, 6)
                    
                    heatmap_end_date = getattr(main, 'heatmap_end_date', None)
                    
                    update_statistics_tab(window, data_with_indices, selected_game_for_stats, 
                                        update_game_list=False, contributions_year=contributions_year,
                                        heatmap_window_months=window_months, heatmap_end_date=heatmap_end_date,
                                        distribution_chart_type=chart_type)
                    force_scrollable_refresh(window)
            except Exception as e:
                print(f"Error handling game selection: {str(e)}")
                sg.popup_error(f"Error selecting game: {str(e)}", title="Error")
                
        # Handle show all games button
        elif event == '-SHOW-ALL-GAMES-':
            from event_handlers import update_statistics_tab
            
            # Get current chart type selection
            chart_type_text = values.get('-DISTRIBUTION-CHART-TYPE-', 'Line Chart')
            chart_type_map = {
                'Line Chart': 'line',
                'Scatter Plot': 'scatter', 
                'Box Plot': 'box',
                'Histogram': 'histogram'
            }
            chart_type = chart_type_map.get(chart_type_text, 'line')
            
            selected_game_for_stats = None
            window['-GAME-LIST-'].update(set_to_index=[])  # Clear selection in listbox
            update_statistics_tab(window, data_with_indices, selected_game=None, update_game_list=True,
                                distribution_chart_type=chart_type)
            force_scrollable_refresh(window)
            
        # Handle session search
        elif event == '-SESSION-SEARCH-':
            search_query = values['-SESSION-SEARCH-'].lower()
            game_names = []
            for idx, game_data in data_with_indices:
                game_name = game_data[0]
                has_sessions = len(game_data) > 7 and game_data[7] and len(game_data[7]) > 0
                has_status_history = len(game_data) > 8 and game_data[8] and len(game_data[8]) > 0
                if (has_sessions or has_status_history) and (not search_query or search_query in game_name.lower()):
                    game_names.append(game_name)
            window['-GAME-LIST-'].update(values=sorted(game_names))
            
        elif event == '-SESSION-SEARCH-BTN-':
            search_query = values['-SESSION-SEARCH-'].lower()
            game_names = []
            for idx, game_data in data_with_indices:
                game_name = game_data[0]
                has_sessions = len(game_data) > 7 and game_data[7] and len(game_data[7]) > 0
                has_status_history = len(game_data) > 8 and game_data[8] and len(game_data[8]) > 0
                if (has_sessions or has_status_history) and (not search_query or search_query in game_name.lower()):
                    game_names.append(game_name)
            window['-GAME-LIST-'].update(values=sorted(game_names))
            
        # Handle search and reset
        elif event == 'Search' or event in ['\r', QT_ENTER_KEY1, QT_ENTER_KEY2]:
            query = values['-SEARCH-'].lower()
            if data_storage is None:  # save the whole dataset once before filtering
                data_storage = data_with_indices.copy()
            data_with_indices = [row for row in data_with_indices if any(query in str(cell).lower() for cell in row[1])]
            from ui_components import update_table_display
            update_table_display(data_with_indices, window)
            update_summary(data_with_indices, window)
            
        elif event == 'Reset':
            if data_storage is not None:
                # Restore original data but reset indices
                data_with_indices = data_storage.copy()
                # Reset indices to avoid duplication
                data_with_indices = [(i, row[1]) for i, row in enumerate(data_with_indices)]
                data_storage = None
            from ui_components import update_table_display
            update_table_display(data_with_indices, window)
            window['-SEARCH-'].update('')
            update_summary(data_with_indices, window)
            
        # Handle save
        elif event == 'Save':
            save_data(data_with_indices, fn, data_storage)
            from event_handlers import update_window_title
            update_window_title(window, fn)
            sg.popup(f'Data manually saved to {fn}!\n\nNote: Most operations now auto-save. Manual save is mainly needed for search/filter changes or as backup.', title='Manual Save Confirmation')
            
        # Handle add entry
        elif event == 'Add Entry':
            result = handle_add_entry(data_with_indices, window, fn, data_storage)
            if result and result.get('action') == 'entry_added':
                data_with_indices = result['data']
                from ui_components import update_table_display
                update_table_display(data_with_indices, window)
                update_summary(data_with_indices, window)
                
        # Handle table events
        elif isinstance(event, tuple) and event[0] == '-TABLE-':
            result = handle_table_event(event, data_with_indices, window, sort_directions, fn, data_storage)
            if result:
                if isinstance(result, list):  # Sorted data returned
                    data_with_indices = result
                elif isinstance(result, dict) and result.get('action') == 'show_actions':
                    action_result = handle_game_action(
                        result['row_index'], data_with_indices, window, 
                        data_storage, fn
                    )
                    if action_result:
                        if action_result.get('action') in ['game_edited', 'game_deleted', 'game_rated', 'time_tracked']:
                            data_with_indices = action_result['data']
                            from ui_components import update_table_display
                            update_table_display(data_with_indices, window)
                            update_summary(data_with_indices, window)
                            # Update charts if on summary tab
                            if values['-TABGROUP-'] == 'Summary':
                                charts = update_summary_charts(data_with_indices)
                                if charts:
                                    window['-PIE-CHART-'].update(filename=charts['pie_chart'])
                                    window['-YEAR-CHART-'].update(filename=charts['year_chart'])
                                    window['-PLAYTIME-CHART-'].update(filename=charts['playtime_chart'])
                                    window['-RATING-CHART-'].update(filename=charts['rating_chart'])
                                    force_scrollable_refresh(window)
                                    
        # Handle right-click on table
        elif event == '-TABLE-Right':
            if window['-TABLE-'].SelectedRows and len(window['-TABLE-'].SelectedRows) > 0:
                row_index = window['-TABLE-'].SelectedRows[0]
                action_result = handle_game_action(
                    row_index, data_with_indices, window, 
                    data_storage, fn
                )
                if action_result:
                    if action_result.get('action') in ['game_edited', 'game_deleted', 'game_rated', 'time_tracked']:
                        data_with_indices = action_result['data']
                        from ui_components import update_table_display
                        update_table_display(data_with_indices, window)
                        update_summary(data_with_indices, window)
                        
        # Handle session table clicks
        elif event == '-SESSIONS-TABLE-' and values['-SESSIONS-TABLE-']:
            result = handle_session_table_click(
                values, selected_game_for_stats, data_with_indices, window, fn, data_storage
            )
            # The statistics tab will be updated within the handler if needed
            
        # Handle view all notes button
        elif event == '-VIEW-ALL-NOTES-':
            try:
                from session_management import get_game_sessions
                if selected_game_for_stats:
                    game_sessions = get_game_sessions(data_with_indices, selected_game_for_stats)
                    display_all_game_notes(selected_game_for_stats, game_sessions, data_with_indices)
                else:
                    sg.popup("Please select a game first", title="No Game Selected", icon='gameslisticon.ico')
            except Exception as e:
                print(f"Error displaying all notes: {str(e)}")
                sg.popup_error(f"Error displaying notes: {str(e)}", title="Error")

    window.close()

if __name__ == "__main__":
    main() 