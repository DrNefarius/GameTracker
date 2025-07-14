"""
Event handling for the GamesList application.
Handles all user interactions, menu events, and UI event processing.
"""

import os
import re
import io
import time
import tempfile
import traceback
import PySimpleGUI as sg
import matplotlib.pyplot as plt
from datetime import datetime, timedelta

from constants import QT_ENTER_KEY1, QT_ENTER_KEY2, STAR_FILLED, STAR_EMPTY, VERSION
from config import load_config, save_config
from data_management import load_from_gmd, save_to_gmd, convert_excel_to_gmd, save_data
from ui_components import (
    create_entry_popup, validate_entry_form, show_game_actions_dialog,
    update_table_display, get_display_row_with_rating
)
from session_management import (
    show_popup, extract_all_sessions, calculate_session_statistics,
    get_game_sessions, format_session_for_display, get_status_history,
    format_status_history_for_display, display_all_game_notes, show_session_feedback_popup,
    migrate_all_game_sessions, create_github_contributions_canvas, setup_contributions_tooltip_callback
)
from visualizations import update_summary_charts
from game_statistics import update_summary
from utilities import safe_sort_by_date, safe_sort_by_time, calculate_popup_center_location
from ratings import show_rating_popup, get_session_rating_summary, format_rating
from help_dialogs import show_user_guide, show_data_format_info, show_troubleshooting_guide, show_feature_tour, show_release_notes, show_bug_report_info, show_about_dialog
from discord_integration import get_discord_integration

def record_status_change(game_data, old_status, new_status):
    """Record a status change with timestamp"""
    if old_status == new_status:
        return  # No change to record
        
    # Ensure status_history exists
    if len(game_data) <= 8 or game_data[8] is None:
        game_data.append([])
    
    # Record the status change
    status_change = {
        'from': old_status,
        'to': new_status,
        'timestamp': datetime.now().isoformat()
    }
    
    game_data[8].append(status_change)
    return status_change

