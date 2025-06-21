"""
Event handling for the GamesList application.
Handles all user interactions, menu events, and UI event processing.
"""

import os
import re
import PySimpleGUI as sg
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
from utilities import safe_sort_by_date, safe_sort_by_time
from ratings import show_rating_popup, get_session_rating_summary, format_rating

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
                          heatmap_window_months=1, heatmap_end_date=None, distribution_chart_type='line'):
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
        # Get unique game names for the game list - include games with sessions, status history, OR game-level ratings
        game_names = []
        for idx, game_data in data:
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
            import traceback
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
            import traceback
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
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.text(0.5, 0.5, "Select a specific game to view status timeline", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Status Change Timeline", fontsize=12)
        
        # Save to a buffer
        import io
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

def handle_table_event(event, data_with_indices, window, sort_directions, fn=None, data_storage=None):
    """Handle table click events including sorting and row selection"""
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
                            # Handle left-click on any other column - show the game actions dialog
                            return {'action': 'show_actions', 'row_index': row_index}
    
    except Exception as e:
        print(f"Error handling table event: {str(e)}")
    
    return None

def handle_menu_events(event, window, data_with_indices, fn):
    """Handle menu events like Open, Save As, Import, etc."""
    if event == 'Notes::notes_toggle':
        # Notes are now always available with unified feedback system
        sg.popup("Session feedback is now always available when tracking time or viewing sessions.", 
                title="Session Feedback", icon='gameslisticon.ico')
        return None
        
    elif event == 'Open':
        # Open file dialog to select .gmd file
        file_path = sg.popup_get_file('Select .gmd file to open', file_types=(("GMD Files", "*.gmd"),), initial_folder=os.path.dirname(fn))
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
                    sg.popup(f"Successfully loaded {len(loaded_data)} games from {file_path}")
                    return {'action': 'file_loaded', 'data': loaded_data, 'filename': file_path}
            except Exception as e:
                sg.popup_error(f"Error loading file: {str(e)}")
                
    elif event == 'Save As':
        # Open file dialog to select destination .gmd file
        file_path = sg.popup_get_file('Save as .gmd file', file_types=(("GMD Files", "*.gmd"),), 
                                     save_as=True, default_extension=".gmd", 
                                     initial_folder=os.path.dirname(fn))
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
                    sg.popup(f"Successfully saved {len(data_with_indices)} games to {file_path}")
                    return {'action': 'file_saved', 'filename': file_path}
            except Exception as e:
                sg.popup_error(f"Error saving file: {str(e)}")
                
    elif event == 'Import from Excel':
        # Open file dialog to select Excel file
        excel_path = sg.popup_get_file('Select Excel file to import', file_types=(("Excel Files", "*.xlsx"),), initial_folder=os.path.dirname(fn))
        if excel_path and os.path.exists(excel_path):
            # Ask for destination .gmd file
            gmd_path = sg.popup_get_file('Save as .gmd file', file_types=(("GMD Files", "*.gmd"),), 
                                        save_as=True, default_extension=".gmd", 
                                        initial_folder=os.path.dirname(fn))
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
                        sg.popup(f"Successfully converted Excel file to {gmd_path}")
                        return {'action': 'file_converted', 'data': converted_data, 'filename': gmd_path}
                    else:
                        sg.popup_error("Failed to convert Excel file to GMD format.")
                except Exception as e:
                    sg.popup_error(f"Error converting Excel file: {str(e)}")
                    
    elif event == 'User Guide':
        show_user_guide()
        
    elif event == 'Data Format Info':
        show_data_format_info()
        
    elif event == 'Troubleshooting':
        show_troubleshooting_guide()
        
    elif event == 'Feature Tour':
        show_feature_tour()
        
    elif event == 'Release Notes':
        show_release_notes()
        
    elif event == 'Report Bug':
        show_bug_report_info()
        
    elif event == 'About':
        show_about_dialog()
        
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
    action = show_game_actions_dialog(row_index, data_with_indices)
    
    if action == "Track Time":
        show_popup(row_index, data_with_indices, window, data_storage, save_filename=fn)
        return {'action': 'time_tracked', 'data': data_with_indices}
        
    elif action == "Edit Game":
        existing_entry = data_with_indices[row_index][1]
        popup_values, action_type, rating = create_entry_popup(existing_entry)
        
        if action_type == 'Delete':
            # Confirm deletion
            if sg.popup_yes_no(f"Are you sure you want to delete '{existing_entry[0]}'?", 
                               title="Confirm Deletion") == 'Yes':
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

                sg.popup(f"'{existing_entry[0]}' has been deleted.", title="Deletion Complete")
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
        new_rating = show_rating_popup(existing_rating)
        if new_rating:
            # Add the rating to the game data
            while len(game_data) <= 9:
                game_data.append(None)
            game_data[9] = new_rating
            
            # Save data after rating
            if fn:
                save_data(data_with_indices, fn, data_storage)
            
            sg.popup(f"Rating saved for {game_data[0]}", title="Rating Added")
            return {'action': 'game_rated', 'data': data_with_indices}
    
    elif action == "Add Session":
        # Get game data
        game_data = data_with_indices[row_index][1]
        game_name = game_data[0]
        
        # Show manual session popup
        from session_management import show_manual_session_popup, add_manual_session_to_game
        session = show_manual_session_popup(game_name)
        if session:
            # Add session to game
            success = add_manual_session_to_game(game_name, session, data_with_indices, data_storage)
            if success:
                # Save data after adding session
                if fn:
                    save_data(data_with_indices, fn, data_storage)
                
                sg.popup(f"Manual session added to {game_name}!", title="Session Added")
                return {'action': 'session_added', 'data': data_with_indices}
            else:
                sg.popup_error(f"Failed to add session to {game_name}", title="Error")
    
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
                if selected_row < len(game_sessions):
                    # Get the session
                    session = game_sessions[selected_row]
                    has_feedback = 'feedback' in session and session['feedback']
                    
                    # Ask what action to take
                    if has_feedback:
                        # Create a custom popup with buttons
                        feedback_popup = sg.Window("Session Feedback Options", 
                                            [[sg.Text("This session has feedback. What would you like to do?")],
                                            [sg.Button("View"), sg.Button("Edit"), sg.Button("Delete", button_color=('white', 'red')), sg.Button("Cancel")]],
                                            modal=True, icon='gameslisticon.ico')
                        
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
                            sg.popup_scrolled(full_feedback, title=f"Session Feedback - {selected_game}", size=(60, 20), icon='gameslisticon.ico')
                            
                        elif feedback_action == "Edit":  # Edit
                            new_feedback = show_session_feedback_popup(session['feedback'])
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
                            delete_options = sg.Window("Delete Options", 
                                                 [[sg.Text("What would you like to delete?")],
                                                 [sg.Button("Delete Feedback Only"), sg.Button("Delete Entire Session"), sg.Button("Cancel")]],
                                                 modal=True, icon='gameslisticon.ico')
                            
                            delete_choice, _ = delete_options.read()
                            delete_options.close()
                            
                            if delete_choice == "Delete Feedback Only":
                                if sg.popup_yes_no("Are you sure you want to remove this feedback?", title="Confirm Deletion", icon='gameslisticon.ico') == "Yes":
                                    # Remove the feedback
                                    session.pop('feedback', None)
                                    # Update the sessions table
                                    update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False)
                                    # Save changes
                                    if fn:
                                        save_data(data_with_indices, fn, data_storage)
                                    return {'action': 'session_feedback_deleted'}
                            elif delete_choice == "Delete Entire Session":
                                if sg.popup_yes_no("Are you sure you want to delete this session?", title="Confirm Deletion", icon='gameslisticon.ico') == "Yes":
                                    # Get the game's sessions
                                    game_sessions = get_game_sessions(data_with_indices, selected_game)
                                    # Remove the session
                                    game_sessions.pop(selected_row)
                                    # Update the sessions table
                                    update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False)
                                    # Save changes
                                    if fn:
                                        save_data(data_with_indices, fn, data_storage)
                                    return {'action': 'session_deleted'}
                    else:
                        # No feedback exists, show options popup with Add Feedback and Delete options
                        feedback_popup = sg.Window("Session Options", 
                                            [[sg.Text("What would you like to do with this session?")],
                                            [sg.Button("Add Feedback"), sg.Button("Delete", button_color=('white', 'red')), sg.Button("Cancel")]],
                                            modal=True, icon='gameslisticon.ico')
                        
                        feedback_action, _ = feedback_popup.read()
                        feedback_popup.close()
                        
                        if feedback_action == "Add Feedback":
                            new_feedback = show_session_feedback_popup()
                            if new_feedback:
                                session['feedback'] = new_feedback
                                # Update the sessions table
                                update_statistics_tab(window, data_with_indices, selected_game, update_game_list=False)
                                # Make sure to save the changes
                                if fn:
                                    save_data(data_with_indices, fn, data_storage)
                                return {'action': 'session_feedback_added'}
                        elif feedback_action == "Delete":
                            if sg.popup_yes_no("Are you sure you want to delete this session?", title="Confirm Deletion", icon='gameslisticon.ico') == "Yes":
                                # Get the game's sessions
                                game_sessions = get_game_sessions(data_with_indices, selected_game)
                                # Remove the session
                                game_sessions.pop(selected_row)
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
    # Call the popup with no existing entry
    popup_values, action, rating = create_entry_popup()
    
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
        
        data_with_indices.append((len(data_with_indices), new_entry))
        
        # Auto-save after adding new entry
        if fn:
            save_data(data_with_indices, fn, data_storage)
        
        return {'action': 'entry_added', 'data': data_with_indices}
    
    return None