def update_statistics_tab(window, data, selected_game=None, update_game_list=True, contributions_year=None, 
                          heatmap_window_months=1, heatmap_end_date=None, distribution_chart_type='line', full_dataset=None):
    """Update all elements in the Statistics tab"""
    # Extract all sessions
    all_sessions = extract_all_sessions(data)
    
    # Calculate overall statistics
    stats = calculate_session_statistics(all_sessions)
    
    # Update overall statistics display
    window['-TOTAL-SESSIONS-'].update(f"Total Sessions: {stats['total_count']}")
    from utilities import format_timedelta_with_seconds
    window['-TOTAL-SESSION-TIME-'].update(f"Total Session Time: {format_timedelta_with_seconds(stats['total_time'])}")
    window['-AVG-SESSION-'].update(f"Average Session Length: {format_timedelta_with_seconds(stats['avg_length'])}")
    
    most_active = stats['most_active_day']
    if most_active['day']:
        window['-MOST-ACTIVE-DAY-'].update(f"Most Active Day: {most_active['day'].strftime('%Y-%m-%d')} ({most_active['count']} sessions)")
    else:
        window['-MOST-ACTIVE-DAY-'].update("Most Active Day: None")
    
    # Update heatmap period display
    if heatmap_end_date:
        start_date = heatmap_end_date - timedelta(days=heatmap_window_months * 30)
        period_text = f"{start_date.strftime('%b %Y')} - {heatmap_end_date.strftime('%b %Y')}"
    else:
        # Default display for most recent period
        window_names = {1: '1 Month', 3: '3 Months', 6: '6 Months', 12: '1 Year'}
        period_text = f"Recent {window_names.get(heatmap_window_months, f'{heatmap_window_months} Months')}"
    
    window['-HEATMAP-PERIOD-DISPLAY-'].update(period_text)
    
    # Only update game list when explicitly requested (not during selection)
    if update_game_list:
        # Use full dataset for game list population to show all games even when filtering is active
        game_list_data = full_dataset if full_dataset is not None else data
        
        # Get unique game names for the game list - include games with sessions, status history, OR game-level ratings
        game_names = []
        for idx, game_data in game_list_data:
            game_name = game_data[0]
            has_sessions = len(game_data) > 7 and game_data[7] and len(game_data[7]) > 0
            has_status_history = len(game_data) > 8 and game_data[8] and len(game_data[8]) > 0
            has_game_rating = len(game_data) > 9 and game_data[9] and isinstance(game_data[9], dict)
            
            # Include the game if it has sessions, status history, OR a game-level rating
            if has_sessions or has_status_history or has_game_rating:
                game_names.append(game_name)
        
        # Update game list
        window['-GAME-LIST-'].update(values=sorted(game_names))
    
    # If a game is selected, update its specific statistics
    if selected_game:
        # Update Discord presence for viewing stats
        discord = get_discord_integration()
        discord.update_presence_viewing_stats(selected_game)
        
        # Get sessions for the selected game
        game_sessions = get_game_sessions(data, selected_game)
        
        # Get status history for the selected game
        status_history = get_status_history(data, selected_game)
        
        # Calculate game-specific stats
        game_session_count = len(game_sessions)
        game_session_time = timedelta()
        
        for session in game_sessions:
            try:
                duration = session.get('duration', '00:00:00')
                if isinstance(duration, str):
                    parts = duration.split(':')
                    if len(parts) == 3:
                        h, m, s = map(int, parts)
                        game_session_time += timedelta(hours=h, minutes=m, seconds=s)
            except:
                continue
        
        # Update game-specific display
        window['-SELECTED-GAME-'].update(f"Sessions for: {selected_game}")
        window['-GAME-SESSIONS-'].update(f"Sessions: {game_session_count}")
        window['-GAME-SESSION-TIME-'].update(f"Total Time: {format_timedelta_with_seconds(game_session_time)}")
        
        # Update rating comparison widget
        # Get auto-calculated rating from sessions
        session_rating_summary = get_session_rating_summary(game_sessions)
        
        # Get manual game rating
        manual_rating = None
        for idx, game_data in data:
            if game_data[0] == selected_game:
                if len(game_data) > 9 and game_data[9] and isinstance(game_data[9], dict):
                    manual_rating = game_data[9]
                break
        
        # Update rating comparison display
        if session_rating_summary or manual_rating:
            window['-RATING-COMPARISON-'].update(visible=True)
            
            # Update auto-calculated rating side
            if session_rating_summary:
                auto_stars = session_rating_summary['average_stars']
                auto_rating_display = STAR_FILLED * auto_stars + STAR_EMPTY * (5 - auto_stars)
                window['-AUTO-RATING-STARS-'].update(auto_rating_display)
                window['-AUTO-RATING-INFO-'].update(f"Avg: {session_rating_summary['exact_average']:.1f} ({session_rating_summary['total_rated_sessions']} sessions)")
                
                # Format common tags as comma-separated list
                if session_rating_summary['most_common_tags']:
                    tags_text = ', '.join(session_rating_summary['most_common_tags'][:5])
                else:
                    tags_text = "No tags found"
                window['-AUTO-RATING-TAGS-'].update(tags_text)
            else:
                window['-AUTO-RATING-STARS-'].update("No session ratings")
                window['-AUTO-RATING-INFO-'].update("")
                window['-AUTO-RATING-TAGS-'].update("N/A")
            
            # Update manual rating side
            if manual_rating:
                manual_stars = manual_rating.get('stars', 0)
                manual_rating_display = STAR_FILLED * manual_stars + STAR_EMPTY * (5 - manual_stars)
                window['-MANUAL-RATING-STARS-'].update(manual_rating_display)
                
                rating_type = "Auto-calculated" if manual_rating.get('auto_calculated', False) else "Manual"
                window['-MANUAL-RATING-INFO-'].update(rating_type)
                
                # Format manual rating tags separately from comment
                manual_tags = manual_rating.get('tags', [])
                manual_comment = manual_rating.get('comment', '').strip()
                
                # Update tags (centered, comma-separated)
                if manual_tags:
                    tags_text = ', '.join(manual_tags[:5])
                else:
                    tags_text = "No tags"
                window['-MANUAL-RATING-TAGS-'].update(tags_text)
                
                # Update comment in separate area
                if manual_comment:
                    # Show full comment text without truncation
                    window['-MANUAL-RATING-COMMENT-'].update(manual_comment)
                else:
                    window['-MANUAL-RATING-COMMENT-'].update("No comment")
            else:
                window['-MANUAL-RATING-STARS-'].update("No manual rating")
                window['-MANUAL-RATING-INFO-'].update("")
                window['-MANUAL-RATING-TAGS-'].update("N/A")
                window['-MANUAL-RATING-COMMENT-'].update("N/A")
        else:
            window['-RATING-COMPARISON-'].update(visible=False)
        
        # Update sessions table
        display_data = format_session_for_display(game_sessions)
        
        # Format the details column to ensure it fits well
        if display_data:
            for row in display_data:
                # Don't truncate the text so aggressively - use more of the available width
                if len(row) > 2 and len(row[2]) > 120:
                    row[2] = row[2][:117] + '...'
        
        # Set colors for rows with notes/ratings
        from utilities import get_session_row_colors
        row_colors = get_session_row_colors(display_data)
        window['-SESSIONS-TABLE-'].update(values=display_data, row_colors=row_colors)
        
        # Update status history table
        status_display_data = format_status_history_for_display(status_history)
        window['-STATUS-HISTORY-TABLE-'].update(values=status_display_data)
        
        # Update visualizations for the selected game
        from session_management import (
            create_session_timeline_chart, create_session_distribution_chart,
            create_session_heatmap, create_status_timeline_chart,
            create_github_contributions_canvas
        )
        
        # Create GitHub-style contributions canvas
        try:
            contributions_data = create_github_contributions_canvas(game_sessions, selected_game, year=contributions_year)
            if contributions_data and 'draw_function' in contributions_data:
                # Set up tooltip callback
                tooltip_callback = setup_contributions_tooltip_callback(window)
                window['-CONTRIBUTIONS-CANVAS-']._tooltip_callback = tooltip_callback
                
                # Draw the heatmap on the fixed canvas
                contributions_data['draw_function'](window['-CONTRIBUTIONS-CANVAS-'])
        except Exception as e:
            print(f"Error creating contributions canvas: {str(e)}")
            traceback.print_exc()
            # Draw error message on canvas
            try:
                canvas = window['-CONTRIBUTIONS-CANVAS-'].Widget
                canvas.delete("all")
                canvas.create_text(400, 150, text="Error loading contributions map", 
                                 font=('Arial', 12, 'bold'), fill='red')
            except:
                pass
        
        # Create other charts
        timeline_data = create_session_timeline_chart(game_sessions, selected_game)
        distribution_data = create_session_distribution_chart(game_sessions, selected_game, distribution_chart_type)
        heatmap_data = create_session_heatmap(game_sessions, selected_game, heatmap_window_months, heatmap_end_date)
        status_timeline_data = create_status_timeline_chart(status_history, selected_game)
        
        # Use temporary files for other charts with unique names to force refresh
        import tempfile
        import time
        temp_dir = tempfile.gettempdir()
        timestamp = str(int(time.time() * 1000))  # Millisecond timestamp for uniqueness
        
        timeline_file = os.path.join(temp_dir, f'timeline_temp_{timestamp}.png')
        distribution_file = os.path.join(temp_dir, f'distribution_temp_{timestamp}.png')
        heatmap_file = os.path.join(temp_dir, f'heatmap_temp_{timestamp}.png')
        status_timeline_file = os.path.join(temp_dir, f'status_timeline_temp_{timestamp}.png')
        
        with open(timeline_file, 'wb') as f:
            f.write(timeline_data.getvalue())
        
        with open(distribution_file, 'wb') as f:
            f.write(distribution_data.getvalue())
            
        with open(heatmap_file, 'wb') as f:
            f.write(heatmap_data.getvalue())
            
        with open(status_timeline_file, 'wb') as f:
            f.write(status_timeline_data.getvalue())
        
        print(f"Updated distribution chart file: {distribution_file}")
        
        window['-SESSIONS-TIMELINE-'].update(filename=timeline_file)
        window['-SESSIONS-DISTRIBUTION-'].update(filename=distribution_file)
        window['-SESSIONS-HEATMAP-'].update(filename=heatmap_file)
        window['-STATUS-TIMELINE-'].update(filename=status_timeline_file)
    else:
        # Show overall visualizations when no game is selected
        window['-SELECTED-GAME-'].update("No game selected")
        window['-GAME-SESSIONS-'].update("Sessions: 0")
        window['-GAME-SESSION-TIME-'].update("Total Time: 00:00:00")
        
        # Hide rating comparison when no game is selected
        window['-RATING-COMPARISON-'].update(visible=False)
        
        # Explicitly clear row colors by passing an empty list
        window['-SESSIONS-TABLE-'].update(values=[], row_colors=[])
        window['-STATUS-HISTORY-TABLE-'].update(values=[])
        
        # Create overall visualizations
        from session_management import (
            create_session_timeline_chart, create_session_distribution_chart, 
            create_session_heatmap, create_github_contributions_canvas
        )
        
        # Create overall GitHub-style contributions canvas for all sessions
        try:
            contributions_data = create_github_contributions_canvas(all_sessions, year=contributions_year)
            if contributions_data and 'draw_function' in contributions_data:
                # Set up tooltip callback
                tooltip_callback = setup_contributions_tooltip_callback(window)
                window['-CONTRIBUTIONS-CANVAS-']._tooltip_callback = tooltip_callback
                
                # Draw the heatmap on the fixed canvas
                contributions_data['draw_function'](window['-CONTRIBUTIONS-CANVAS-'])
        except Exception as e:
            print(f"Error creating overall contributions canvas: {str(e)}")
            traceback.print_exc()
            # Draw error message on canvas
            try:
                canvas = window['-CONTRIBUTIONS-CANVAS-'].Widget
                canvas.delete("all")
                canvas.create_text(400, 150, text="Error loading contributions map", 
                                 font=('Arial', 12, 'bold'), fill='red')
            except:
                pass
        
        # Create other charts
        timeline_data = create_session_timeline_chart(all_sessions)
        distribution_data = create_session_distribution_chart(all_sessions, None, distribution_chart_type)
        heatmap_data = create_session_heatmap(all_sessions, None, heatmap_window_months, heatmap_end_date)
        
        # For status timeline in overview mode, show placeholder
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.text(0.5, 0.5, "Select a specific game to view status timeline", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Status Change Timeline", fontsize=12)
        
        # Save to a buffer
        status_timeline_buf = io.BytesIO()
        fig.savefig(status_timeline_buf, format='png')
        status_timeline_buf.seek(0)
        plt.close(fig)
        
        # Use temporary files for other charts with unique names to force refresh
        import tempfile
        import time
        temp_dir = tempfile.gettempdir()
        timestamp = str(int(time.time() * 1000))  # Millisecond timestamp for uniqueness
        
        timeline_file = os.path.join(temp_dir, f'timeline_temp_{timestamp}.png')
        distribution_file = os.path.join(temp_dir, f'distribution_temp_{timestamp}.png')
        heatmap_file = os.path.join(temp_dir, f'heatmap_temp_{timestamp}.png')
        status_timeline_file = os.path.join(temp_dir, f'status_timeline_temp_{timestamp}.png')
        
        with open(timeline_file, 'wb') as f:
            f.write(timeline_data.getvalue())
        
        with open(distribution_file, 'wb') as f:
            f.write(distribution_data.getvalue())
            
        with open(heatmap_file, 'wb') as f:
            f.write(heatmap_data.getvalue())
            
        with open(status_timeline_file, 'wb') as f:
            f.write(status_timeline_buf.getvalue())
        
        window['-SESSIONS-TIMELINE-'].update(filename=timeline_file)
        window['-SESSIONS-DISTRIBUTION-'].update(filename=distribution_file)
        window['-SESSIONS-HEATMAP-'].update(filename=heatmap_file)
        window['-STATUS-TIMELINE-'].update(filename=status_timeline_file)

def update_window_title(window, file_path):
    """Update the window title to display the current file name"""
    window.set_title(f'Games List Manager - {os.path.basename(file_path)}')

# Global variables for double-click detection
_last_click_time = 0
_last_click_row = None
_double_click_threshold = 0.5  # seconds

def handle_table_event(event, data_with_indices, window, sort_directions, fn=None, data_storage=None):
    """Handle table click events including sorting and row selection"""
    import time
    global _last_click_time, _last_click_row
    
    try:
        # Extract the table event data
        event_data = event[2] if len(event) > 2 else None
        
        # Safety check for event_data
        if event_data is not None:
            # HEADER CLICK: event_data[0] == -1
            if isinstance(event_data, tuple) and len(event_data) > 0 and event_data[0] == -1:
                if len(event_data) > 1:
                    col_num = event_data[1]
                    if col_num != -1:
                        # Sort by column
                        current_direction = sort_directions[col_num]
                        if col_num == 1:  # Release date column
                            data_with_indices = safe_sort_by_date(data_with_indices, col_num, reverse=not current_direction)
                        elif col_num == 3:  # Time column
                            data_with_indices = safe_sort_by_time(data_with_indices, col_num, reverse=not current_direction)
                        else:
                            data_with_indices = sorted(data_with_indices, key=lambda x: (x[1][col_num] is not None, x[1][col_num]),
                                                     reverse=not current_direction)
                        sort_directions[col_num] = not current_direction
                        
                        # Update both table values and row colors after sorting
                        from ui_components import update_table_display
                        update_table_display(data_with_indices, window)
                        return data_with_indices
            
            # ROW CLICK: We have a row click of some kind
            elif isinstance(event_data, tuple) and len(event_data) > 0:
                row_index = event_data[0]
                if row_index is not None and row_index < len(data_with_indices):
                    # Check for column click
                    if len(event_data) > 1:
                        col_clicked = event_data[1]
                        
                        # STATUS COLUMN CLICK: col_clicked == 4
                        if col_clicked == 4:  # Status column
                            return handle_status_change(row_index, data_with_indices, window, data_storage, fn)
                        else:
                            # DOUBLE-CLICK DETECTION for other columns
                            current_time = time.time()
                            
                            # Check if this is a double-click (same row, within threshold time)
                            if (_last_click_row == row_index and 
                                current_time - _last_click_time <= _double_click_threshold):
                                # Double-click detected - show actions dialog
                                _last_click_time = 0  # Reset to prevent triple-click
                                _last_click_row = None
                                return {'action': 'show_actions', 'row_index': row_index}
                            else:
                                # Single-click - just record the click for potential double-click
                                _last_click_time = current_time
                                _last_click_row = row_index
                                # Single-click just selects the row (no action)
                                return None
    
    except Exception as e:
        print(f"Error handling table event: {str(e)}")
    
    return None

def handle_menu_events(event, window, data_with_indices, fn):
    """Handle menu events like Open, Save As, Import, etc."""
    if event.startswith('Discord:') and event.endswith('::discord_toggle'):
        # Toggle Discord Rich Presence integration
        config = load_config()
        current_enabled = config.get('discord_enabled', True)
        new_enabled = not current_enabled
        
        # Update config
        config['discord_enabled'] = new_enabled
        save_config(config)
        
        # Update Discord integration
        discord = get_discord_integration()
        if discord:
            if new_enabled:
                discord.enable_discord()
                status_msg = "Discord Rich Presence has been enabled!"
            else:
                discord.disable_discord()
                status_msg = "Discord Rich Presence has been disabled."
        else:
            status_msg = "Discord integration is not available."
        
        discord_toggle_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
        sg.popup(status_msg + "\n\nNote: The menu will show the updated status on next restart.", 
                title="Discord Integration", icon='gameslisticon.ico', location=discord_toggle_location)
        return None
        
    elif event == 'Open':
        # Open file dialog to select .gmd file
        open_file_location = calculate_popup_center_location(window, popup_width=500, popup_height=300)
        file_path = sg.popup_get_file('Select .gmd file to open', file_types=(("GMD Files", "*.gmd"),), initial_folder=os.path.dirname(fn), location=open_file_location)
        if file_path and os.path.exists(file_path):
            try:
                # Load data from selected file
                loaded_data, needs_migration = load_from_gmd(file_path)
                if loaded_data:
                    # Migrate if necessary
                    if needs_migration:
                        print("Migrating loaded data to unified feedback format...")
                        from session_management import migrate_all_game_sessions
                        loaded_data = migrate_all_game_sessions(loaded_data)
                        # Save migrated data
                        save_data(loaded_data, file_path)
                        print("Loaded data migration completed")
                    
                    # Update config with the new file path
                    config = load_config()
                    config['last_file'] = file_path
                    save_config(config)
                    # Update window title
                    update_window_title(window, file_path)
                    success_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                    sg.popup(f"Successfully loaded {len(loaded_data)} games from {file_path}", location=success_location)
                    return {'action': 'file_loaded', 'data': loaded_data, 'filename': file_path}
            except Exception as e:
                error_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                sg.popup_error(f"Error loading file: {str(e)}", location=error_location)
                
    elif event == 'Save As':
        # Open file dialog to select destination .gmd file
        save_file_location = calculate_popup_center_location(window, popup_width=500, popup_height=300)
        file_path = sg.popup_get_file('Save as .gmd file', file_types=(("GMD Files", "*.gmd"),), 
                                     save_as=True, default_extension=".gmd", 
                                     initial_folder=os.path.dirname(fn), location=save_file_location)
        if file_path:
            try:
                # Save data to the selected file
                if save_data(data_with_indices, file_path):
                    # Update config with the new file path
                    config = load_config()
                    config['last_file'] = file_path
                    save_config(config)
                    # Update window title
                    update_window_title(window, file_path)
                    save_success_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                    sg.popup(f"Successfully saved {len(data_with_indices)} games to {file_path}", location=save_success_location)
                    return {'action': 'file_saved', 'filename': file_path}
            except Exception as e:
                save_error_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                sg.popup_error(f"Error saving file: {str(e)}", location=save_error_location)
                
    elif event == 'Import from Excel':
        # Open file dialog to select Excel file
        excel_file_location = calculate_popup_center_location(window, popup_width=500, popup_height=300)
        excel_path = sg.popup_get_file('Select Excel file to import', file_types=(("Excel Files", "*.xlsx"),), initial_folder=os.path.dirname(fn), location=excel_file_location)
        if excel_path and os.path.exists(excel_path):
            # Ask for destination .gmd file
            gmd_save_location = calculate_popup_center_location(window, popup_width=500, popup_height=300)
            gmd_path = sg.popup_get_file('Save as .gmd file', file_types=(("GMD Files", "*.gmd"),), 
                                        save_as=True, default_extension=".gmd", 
                                        initial_folder=os.path.dirname(fn), location=gmd_save_location)
            if gmd_path:
                try:
                    # Convert Excel to GMD
                    converted_data = convert_excel_to_gmd(excel_path, gmd_path)
                    if converted_data:
                        # Update config with the new file path
                        config = load_config()
                        config['last_file'] = gmd_path
                        save_config(config)
                        # Update window title
                        update_window_title(window, gmd_path)
                        convert_success_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                        sg.popup(f"Successfully converted Excel file to {gmd_path}", location=convert_success_location)
                        return {'action': 'file_converted', 'data': converted_data, 'filename': gmd_path}
                    else:
                        convert_error_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                        sg.popup_error("Failed to convert Excel file to GMD format.", location=convert_error_location)
                except Exception as e:
                    convert_exception_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                    sg.popup_error(f"Error converting Excel file: {str(e)}", location=convert_exception_location)
                    
    elif event == 'User Guide':
        show_user_guide(window)
        
    elif event == 'Data Format Info':
        show_data_format_info(window)
        
    elif event == 'Troubleshooting':
        show_troubleshooting_guide(window)
        
    elif event == 'Feature Tour':
        show_feature_tour(window)
        
    elif event == 'Release Notes':
        show_release_notes(window)
        
    elif event == 'Report Bug':
        show_bug_report_info(window)
        
    elif event == 'About':
        show_about_dialog(window)
        
    elif event == 'Check for Updates':
        # Check for updates manually
        from update_ui import check_for_updates_manual
        check_for_updates_manual(window)
        
    elif event == 'Update Settings':
        # Show update settings dialog
        from update_ui import show_update_settings
        from auto_updater import get_updater
        
        settings = show_update_settings(window)
        if settings:
            updater = get_updater()
            updater.set_check_on_startup_enabled(settings['check_on_startup_enabled'])
            
            settings_saved_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
            sg.popup("Update settings saved successfully!", title="Settings Updated", location=settings_saved_location)
        
    return None

def handle_status_change(row_index, data_with_indices, window, data_storage=None, fn=None):
    """Handle status change for a game"""
    current_status = data_with_indices[row_index][1][4]
    status_window = sg.Window('Change Status', [
        [sg.Combo(['Pending', 'In progress', 'Completed'], default_value=current_status, key='-STATUS-'),
         sg.Button('OK')]
    ], modal=True, icon='gameslisticon.ico')
    
    while True:
        event, values = status_window.read()
        if event == 'OK':
            new_status = values['-STATUS-']
            if new_status != current_status:  # Only record if status actually changed
                # Record the status change with timestamp
                record_status_change(data_with_indices[row_index][1], current_status, new_status)
                # Update the status
                data_with_indices[row_index][1][4] = new_status
                
                # Update the full dataset when modifying filtered data
                if data_storage:
                    original_index = data_with_indices[row_index][0]
                    # Find and update the correct entry in data_storage
                    for i, (idx, _) in enumerate(data_storage):
                        if idx == original_index:
                            data_storage[i] = data_with_indices[row_index]
                            break

                # Update both table values and row colors to reflect the status change
                from ui_components import update_table_display
                update_table_display(data_with_indices, window)
                
                # Auto-save after status change
                if fn:
                    save_data(data_with_indices, fn, data_storage)
                    
            status_window.close()
            return data_with_indices
        elif event == sg.WIN_CLOSED:
            status_window.close()
            break
    
    return None


def handle_game_action(row_index, data_with_indices, window, data_storage=None, fn=None):
    """Handle game actions like Track Time, Edit Game, Rate Game"""
    action = show_game_actions_dialog(row_index, data_with_indices, window)
    
    if action == "Track Time":
        show_popup(row_index, data_with_indices, window, data_storage, save_filename=fn)
        return {'action': 'time_tracked', 'data': data_with_indices}
        
    elif action == "Edit Game":
        existing_entry = data_with_indices[row_index][1]
        game_name = existing_entry[0]
        
        # Update Discord presence for editing game
        discord = get_discord_integration()
        discord.update_presence_editing_game(game_name)
        
        popup_values, action_type, rating = create_entry_popup(existing_entry, window)
        
        # If action_type is None, the dialog was cancelled - reset Discord presence
        if action_type is None:
            discord = get_discord_integration()
            discord.update_presence_browsing("Games List")
            return None
        
        if action_type == 'Delete':
            # Confirm deletion
            delete_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
            if sg.popup_yes_no(f"Are you sure you want to delete '{existing_entry[0]}'?", 
                               title="Confirm Deletion", location=delete_location) == 'Yes':
                # Remove from data_with_indices
                original_idx = data_with_indices[row_index][0]
                deleted_game = data_with_indices.pop(row_index)
                
                # Also remove from data_storage if filtering is active
                if data_storage:
                    # Find and delete from the original dataset
                    for i, (idx, _) in enumerate(data_storage):
                        if idx == original_idx:
                            data_storage.pop(i)
                            break
                
                # Auto-save after deletion
                if fn:
                    save_data(data_with_indices, fn, data_storage)

                deletion_complete_location = calculate_popup_center_location(window, popup_width=350, popup_height=120)
                sg.popup(f"'{existing_entry[0]}' has been deleted.", title="Deletion Complete", location=deletion_complete_location)
                return {'action': 'game_deleted', 'data': data_with_indices}
        
        elif action_type == 'Submit':
            # Process the submitted values
            new_release = popup_values['-NEW-RELEASE-']
            if new_release == '-' or not new_release.strip():
                new_release_date = '-'  # Use '-' for empty or unknown dates
            else:
                # Safe to parse since validation already passed
                new_release_date = datetime.strptime(new_release, '%Y-%m-%d').strftime('%Y-%m-%d')
                
            time_value = popup_values['-NEW-TIME-']
            if not time_value or time_value in ['00:00:00', '00:00']:
                time_value = None
            
            # Check if status has changed and record if it has
            old_status = existing_entry[4]
            new_status = popup_values['-NEW-STATUS-']
            
            # Create the updated entry
            updated_entry = [
                popup_values['-NEW-NAME-'],
                new_release_date,
                popup_values['-NEW-PLATFORM-'],
                time_value,
                new_status,
                '✅' if popup_values['-NEW-OWNED-'] else '',
                existing_entry[6]
            ]
            
            # Preserve sessions if they exist
            if len(existing_entry) > 7 and existing_entry[7] is not None:
                updated_entry.append(existing_entry[7])
            else:
                updated_entry.append([])
                
            # Preserve or create status history and record change if needed
            if len(existing_entry) > 8 and existing_entry[8] is not None:
                updated_entry.append(existing_entry[8])
            else:
                updated_entry.append([])
                
            # Record status change if it changed
            if old_status != new_status:
                record_status_change(updated_entry, old_status, new_status)
            
            # Add or update rating if provided
            if rating is not None:
                # Make sure there's space for the rating
                while len(updated_entry) <= 9:
                    updated_entry.append(None)
                updated_entry[9] = rating
            elif len(existing_entry) > 9 and existing_entry[9] is not None:
                # Preserve existing rating if no new rating provided
                updated_entry.append(existing_entry[9])
            
            data_with_indices[row_index] = (data_with_indices[row_index][0], updated_entry)
            
            # Update the full dataset when modifying filtered data
            if data_storage:
                original_index = data_with_indices[row_index][0]
                # Find and update the correct entry in data_storage
                for i, (idx, _) in enumerate(data_storage):
                    if idx == original_index:
                        data_storage[i] = data_with_indices[row_index]
                        break

            # Auto-save after editing
            if fn:
                save_data(data_with_indices, fn, data_storage)

            return {'action': 'game_edited', 'data': data_with_indices}
    
    elif action == "Rate Game":
        # Get existing rating if any
        game_data = data_with_indices[row_index][1]
        existing_rating = game_data[9] if len(game_data) > 9 else None
        
        # Show rating popup
        new_rating = show_rating_popup(existing_rating, window)
        if new_rating:
            # Add the rating to the game data
            while len(game_data) <= 9:
                game_data.append(None)
            game_data[9] = new_rating
            
            # Save data after rating
            if fn:
                save_data(data_with_indices, fn, data_storage)
            
            rating_saved_location = calculate_popup_center_location(window, popup_width=350, popup_height=120)
            sg.popup(f"Rating saved for {game_data[0]}", title="Rating Added", location=rating_saved_location)
            return {'action': 'game_rated', 'data': data_with_indices}
    
    elif action == "Add Session":
        # Get game data
        game_data = data_with_indices[row_index][1]
        game_name = game_data[0]
        
        # Show manual session popup
        from session_management import show_manual_session_popup, add_manual_session_to_game
        session = show_manual_session_popup(game_name, window)
        if session:
            # Add session to game
            success = add_manual_session_to_game(game_name, session, data_with_indices, data_storage)
            if success:
                # Save data after adding session
                if fn:
                    save_data(data_with_indices, fn, data_storage)
                
                session_added_location = calculate_popup_center_location(window, popup_width=350, popup_height=120)
                sg.popup(f"Manual session added to {game_name}!", title="Session Added", location=session_added_location)
                return {'action': 'session_added', 'data': data_with_indices}
            else:
                session_error_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                sg.popup_error(f"Failed to add session to {game_name}", title="Error", location=session_error_location)
    
    elif action == "View Statistics":
        # Get game data
        game_data = data_with_indices[row_index][1]
        game_name = game_data[0]
        
        # Switch to Statistics tab and pre-select the game
        return {'action': 'view_statistics', 'game_name': game_name, 'data': data_with_indices}
    
    return None

def handle_session_table_click(values, selected_game, data_with_indices, window, fn=None, data_storage=None):
    """Handle clicks on the session table"""
    try:
        if selected_game and values['-SESSIONS-TABLE-']:
            # Get the selected row index
            if isinstance(values['-SESSIONS-TABLE-'], list) and len(values['-SESSIONS-TABLE-']) > 0:
                selected_row = values['-SESSIONS-TABLE-'][0]
                # Get the sessions for this game
                game_sessions = get_game_sessions(data_with_indices, selected_game)
                
                # Sort sessions the same way as the display function to get the correct session
                def get_session_start_datetime(session):
                    """Get datetime object for sorting, defaulting to epoch for invalid dates"""
                    if 'start' in session:
                        try:
                            return datetime.fromisoformat(session['start'])
                        except (ValueError, TypeError):
                            pass
                    return datetime.min  # Default to earliest possible date for invalid sessions
                
                sorted_sessions = sorted(game_sessions, key=get_session_start_datetime)
                
                if selected_row < len(sorted_sessions):
                    # Get the session from the sorted list (this is what user actually clicked on)
                    session = sorted_sessions[selected_row]
                    
                    # Find the original index of this session in the unsorted list for modification
                    original_session_index = None
                    for i, original_session in enumerate(game_sessions):
                        if original_session is session:  # Reference equality check
                            original_session_index = i
                            break
                    
                    # Safety check to ensure we found the original index
                    if original_session_index is None:
                        print(f"Error: Could not find original session index for selected session")
                        return None
                    
                    has_feedback = 'feedback' in session and session['feedback']
                    
                    # Ask what action to take
                    if has_feedback:
                        # Create a custom popup with buttons
                        feedback_options_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                        feedback_popup = sg.Window("Session Feedback Options", 
                                            [[sg.Text("This session has feedback. What would you like to do?")],
                                            [sg.Button("View"), sg.Button("Edit"), sg.Button("Delete", button_color=('white', 'red')), sg.Button("Cancel")]],
                                            modal=True, icon='gameslisticon.ico', location=feedback_options_location)
                        
                        feedback_action, _ = feedback_popup.read()
                        feedback_popup.close()
                        
                        if feedback_action == "View":  # View
                            feedback_text = session['feedback'].get('text', 'No text provided')
                            rating_info = ""
                            if 'rating' in session['feedback']:
                                rating = session['feedback']['rating']
                                stars = rating.get('stars', 0)
                                rating_info = f"\n\nRating: {STAR_FILLED * stars}{STAR_EMPTY * (5 - stars)}"
                                if rating.get('tags'):
                                    rating_info += f"\nTags: {', '.join(rating['tags'])}"
                            
                            full_feedback = feedback_text + rating_info
                            feedback_view_location = calculate_popup_center_location(window, popup_width=600, popup_height=400)
                            sg.popup_scrolled(full_feedback, title=f"Session Feedback - {selected_game}", size=(60, 20), icon='gameslisticon.ico', location=feedback_view_location)
                            
                        elif feedback_action == "Edit":  # Edit
                            new_feedback = show_session_feedback_popup(session['feedback'], window)
                            if new_feedback is not None:  # None means cancel was pressed
                                session['feedback'] = new_feedback
                                # Update the sessions table
                                update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False)
                                # Make sure to save the changes
                                if fn:
                                    save_data(data_with_indices, fn, data_storage)
                                return {'action': 'session_feedback_edited'}
                                
                        elif feedback_action == "Delete":  # Delete
                            # Ask if user wants to delete just the feedback or the entire session
                            delete_options_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                            delete_options = sg.Window("Delete Options", 
                                                 [[sg.Text("What would you like to delete?")],
                                                 [sg.Button("Delete Feedback Only"), sg.Button("Delete Entire Session"), sg.Button("Cancel")]],
                                                 modal=True, icon='gameslisticon.ico', location=delete_options_location)
                            
                            delete_choice, _ = delete_options.read()
                            delete_options.close()
                            
                            if delete_choice == "Delete Feedback Only":
                                feedback_delete_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                                if sg.popup_yes_no("Are you sure you want to remove this feedback?", title="Confirm Deletion", icon='gameslisticon.ico', location=feedback_delete_location) == "Yes":
                                    # Remove the feedback
                                    session.pop('feedback', None)
                                    # Update the sessions table
                                    update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False)
                                    # Save changes
                                    if fn:
                                        save_data(data_with_indices, fn, data_storage)
                                    return {'action': 'session_feedback_deleted'}
                            elif delete_choice == "Delete Entire Session":
                                session_delete_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                                if sg.popup_yes_no("Are you sure you want to delete this session?", title="Confirm Deletion", icon='gameslisticon.ico', location=session_delete_location) == "Yes":
                                    # Get the game's sessions
                                    game_sessions = get_game_sessions(data_with_indices, selected_game)
                                    # Remove the session using the original index
                                    game_sessions.pop(original_session_index)
                                    # Update the sessions table
                                    update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False)
                                    # Save changes
                                    if fn:
                                        save_data(data_with_indices, fn, data_storage)
                                    return {'action': 'session_deleted'}
                    else:
                        # No feedback exists, show options popup with Add Feedback and Delete options
                        session_options_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                        feedback_popup = sg.Window("Session Options", 
                                            [[sg.Text("What would you like to do with this session?")],
                                            [sg.Button("Add Feedback"), sg.Button("Delete", button_color=('white', 'red')), sg.Button("Cancel")]],
                                            modal=True, icon='gameslisticon.ico', location=session_options_location)
                        
                        feedback_action, _ = feedback_popup.read()
                        feedback_popup.close()
                        
                        if feedback_action == "Add Feedback":
                            new_feedback = show_session_feedback_popup(None, window)
                            if new_feedback:
                                session['feedback'] = new_feedback
                                # Update the sessions table
                                update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False)
                                # Make sure to save the changes
                                if fn:
                                    save_data(data_with_indices, fn, data_storage)
                                return {'action': 'session_feedback_added'}
                        elif feedback_action == "Delete":
                            final_delete_location = calculate_popup_center_location(window, popup_width=400, popup_height=150)
                            if sg.popup_yes_no("Are you sure you want to delete this session?", title="Confirm Deletion", icon='gameslisticon.ico', location=final_delete_location) == "Yes":
                                # Get the game's sessions
                                game_sessions = get_game_sessions(data_with_indices, selected_game)
                                # Remove the session using the original index
                                game_sessions.pop(original_session_index)
                                # Update the sessions table
                                update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False)
                                # Save changes
                                if fn:
                                    save_data(data_with_indices, fn, data_storage)
                                return {'action': 'session_deleted'}
    except Exception as e:
        print(f"Error handling session table click: {str(e)}")
    
    return None

def handle_add_entry(data_with_indices, window, fn=None, data_storage=None):
    """Handle adding a new game entry"""
    # Update Discord presence for adding game
    discord = get_discord_integration()
    discord.update_presence_adding_game()
    
    # Call the popup with no existing entry
    popup_values, action, rating = create_entry_popup(None, window)
    
    if action == 'Submit':
        # All validation is now handled in create_entry_popup() 
        # so we can safely process the values here
        new_release = popup_values['-NEW-RELEASE-']
        if new_release == '-' or not new_release.strip():
            new_release_date = '-'  # Use '-' for empty or unknown dates
        else:
            # Safe to parse since validation already passed
            new_release_date = datetime.strptime(new_release, '%Y-%m-%d').strftime('%Y-%m-%d')
                
        time_value = popup_values['-NEW-TIME-']
        if not time_value or time_value in ['00:00:00', '00:00']:
            time_value = None
        
        # Create the new entry
        new_entry = [
            popup_values['-NEW-NAME-'],
            new_release_date,
            popup_values['-NEW-PLATFORM-'],
            time_value,
            popup_values['-NEW-STATUS-'],
            '✅' if popup_values['-NEW-OWNED-'] else '',
            None  # Last played date
        ]
        
        # Initialize sessions array
        new_entry.append([])
        
        # Initialize status history - record initial status with timestamp
        status_history = []
        initial_status_change = {
            'from': None,
            'to': popup_values['-NEW-STATUS-'],
            'timestamp': datetime.now().isoformat()
        }
        status_history.append(initial_status_change)
        new_entry.append(status_history)
        
        # Add rating if provided
        if rating is not None:
            new_entry.append(rating)
        
        # Handle adding entry properly when filtering is active
        if data_storage is not None:
            # Filtering is active - add to both data_storage and data_with_indices
            new_index = len(data_storage)  # Use full dataset size for correct index
            new_entry_with_index = (new_index, new_entry)
            
            # Add to the full dataset
            data_storage.append(new_entry_with_index)
            
            # Add to current filtered view so it appears immediately
            data_with_indices.append(new_entry_with_index)
        else:
            # No filtering active - add normally
            data_with_indices.append((len(data_with_indices), new_entry))
        
        # Auto-save after adding new entry
        if fn:
            save_data(data_with_indices, fn, data_storage)
        
        # Return to browsing state
        discord.update_presence_browsing("Games List")
        
        return {'action': 'entry_added', 'data': data_with_indices}
    
    # Return to browsing state if cancelled
    discord.update_presence_browsing("Games List")
    return None