def show_user_guide():
    """Show comprehensive user guide with emoji images"""
    from emoji_utils import emoji_image, get_emoji
    
    # Create a custom window with emoji support
    guide_layout = [
        [sg.Text("GAMES LIST MANAGER - USER GUIDE", font=('Arial', 14, 'bold'), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [sg.Text("=== GETTING STARTED ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Add games using the \"Add Entry\" button")],
            [sg.Text("• Track time by clicking on a game and selecting \"Track Time\"")],
            [sg.Text("• Edit games by clicking on them and selecting \"Edit Game\"")],
            [sg.Text("• Rate games using the \"Rate Game\" option")],
            [sg.Text("")],
            [sg.Text("=== MAIN FEATURES ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [emoji_image(get_emoji('book'), size=16), sg.Text(" GAMES LIST TAB:", font=('Arial', 11, 'bold'))],
            [sg.Text("• View all your games in a sortable table")],
            [sg.Text("• Search/filter games using the search box")],
            [sg.Text("• Click column headers to sort by that column")],
            [sg.Text("• Right-click or left-click games for action menu")],
            [sg.Text("• Color coding: Green=Completed, Yellow=In Progress, Purple=Future Release, Red=Pending")],
            [sg.Text("")],
            [emoji_image(get_emoji('time'), size=16), sg.Text(" TIME TRACKING:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Click \"Track Time\" to start a session timer")],
            [sg.Text("• Use Play/Pause/Stop controls")],
            [sg.Text("• Add session feedback (notes + ratings) when stopping")],
            [sg.Text("• Sessions are automatically saved to your game data")],
            [sg.Text("")],
            [emoji_image(get_emoji('chart'), size=16), sg.Text(" SUMMARY TAB:", font=('Arial', 11, 'bold'))],
            [sg.Text("• View statistics about your game collection")],
            [sg.Text("• Charts showing status distribution, release years, playtime, and ratings")],
            [sg.Text("• Refresh charts with the \"Refresh Charts\" button")],
            [sg.Text("")],
            [emoji_image(get_emoji('stats'), size=16), sg.Text(" STATISTICS TAB:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Detailed session analysis and visualizations")],
            [sg.Text("• Select specific games to view their session history")],
            [sg.Text("• View session feedback, ratings, and status changes")],
            [sg.Text("• Interactive charts: timeline, distribution, heatmap, status changes")],
            [sg.Text("")],
            [sg.Text("=== RATINGS SYSTEM ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Rate games 1-5 stars with optional tags and comments")],
            [sg.Text("• Session ratings: Rate individual gaming sessions")],
            [sg.Text("• Game ratings: Overall rating for the entire game")],
            [sg.Text("• Auto-calculated ratings: Automatically calculated from session ratings")],
            [sg.Text("• Rating comparison: Compare session-based vs manual ratings")],
            [sg.Text("")],
            [sg.Text("=== DATA MANAGEMENT ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Files are saved in .gmd format (JSON-based)")],
            [sg.Text("• Auto-save when tracking time or making changes")],
            [sg.Text("• Import from Excel files (.xlsx)")],
            [sg.Text("• Export/backup using \"Save As\"")],
            [sg.Text("")],
            [emoji_image(get_emoji('light_bulb'), size=16), sg.Text(" TIPS:", font=('Arial', 12, 'bold'))],
            [sg.Text("• Use tags in ratings to categorize your experience")],
            [sg.Text("• Session feedback helps track your gaming journey")],
            [sg.Text("• The heatmap shows your gaming patterns and break habits")],
            [sg.Text("• Status changes are automatically tracked with timestamps")]
        ], scrollable=True, vertical_scroll_only=True, size=(750, 500), expand_x=True, expand_y=True)],
        [sg.Button('Close')]
    ]
    
    guide_window = sg.Window('User Guide', guide_layout, modal=True, size=(800, 600), 
                            icon='gameslisticon.ico', finalize=True, resizable=True)
    
    while True:
        event, values = guide_window.read()
        if event in (sg.WIN_CLOSED, 'Close'):
            break
    
    guide_window.close()

def show_data_format_info():
    """Show information about data formats and file structure"""
    format_text = """
DATA FORMAT INFORMATION

=== FILE FORMATS ===

📄 .GMD FILES (Games Manager Data):
• Primary format used by Games List Manager
• JSON-based structure for easy reading and backup
• Contains all game data, sessions, ratings, and history
• Automatically versioned for future compatibility

📊 EXCEL IMPORT (.XLSX):
• Import existing game lists from Excel spreadsheets
• Expected columns: Name, Release Date, Platform, Time, Status, Owned, Last Played
• Automatically converts to .gmd format after import

=== DATA STRUCTURE ===

Each game entry contains:
• Basic Info: Name, Release Date, Platform, Status, Ownership
• Time Data: Total playtime, Last played date
• Sessions: Individual gaming sessions with timestamps, duration, feedback
• Ratings: Both game-level and session-level ratings with tags and comments
• History: Status change tracking with timestamps

=== SESSION DATA ===
Sessions include:
• Start/End timestamps
• Duration tracking
• Pause/Resume information
• Unified feedback system (notes + ratings)
• Automatic session statistics

=== BACKUP RECOMMENDATIONS ===
• Regular backups using "Save As" to different locations
• .gmd files are human-readable JSON for easy recovery
• Consider cloud storage for automatic backup
• Export important data before major updates

=== MIGRATION ===
• Automatic migration from older data formats
• Unified feedback system migration (notes + ratings combined)
• Backward compatibility maintained where possible
• Migration status shown during file loading

=== FILE LOCATIONS ===
• Default save location: User's home directory
• Config files: Platform-specific application data folders
• Temporary chart files: System temp directory (auto-cleaned)
"""
    
    sg.popup_scrolled(format_text, title="Data Format Information", size=(75, 30), icon='gameslisticon.ico')

def show_troubleshooting_guide():
    """Show troubleshooting guide with emoji images"""
    from emoji_utils import emoji_image, get_emoji
    
    # Create a custom window with emoji support
    troubleshooting_layout = [
        [sg.Text("TROUBLESHOOTING GUIDE", font=('Arial', 14, 'bold'), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [sg.Text("=== COMMON ISSUES ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [emoji_image(get_emoji('tools'), size=16), sg.Text(" APPLICATION WON'T START:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Check if gameslisticon.ico is in the same folder as the executable")],
            [sg.Text("• Ensure you have sufficient permissions in the installation directory")],
            [sg.Text("• Try running as administrator (Windows) or with sudo (Linux/Mac)")],
            [sg.Text("• Check antivirus software isn't blocking the application")],
            [sg.Text("")],
            [emoji_image(get_emoji('file'), size=16), sg.Text(" FILE LOADING ERRORS:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Verify the .gmd file isn't corrupted (should be valid JSON)")],
            [sg.Text("• Check file permissions - ensure read/write access")],
            [sg.Text("• Try opening the file in a text editor to verify it's not empty")],
            [sg.Text("• Backup files are created automatically if corruption is detected")],
            [sg.Text("")],
            [emoji_image(get_emoji('time'), size=16), sg.Text(" TIME TRACKING ISSUES:", font=('Arial', 11, 'bold'))],
            [sg.Text("• If timer doesn't start, check if another instance is running")],
            [sg.Text("• Timer data is saved automatically when stopped")],
            [sg.Text("• If session data is lost, check the last saved .gmd file")],
            [sg.Text("• Pause/resume functionality requires proper session start")],
            [sg.Text("")],
            [emoji_image(get_emoji('chart'), size=16), sg.Text(" CHART/VISUALIZATION PROBLEMS:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Charts not loading: Try refreshing with the \"Refresh Charts\" button")],
            [sg.Text("• Missing data: Ensure games have the required data (dates, times, ratings)")],
            [sg.Text("• Performance issues: Large datasets may take time to generate charts")],
            [sg.Text("• Display issues: Try resizing the window or switching tabs")],
            [sg.Text("")],
            [emoji_image(get_emoji('search'), size=16), sg.Text(" SEARCH NOT WORKING:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Ensure you press Enter after typing in the search box")],
            [sg.Text("• Search is case-insensitive and searches all visible columns")],
            [sg.Text("• Use \"Reset\" button to clear search filters")],
            [sg.Text("• Special characters in game names may affect search")],
            [sg.Text("")],
            [emoji_image(get_emoji('stats'), size=16), sg.Text(" STATISTICS TAB ISSUES:", font=('Arial', 11, 'bold'))],
            [sg.Text("• No data showing: Ensure games have session data or status history")],
            [sg.Text("• Game not in list: Only games with sessions/ratings/status changes appear")],
            [sg.Text("• Charts not updating: Use \"Refresh Statistics\" button")],
            [sg.Text("• Performance: Large session datasets may take time to process")],
            [sg.Text("")],
            [sg.Text("=== DATA RECOVERY ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("If your data is lost or corrupted:")],
            [sg.Text("1. Check for backup files (*.backup-YYYYMMDDHHMMSS)")],
            [sg.Text("2. Look in the default save directory for recent .gmd files")],
            [sg.Text("3. Check the application config for the last used file path")],
            [sg.Text("4. Import from Excel if you have a backup spreadsheet")],
            [sg.Text("")],
            [sg.Text("=== PERFORMANCE OPTIMIZATION ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("For better performance with large datasets:")],
            [sg.Text("• Regularly clean up old session data if not needed")],
            [sg.Text("• Use search/filtering to work with smaller subsets")],
            [sg.Text("• Close other applications when generating complex charts")],
            [sg.Text("• Consider splitting very large game collections into multiple files")],
            [sg.Text("")],
            [sg.Text("=== GETTING HELP ===", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("If problems persist:")],
            [sg.Text("• Contact @drnefarius on Discord for support")],
            [sg.Text("• Discord is the primary and recommended support channel")],
            [sg.Text("• Include your operating system and application version")],
            [sg.Text("• Attach relevant error messages or log files")]
        ], scrollable=True, vertical_scroll_only=True, size=(750, 500), expand_x=True, expand_y=True)],
        [sg.Button('Close')]
    ]
    
    troubleshooting_window = sg.Window('Troubleshooting Guide', troubleshooting_layout, modal=True, size=(800, 600), 
                                      icon='gameslisticon.ico', finalize=True, resizable=True)
    
    while True:
        event, values = troubleshooting_window.read()
        if event in (sg.WIN_CLOSED, 'Close'):
            break
    
    troubleshooting_window.close()

def show_feature_tour():
    """Show feature tour/walkthrough"""
    tour_text = """
FEATURE TOUR - DISCOVER WHAT'S POSSIBLE

=== 🎮 BASIC GAME MANAGEMENT ===

1. ADD YOUR FIRST GAME:
   • Click "Add Entry" button
   • Fill in game name (required)
   • Add release date, platform, initial status
   • Set ownership status with checkbox

2. ORGANIZE YOUR COLLECTION:
   • Use status: Pending → In Progress → Completed
   • Track ownership with the checkbox
   • Sort by any column (click headers)
   • Search to find specific games quickly

=== ⏱️ TIME TRACKING & SESSIONS ===

3. TRACK YOUR GAMING TIME:
   • Click any game → "Track Time"
   • Use Play/Pause/Stop controls
   • Add session feedback when done (notes + rating)
   • Time automatically adds to total playtime

4. SESSION FEEDBACK SYSTEM:
   • Rate individual sessions (1-5 stars)
   • Add tags to categorize experience
   • Write detailed notes about your session
   • View all feedback in the Statistics tab

=== 📊 ANALYTICS & INSIGHTS ===

5. SUMMARY DASHBOARD:
   • Status distribution pie chart
   • Games by release year
   • Top games by playtime
   • Rating distribution analysis

6. DETAILED STATISTICS:
   • Session timeline visualization
   • Gaming heatmap (shows when you play)
   • Session length distribution
   • Status change timeline

=== 🌟 ADVANCED RATING SYSTEM ===

7. DUAL RATING APPROACH:
   • Session ratings: Rate each gaming session
   • Game ratings: Overall rating for the entire game
   • Auto-calculated ratings: Computed from session ratings
   • Rating comparison: See how session vs game ratings differ

8. RICH RATING DATA:
   • 50+ predefined tags (positive, neutral, negative)
   • Custom comments for detailed feedback
   • Tag frequency analysis
   • Rating trends over time

=== 📈 DATA VISUALIZATION ===

9. INTERACTIVE CHARTS:
   • Click and explore different visualizations
   • Refresh data with dedicated buttons
   • Export charts (screenshot capability)
   • Responsive design adapts to window size

10. SESSION ANALYSIS:
    • Gaming heatmap shows daily patterns
    • Pause analysis (focused vs interrupted sessions)
    • Session length trends
    • Most active gaming periods

=== 🔧 POWER USER FEATURES ===

11. DATA MANAGEMENT:
    • Import from Excel spreadsheets
    • Export to .gmd format for backup
    • Automatic data migration between versions
    • Human-readable JSON format

12. CUSTOMIZATION:
    • Configurable save locations
    • Persistent window settings
    • Automatic session saving
    • Flexible data filtering

=== 💡 PRO TIPS ===

• Use session ratings to track how you feel about games over time
• The heatmap reveals your gaming habits and optimal play times
• Tags help identify what you enjoy most in games
• Status history shows your gaming journey progression
• Regular backups ensure your gaming history is preserved

Ready to explore? Start with adding a few games and tracking some sessions!
"""
    
    sg.popup_scrolled(tour_text, title="Feature Tour", size=(85, 40), icon='gameslisticon.ico')

def show_release_notes():
    """Show release notes and version history"""
    from emoji_utils import emoji_image, get_emoji
    
    # Create a custom window with emoji support
    release_notes_layout = [
        [sg.Text("RELEASE NOTES", font=('Arial', 14, 'bold'), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [sg.Text(f"=== VERSION {VERSION} (Current) ===", font=('Arial', 12, 'bold'))],
            [emoji_image(get_emoji('star'), size=16), sg.Text(" NEW FEATURES:", font=('Arial', 11, 'bold'))],
            [sg.Text("• GitHub-style contributions heatmap visualization")],
            [sg.Text("• Year navigation for contributions view (previous/next year)")],
            [sg.Text("• Enhanced table color refresh after status changes")],
            [sg.Text("• Improved data consistency in filtered views")],
            [sg.Text("• Better error handling for contributions visualization")],
            [sg.Text("")],
            [emoji_image(get_emoji('tools'), size=16), sg.Text(" IMPROVEMENTS:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Fixed table row colors not updating after status changes")],
            [sg.Text("• Enhanced update_table_display integration across all operations")],
            [sg.Text("• Improved matplotlib canvas handling for contributions map")],
            [sg.Text("• Better tooltip system for interactive visualizations")],
            [sg.Text("• More robust canvas error handling and fallback display")],
            [sg.Text("")],
            [emoji_image(get_emoji('bug'), size=16), sg.Text(" BUG FIXES:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Fixed table losing color coding after status changes")],
            [sg.Text("• Resolved contributions heatmap display issues")],
            [sg.Text("• Fixed matplotlib tight layout warnings")],
            [sg.Text("• Improved canvas refresh and rendering reliability")],
            [sg.Text("• Better handling of empty contributions data")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.4 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Unified session feedback system (notes + ratings combined)")],
            [sg.Text("• Enhanced rating comparison widget")],
            [sg.Text("• Improved session visualization with heatmaps")],
            [sg.Text("• Status change timeline tracking")],
            [sg.Text("• Auto-calculated ratings from session data")],
            [sg.Text("• Expanded Help menu with comprehensive guides")],
            [sg.Text("• Emoji rendering system for better visual experience")],
            [sg.Text("• Better data migration system")],
            [sg.Text("• Enhanced chart performance and error handling")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.3 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Added Statistics tab with detailed analytics")],
            [sg.Text("• Session tracking with pause/resume functionality")],
            [sg.Text("• Rating system with tags and comments")],
            [sg.Text("• Data visualization improvements")],
            [sg.Text("• Excel import functionality")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.2 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Summary tab with charts and statistics")],
            [sg.Text("• Enhanced time tracking")],
            [sg.Text("• Improved data management")],
            [sg.Text("• Better search and filtering")],
            [sg.Text("")],
            [sg.Text("=== VERSION 1.1 ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Basic game management")],
            [sg.Text("• Simple time tracking")],
            [sg.Text("• File save/load functionality")],
            [sg.Text("• Initial release")],
            [sg.Text("")],
            [emoji_image(get_emoji('crystal_ball'), size=16), sg.Text(" UPCOMING FEATURES (Planned):", font=('Arial', 12, 'bold'))],
            [sg.Text("• Cloud sync capabilities")],
            [sg.Text("• Mobile companion app")],
            [sg.Text("• Advanced filtering options")],
            [sg.Text("• Custom chart creation")],
            [sg.Text("• Social features (share collections)")],
            [sg.Text("• Game recommendation engine")],
            [sg.Text("• Achievement tracking")],
            [sg.Text("• Backup automation")],
            [sg.Text("")],
            [sg.Text("=== TECHNICAL NOTES ===", font=('Arial', 12, 'bold'))],
            [sg.Text("• Built with Python and PySimpleGUI")],
            [sg.Text("• Uses matplotlib for visualizations")],
            [sg.Text("• JSON-based data storage (.gmd format)")],
            [sg.Text("• Cross-platform compatibility (Windows, Mac, Linux)")],
            [sg.Text("• Modular architecture for easy maintenance")],
            [sg.Text("• Pillow (PIL) for emoji rendering")],
            [sg.Text("")],
            [sg.Text("=== FEEDBACK & CONTRIBUTIONS ===", font=('Arial', 12, 'bold'))],
            [sg.Text("We welcome feedback and contributions!")],
            [sg.Text("• Report bugs via Discord (@drnefarius)")],
            [sg.Text("• Suggest features via Discord (@drnefarius)")],
            [sg.Text("• Share your gaming insights with the community")],
            [sg.Text("• Contribute ideas for new features")],
            [sg.Text("")],
            [sg.Text("Thank you for using Games List Manager!", font=('Arial', 11, 'bold'))]
        ], scrollable=True, vertical_scroll_only=True, size=(750, 500), expand_x=True, expand_y=True)],
        [sg.Button('Close')]
    ]
    
    release_notes_window = sg.Window('Release Notes', release_notes_layout, modal=True, size=(800, 600), 
                                    icon='gameslisticon.ico', finalize=True, resizable=True)
    
    while True:
        event, values = release_notes_window.read()
        if event in (sg.WIN_CLOSED, 'Close'):
            break
    
    release_notes_window.close()

def show_bug_report_info():
    """Show bug reporting information with emoji images"""
    from emoji_utils import emoji_image, get_emoji
    
    # Create a custom window with emoji support
    bug_report_layout = [
        [sg.Text("BUG REPORTING & FEEDBACK", font=('Arial', 14, 'bold'), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [sg.Column([
            [emoji_image(get_emoji('bug'), size=18), sg.Text(" REPORTING BUGS", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("When reporting a bug, please include:")],
            [sg.Text("")],
            [emoji_image(get_emoji('book'), size=16), sg.Text(" SYSTEM INFORMATION:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Operating System (Windows 10/11, macOS, Linux distribution)")],
            [sg.Text(f"• Application version (currently {VERSION})")],
            [sg.Text("• Python version (if running from source)")],
            [sg.Text("• Screen resolution and scaling settings")],
            [sg.Text("")],
            [emoji_image(get_emoji('search'), size=16), sg.Text(" BUG DETAILS:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Clear description of what happened")],
            [sg.Text("• Steps to reproduce the issue")],
            [sg.Text("• Expected vs actual behavior")],
            [sg.Text("• Screenshots if applicable")],
            [sg.Text("• Error messages (exact text)")],
            [sg.Text("")],
            [emoji_image(get_emoji('file'), size=16), sg.Text(" DATA INFORMATION:", font=('Arial', 11, 'bold'))],
            [sg.Text("• Size of your .gmd file (number of games/sessions)")],
            [sg.Text("• Whether the issue occurs with new or existing data")],
            [sg.Text("• If the issue started after a specific action")],
            [sg.Text("")],
            [emoji_image(get_emoji('email'), size=16), sg.Text(" HOW TO REPORT:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("DISCORD:")],
            [sg.Text("   • Contact: @drnefarius")],
            [sg.Text("   • Include screenshots and error details")],
            [sg.Text("   • Best for quick questions and clarifications")],
            [sg.Text("   • Include all relevant information listed above")],
            [sg.Text("")],
            [sg.Text("GITHUB ISSUES (Community Support):")],
            [sg.Text("   • Repository: "), sg.Text("https://github.com/DrNefarius/GameTracker", 
                     text_color='blue', enable_events=True, key='-GITHUB-LINK-', 
                     tooltip='Click to open repository in browser')],
            [sg.Text("   • Use for structured bug reports and feature requests")],
            [sg.Text("• Search existing issues before creating new ones")],
            [sg.Text("• Follow the same information guidelines as above")],
            [sg.Text("")],
            [sg.Text("NOTE: There is no in-app bug reporting feature.")],
            [sg.Text("All support requests should go through Discord or GitHub Issues.")],
            [sg.Text("")],
            [emoji_image(get_emoji('rocket'), size=16), sg.Text(" FEATURE REQUESTS:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("Have an idea for improvement?")],
            [sg.Text("• Describe the feature and its benefits")],
            [sg.Text("• Explain your use case")],
            [sg.Text("• Suggest how it might work")],
            [sg.Text("• Consider if it fits the application's scope")],
            [sg.Text("")],
            [emoji_image(get_emoji('handshake'), size=16), sg.Text(" CONTRIBUTING:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("Want to help improve the application?")],
            [sg.Text("• Feature suggestions welcome via Discord")],
            [sg.Text("• Documentation improvements")],
            [sg.Text("• Testing on different platforms")],
            [sg.Text("• UI/UX suggestions")],
            [sg.Text("• Translation assistance")],
            [sg.Text("")],
            [emoji_image(get_emoji('chart'), size=16), sg.Text(" DIAGNOSTIC INFORMATION:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("To help with debugging, you can:")],
            [sg.Text("• Check the console output for error messages")],
            [sg.Text("• Look for backup files if data is corrupted")],
            [sg.Text("• Note the exact sequence of actions that caused the issue")],
            [sg.Text("• Test if the issue occurs with a fresh data file")],
            [sg.Text("")],
            [emoji_image(get_emoji('lightning'), size=16), sg.Text(" QUICK FIXES:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("Before reporting, try these common solutions:")],
            [sg.Text("• Restart the application")],
            [sg.Text("• Check file permissions")],
            [sg.Text("• Verify .gmd file isn't corrupted (open in text editor)")],
            [sg.Text("• Try with a smaller dataset")],
            [sg.Text("• Update to the latest version")],
            [sg.Text("")],
            [emoji_image(get_emoji('pray'), size=16), sg.Text(" THANK YOU:", font=('Arial', 12, 'bold'))],
            [sg.Text("")],
            [sg.Text("Your feedback helps make Games List Manager better for everyone!")],
            [sg.Text("Every bug report and suggestion is valuable for improving the application.")],
            [sg.Text("")],
            [sg.Text("We appreciate your patience and support in making this the best")],
            [sg.Text("game collection manager possible.")]
        ], scrollable=True, vertical_scroll_only=True, size=(750, 500), expand_x=True, expand_y=True)],
        [sg.Button('Close')]
    ]
    
    bug_report_window = sg.Window('Bug Reporting & Feedback', bug_report_layout, modal=True, size=(800, 600), 
                                 icon='gameslisticon.ico', finalize=True, resizable=True)
    
    while True:
        event, values = bug_report_window.read()
        if event in (sg.WIN_CLOSED, 'Close'):
            break
        elif event == '-GITHUB-LINK-':
            import webbrowser
            webbrowser.open('https://github.com/DrNefarius/GameTracker')
    
    bug_report_window.close()

def show_about_dialog():
    """Show enhanced about dialog with emoji images"""
    import sys
    import platform
    from datetime import datetime
    from emoji_utils import emoji_image, get_emoji
    
    # Get system information
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    os_info = f"{platform.system()} {platform.release()}"
    
    about_layout = [
        [sg.Text("Games List Manager", font=('Arial', 16, 'bold'), justification='center', expand_x=True)],
        [sg.Text(f"Version {VERSION}", font=('Arial', 12), justification='center', expand_x=True)],
        [sg.HorizontalSeparator()],
        [emoji_image(get_emoji('game'), size=20), sg.Text(" Manage your game collection with style", justification='center', expand_x=True)],
        [sg.Text("Track playtime • Rate games • Analyze sessions", justification='center', expand_x=True)],
        [sg.VPush()],
        [sg.Frame('Features', [
            [sg.Text("• Comprehensive game library management")],
            [sg.Text("• Advanced time tracking with session analytics")],
            [sg.Text("• Dual rating system (session + game ratings)")],
            [sg.Text("• Rich data visualizations and statistics")],
            [sg.Text("• Session feedback with notes and tags")],
            [sg.Text("• Excel import and .gmd export capabilities")],
            [sg.Text("• Cross-platform compatibility")]
        ], font=('Arial', 10))],
        [sg.VPush()],
        [sg.Frame('Technical Information', [
            [sg.Text(f"Python Version: {python_version}")],
            [sg.Text(f"Operating System: {os_info}")],
            [sg.Text(f"GUI Framework: PySimpleGUI")],
            [sg.Text(f"Charts: Matplotlib")],
            [sg.Text(f"Data Format: JSON (.gmd)")],
            [sg.Text(f"Build Date: {datetime.now().strftime('%Y-%m-%d')}")]
        ], font=('Arial', 9))],
        [sg.VPush()],
        [sg.Frame('Credits', [
            [emoji_image(get_emoji('dev'), size=16), sg.Text(" Developer: @drnefarius", justification='center', expand_x=True)],
            [emoji_image(get_emoji('chat'), size=16), sg.Text(" Discord: @drnefarius", justification='center', expand_x=True)],
            [emoji_image(get_emoji('support'), size=16), sg.Text(" Support: Available through Discord", justification='center', expand_x=True)],
            [emoji_image(get_emoji('community'), size=16), sg.Text(" Community: Join us for gaming discussions!", justification='center', expand_x=True)]
        ], font=('Arial', 10))],
        [sg.VPush()],
        [sg.Frame('License & Legal', [
            [sg.Text("© 2024 Games List Manager. All rights reserved.", justification='center', expand_x=True)],
            [sg.Text("This software is provided 'as-is' without warranty.", justification='center', expand_x=True)],
            [sg.Text("Open source components used under their respective licenses.", justification='center', expand_x=True)]
        ], font=('Arial', 9))],
        [sg.VPush()],
        [sg.Button('View Release Notes', key='-RELEASE-NOTES-'), 
         sg.Button('Report Bug', key='-REPORT-BUG-'), 
         sg.Button('Close', key='-CLOSE-')]
    ]
    
    about_window = sg.Window('About Games List Manager', about_layout, 
                            modal=True, size=(500, 600), icon='gameslisticon.ico', finalize=True)
    
    while True:
        event, values = about_window.read()
        
        if event in (sg.WIN_CLOSED, '-CLOSE-'):
            break
        elif event == '-RELEASE-NOTES-':
            about_window.close()
            show_release_notes()
            break
        elif event == '-REPORT-BUG-':
            about_window.close()
            show_bug_report_info()
            break
    
    about_window.close() 