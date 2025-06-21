"""
Session management functionality for the GamesList application.
Handles session tracking, time management, notes, and session statistics.
"""

# Standard library imports
import io
import re
from datetime import datetime, timedelta, date
from collections import defaultdict

# Third-party imports
import PySimpleGUI as sg
import matplotlib.pyplot as plt
import numpy as np

# Local imports
from visualizations import isolate_matplotlib_env
from session_data import (
    get_latest_session_end_time, 
    extract_all_sessions, 
    calculate_session_statistics, 
    get_game_sessions, 
    get_status_history, 
    add_manual_session_to_game, 
    find_most_active_period
)
from session_ui import (
    show_popup, 
    show_session_feedback_popup, 
    show_manual_session_popup
)
from session_display import (
    display_all_game_notes, 
    format_session_for_display, 
    format_status_history_for_display
)
from session_visualizations import (
    create_session_timeline_chart, 
    create_session_distribution_chart,
    create_status_timeline_chart,
    create_comments_word_cloud_visualization,
    calculate_gaming_streak
)


def migrate_pauses_to_integrated_structure(session_pauses):
    """Convert old pause structure (separate pause/resume events) to new integrated structure"""
    if not session_pauses:
        return []
    
    integrated_pauses = []
    current_pause = None
    
    for event in session_pauses:
        if 'paused_at' in event:
            current_pause = {
                'paused_at': event['paused_at'],
                'elapsed_so_far': event.get('elapsed_so_far', '00:00:00')
            }
        elif 'resumed_at' in event and current_pause:
            current_pause['resumed_at'] = event['resumed_at']
            
            try:
                pause_start = datetime.fromisoformat(current_pause['paused_at'])
                pause_end = datetime.fromisoformat(current_pause['resumed_at'])
                pause_duration = pause_end - pause_start
                
                hours, remainder = divmod(pause_duration.total_seconds(), 3600)
                minutes, seconds = divmod(remainder, 60)
                current_pause['pause_duration'] = f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"
            except (ValueError, TypeError):
                current_pause['pause_duration'] = "00:00:00"
            
            integrated_pauses.append(current_pause)
            current_pause = None
    
    if current_pause:
        current_pause['incomplete'] = True
        integrated_pauses.append(current_pause)
    
    return integrated_pauses


def migrate_session_to_unified_feedback(session):
    """Migrate session from old format (notes + rating) to new unified feedback format"""
    if 'feedback' in session:
        return session
    
    feedback_parts = []
    feedback_obj = {
        'text': '',
        'timestamp': session.get('start', datetime.now().isoformat())
    }
    
    existing_note = session.get('note', '')
    existing_rating = session.get('rating')
    
    if existing_rating and 'stars' in existing_rating:
        feedback_obj['rating'] = {
            'stars': existing_rating['stars'],
            'tags': existing_rating.get('tags', []),
            'timestamp': existing_rating.get('timestamp', feedback_obj['timestamp'])
        }
    
    if existing_note:
        note_lines = existing_note.split('\n')
        clean_note_lines = []
        
        for line in note_lines:
            line_stripped = line.strip()
            if not (re.match(f"^Rating:\s*[★☆]+.*Tags:", line_stripped) or 
                   line_stripped.startswith("Rating:") and any(star in line_stripped for star in ['★', '☆'])):
                clean_note_lines.append(line)
        
        clean_note = '\n'.join(clean_note_lines).strip()
        if clean_note:
            feedback_parts.append(clean_note)
    
    if existing_rating and 'comment' in existing_rating and existing_rating['comment']:
        rating_comment = existing_rating['comment']
        clean_note_text = feedback_parts[0] if feedback_parts else ''
        if rating_comment != clean_note_text and rating_comment not in clean_note_text:
            feedback_parts.append(f"Rating comment: {rating_comment}")
    
    if feedback_parts:
        feedback_obj['text'] = '\n\n'.join(feedback_parts)
    
    new_session = {}
    for key, value in session.items():
        if key not in ['note', 'rating']:
            new_session[key] = value
    
    if feedback_obj['text'] or 'rating' in feedback_obj:
        new_session['feedback'] = feedback_obj
    
    return new_session


def migrate_all_game_sessions(data_with_indices):
    """Migrate all sessions in the dataset to unified feedback format and integrated pause structure"""
    migrated_data = []
    
    for idx, game_data in data_with_indices:
        new_game_data = game_data.copy()
        
        if len(new_game_data) > 7 and new_game_data[7]:
            migrated_sessions = []
            for session in new_game_data[7]:
                migrated_session = migrate_session_to_unified_feedback(session)
                
                if 'pauses' in migrated_session and migrated_session['pauses']:
                    needs_pause_migration = False
                    for pause_event in migrated_session['pauses']:
                        if isinstance(pause_event, dict):
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


# Large visualization functions remain here due to their complexity
def create_session_heatmap(sessions, game_name=None, window_months=1, end_date=None):
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
    
    if not windowed_sessions:
        fig, ax = plt.subplots(figsize=(9, 2.5))
        period_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        ax.text(0.5, 0.5, f"No session data available for period:\n{period_str}", 
                ha='center', va='center', fontsize=10)
        title = f"Gaming Heatmap for {game_name}" if game_name else "Gaming Sessions Heatmap"
        title += f"\n({period_str})"
        ax.set_title(title, fontsize=12)
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    session_segments = []
    
    for session in windowed_sessions:
        try:
            if 'start' in session and 'end' in session and 'pauses' in session:
                start_time = datetime.fromisoformat(session['start'])
                end_time = datetime.fromisoformat(session['end'])
                
                pause_periods = []
                if session['pauses']:
                    for pause in session['pauses']:
                        if 'paused_at' in pause and 'resumed_at' in pause:
                            pause_time = datetime.fromisoformat(pause['paused_at'])
                            resume_time = datetime.fromisoformat(pause['resumed_at'])
                            pause_length = (resume_time - pause_time).total_seconds() / 60
                            pause_periods.append({
                                'start': pause_time,
                                'end': resume_time,
                                'duration': pause_length
                            })
                
                current_time = start_time
                original_session_id = id(session)
                
                while current_time < end_time:
                    current_date = current_time.date()
                    next_midnight = datetime.combine(current_date + timedelta(days=1), datetime.min.time())
                    
                    segment_end = min(end_time, next_midnight)
                    
                    segment_info = {
                        'start': current_time,
                        'end': segment_end,
                        'date': current_date,
                        'pauses': [],
                        'is_continuation': current_time != start_time,
                        'continues_next_day': segment_end == next_midnight and segment_end < end_time,
                        'original_session_id': original_session_id
                    }
                    
                    for pause in pause_periods:
                        if (pause['start'] < segment_end and pause['end'] > current_time):
                            segment_pause = {
                                'start': max(pause['start'], current_time),
                                'end': min(pause['end'], segment_end),
                                'duration': 0
                            }
                            segment_pause['duration'] = (segment_pause['end'] - segment_pause['start']).total_seconds() / 60
                            segment_info['pauses'].append(segment_pause)
                    
                    session_segments.append(segment_info)
                    current_time = next_midnight
                    
        except Exception as e:
            print(f"Error processing session for heatmap: {str(e)}")
            continue
    
    session_segments.sort(key=lambda x: (x['start']))
    
    date_sessions = {}
    for segment in session_segments:
        date_str = segment['date'].strftime('%Y-%m-%d')
        if date_str not in date_sessions:
            date_sessions[date_str] = []
        date_sessions[date_str].append(segment)
    
    fig, ax = plt.subplots(figsize=(9, 3.5))
    
    if date_sessions:
        dates = sorted(date_sessions.keys())
        y_pos = len(dates)
        
        ax.set_ylim(0, len(dates))
        ax.set_xlim(0, 24)
        
        for hour in range(1, 24):
            ax.axvline(x=hour, color='lightgray', linestyle='-', alpha=0.5, linewidth=0.5)
        
        ax.set_xticks(range(0, 25, 3))
        ax.set_xticklabels([f"{i:02d}:00" for i in range(0, 25, 3)], fontsize=8)
        
        for i, date in enumerate(dates):
            ax.text(-0.5, i + 0.5, date, ha='right', va='center', fontsize=8)
            
            for segment in date_sessions[date]:
                start_hour = segment['start'].hour + segment['start'].minute / 60
                end_hour = segment['end'].hour + segment['end'].minute / 60
                
                if end_hour == 0 and segment['end'].time() == datetime.min.time():
                    end_hour = 24
                
                if segment['is_continuation'] and segment['continues_next_day']:
                    facecolor = '#4a9a4a'
                    edgecolor = '#2d5a2d'
                    linestyle = ':'
                elif segment['is_continuation']:
                    facecolor = '#5cb85c'
                    edgecolor = '#2d5a2d'
                    linestyle = '-'
                elif segment['continues_next_day']:
                    facecolor = '#5cb85c'
                    edgecolor = '#2d5a2d'
                    linestyle = '-'
                else:
                    facecolor = '#5cb85c'
                    edgecolor = None
                    linestyle = '-'
                
                rect = plt.Rectangle((start_hour, i), end_hour - start_hour, 0.8, 
                                   alpha=0.7, edgecolor=edgecolor, linewidth=1 if edgecolor else 0,
                                   linestyle=linestyle, facecolor=facecolor)
                ax.add_patch(rect)
                
                for pause in segment['pauses']:
                    pause_start = pause['start'].hour + pause['start'].minute / 60
                    pause_end = pause['end'].hour + pause['end'].minute / 60
                    
                    if pause_end == 0 and pause['end'].time() == datetime.min.time():
                        pause_end = 24
                    
                    if pause_start >= start_hour and pause_end <= end_hour:
                        pause_rect = plt.Rectangle((pause_start, i), pause_end - pause_start, 0.8,
                                                 alpha=0.8, edgecolor='none',
                                                 facecolor='#ff9933')
                        ax.add_patch(pause_rect)
                
                mid_point = (start_hour + end_hour) / 2
                
                if segment['is_continuation'] and segment['continues_next_day']:
                    ax.text(mid_point, i + 0.4, "↔", ha='center', va='center', fontsize=12, fontweight='bold')
                elif segment['is_continuation']:
                    ax.text(mid_point, i + 0.4, "←", ha='center', va='center', fontsize=12, fontweight='bold')
                elif segment['continues_next_day']:
                    ax.text(mid_point, i + 0.4, "→", ha='center', va='center', fontsize=12, fontweight='bold')
                else:
                    if len(segment['pauses']) == 0:
                        ax.text(mid_point, i + 0.4, "★", ha='center', va='center', fontsize=12, fontweight='bold')
                    elif len(segment['pauses']) == 1:
                        ax.text(mid_point, i + 0.4, "◉", ha='center', va='center', fontsize=12)
                    elif len(segment['pauses']) < 4:
                        ax.text(mid_point, i + 0.4, "◯", ha='center', va='center', fontsize=12)
                    else:
                        ax.text(mid_point, i + 0.4, "×", ha='center', va='center', fontsize=12)
        
        ax.set_yticks([])
        ax.grid(True, which='major', axis='x', linestyle='-', alpha=0.3)
        
        period_str = f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}"
        title = f"Gaming Heatmap for {game_name}" if game_name else "Gaming Sessions Heatmap"
        title += f"\n({period_str})"
        ax.set_title(title, fontsize=12)
        ax.set_xlabel("Time of Day", fontsize=10)
        
        unique_sessions = len(set(segment['original_session_id'] for segment in session_segments))
        ax.text(1.05, 0.15, f"Sessions: {unique_sessions}", transform=ax.transAxes, 
                fontsize=10, horizontalalignment='left', verticalalignment='top',
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue", alpha=0.8))
        
        legend_elements = [
            plt.Rectangle((0, 0), 1, 1, facecolor='#5cb85c', alpha=0.7, label='Active Gaming'),
            plt.Rectangle((0, 0), 1, 1, facecolor='#ff9933', alpha=0.8, label='Pauses'),
            plt.Line2D([0], [0], marker='$→$', color='black', label='Session Continues Next Day', linestyle='',
                      markerfacecolor='k', markersize=10),
            plt.Line2D([0], [0], marker='$←$', color='black', label='Session From Previous Day', linestyle='', 
                      markerfacecolor='k', markersize=10),
            plt.Line2D([0], [0], marker='$↔$', color='black', label='Multi-Day Session Middle', linestyle='', 
                      markerfacecolor='k', markersize=10),
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
    
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf


def create_github_style_contributions_heatmap(sessions, game_name=None):
    """Create a GitHub-style contributions heatmap showing gaming activity over time"""
    isolate_matplotlib_env()
    
    if not sessions:
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.text(0.5, 0.5, "No session data available for contributions heatmap", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Gaming Contributions", fontsize=12)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    daily_activity = {}
    
    for session in sessions:
        try:
            if 'start' in session and 'duration' in session:
                start_time = datetime.fromisoformat(session['start'])
                date_key = start_time.date()
                
                duration_str = session['duration']
                parts = duration_str.split(':')
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    duration_minutes = h * 60 + m + s/60
                    
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
    
    calendar_start = start_date - timedelta(days=start_date.weekday())
    
    activity_matrix = np.zeros((7, weeks_to_show))
    date_matrix = []
    
    for week in range(weeks_to_show):
        week_dates = []
        for day in range(7):
            current_date = calendar_start + timedelta(days=week*7 + day)
            week_dates.append(current_date)
            
            if current_date in daily_activity:
                activity_matrix[day, week] = daily_activity[current_date]['total_minutes']
        
        date_matrix.append(week_dates)
    
    colors = ['#ebedf0', '#9be9a8', '#40c463', '#30a14e', '#216e39']
    max_activity = np.max(activity_matrix) if np.max(activity_matrix) > 0 else 1
    
    square_size = 11
    gap_size = 2
    margin_left = 60
    margin_top = 55
    margin_bottom = 60
    margin_right = 20
    
    canvas_width = margin_left + weeks_to_show * (square_size + gap_size) + 200
    canvas_height = margin_top + 7 * (square_size + gap_size) + margin_bottom
    
    square_data = {}
    
    def draw_heatmap_on_canvas(canvas_element):
        canvas = canvas_element.Widget
        canvas.delete("all")
        
        tooltip_items = []
        
        def on_mouse_motion(event):
            for item in tooltip_items:
                canvas.delete(item)
            tooltip_items.clear()
            
            x, y = event.x, event.y
            hovered_square = None
            for square_id, square_info in square_data.items():
                if (square_info['x1'] <= x <= square_info['x2'] and 
                    square_info['y1'] <= y <= square_info['y2']):
                    hovered_square = square_info
                    break
            
            if hovered_square and hovered_square['in_range']:
                date_str = hovered_square['date'].strftime('%B %d, %Y')
                if hovered_square['activity'] > 0:
                    hours = int(hovered_square['activity'] // 60)
                    minutes = int(hovered_square['activity'] % 60)
                    time_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                    
                    tooltip_lines = [f"{hovered_square['sessions']} session(s) on {date_str}"]
                    tooltip_lines.append(f"{time_str} total")
                    
                    if 'games' in hovered_square and hovered_square['games']:
                        tooltip_lines.append("")
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
                        
                        if len(game_details) > 3:
                            tooltip_lines.extend(game_details[:3])
                            tooltip_lines.append(f"• ... and {len(game_details) - 3} more")
                        else:
                            tooltip_lines.extend(game_details)
                    
                    tooltip_text = "\n".join(tooltip_lines)
                else:
                    tooltip_text = f"No gaming on {date_str}"
                
                text_lines = tooltip_text.split('\n')
                max_line_length = max(len(line) for line in text_lines) if text_lines else 0
                tooltip_width = max(max_line_length * 7 + 15, 150)
                tooltip_height = len(text_lines) * 12 + 10
                
                if x > canvas_width * 0.66:
                    tooltip_x = max(x - tooltip_width - 15, 10)
                else:
                    tooltip_x = min(x + 15, canvas_width - tooltip_width - 20)
                tooltip_y = max(y - 30, 10)
                
                bg_item = canvas.create_rectangle(
                    tooltip_x - 5, tooltip_y - 5,
                    tooltip_x + tooltip_width, tooltip_y + tooltip_height,
                    fill='black', outline='gray', width=1
                )
                tooltip_items.append(bg_item)
                
                for i, line in enumerate(text_lines):
                    text_item = canvas.create_text(
                        tooltip_x, tooltip_y + i * 12,
                        text=line, fill='white', font=('Arial', 9),
                        anchor='nw'
                    )
                    tooltip_items.append(text_item)
        
        canvas.bind('<Motion>', on_mouse_motion)
        
        def on_mouse_leave(event):
            for item in tooltip_items:
                canvas.delete(item)
            tooltip_items.clear()
        
        canvas.bind('<Leave>', on_mouse_leave)
        
        title_text = f"Gaming Contributions - {game_name}" if game_name else "Gaming Contributions"
        canvas.create_text(canvas_width//2, 18, text=title_text, font=('Arial', 12, 'bold'), fill='black')
        
        day_labels = ['Mon', 'Wed', 'Fri']
        day_indices = [0, 2, 4]
        
        for i, (label, day_idx) in enumerate(zip(day_labels, day_indices)):
            y_pos = margin_top + day_idx * (square_size + gap_size) + square_size // 2
            canvas.create_text(margin_left - 10, y_pos, text=label, font=('Arial', 8), fill='#586069', anchor='e')
        
        current_month = None
        for week in range(weeks_to_show):
            if week < len(date_matrix):
                week_start_date = date_matrix[week][0]
                if week_start_date.month != current_month:
                    current_month = week_start_date.month
                    x_pos = margin_left + week * (square_size + gap_size)
                    canvas.create_text(x_pos, margin_top - 18, text=week_start_date.strftime('%b'), 
                                     font=('Arial', 8), fill='#586069', anchor='w')
        
        square_data.clear()
        today = datetime.now().date()
        for week in range(weeks_to_show):
            for day in range(7):
                activity = activity_matrix[day, week]
                current_date = date_matrix[week][day]
                
                x1 = margin_left + week * (square_size + gap_size)
                y1 = margin_top + day * (square_size + gap_size)
                x2 = x1 + square_size
                y2 = y1 + square_size
                
                if start_date <= current_date <= end_date:
                    if activity == 0:
                        color = colors[0]
                    else:
                        intensity = min(4, max(1, int((activity / max_activity) * 4)))
                        color = colors[intensity]
                else:
                    color = '#f6f8fa'
                
                square_id = canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=color, width=0)
                
                if current_date == today:
                    canvas.create_rectangle(x1, y1, x2, y2, fill='', outline='red', width=2)
                
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
        
        legend_x = margin_left + 50
        legend_y = margin_top + 7 * (square_size + gap_size) + 25
        
        canvas.create_text(legend_x - 15, legend_y, text="Less", font=('Arial', 8), fill='#586069', anchor='e')
        
        for i, color in enumerate(colors):
            square_x = legend_x + i * (square_size + 2)
            canvas.create_rectangle(square_x, legend_y - 5, square_x + square_size, legend_y + 5, 
                                  fill=color, outline=color, width=0)
        
        canvas.create_text(legend_x + len(colors) * (square_size + 2) + 15, legend_y, 
                          text="More", font=('Arial', 8), fill='#586069', anchor='w')
        
        total_sessions = sum(data['sessions'] for data in daily_activity.values())
        max_streak = calculate_gaming_streak(daily_activity, start_date, end_date)
        date_range_days = (end_date - start_date).days + 1
        summary_text = f"{total_sessions} sessions in the last {date_range_days} days • Longest streak: {max_streak} days"
        
        summary_y = legend_y + 25
        canvas.create_text(canvas_width//2, summary_y, text=summary_text, 
                          font=('Arial', 9), fill='#586069')
    
    return {
        'draw_function': draw_heatmap_on_canvas,
        'square_data': square_data,
        'canvas_key': '-CONTRIBUTIONS-CANVAS-'
    }


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
    end_date = date(show_year, 12, 31)
    start_date = date(show_year, 1, 1)
    
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
        
        canvas.bind('<Motion>', on_mouse_motion)
        
        def on_mouse_leave(event):
            for item in tooltip_items:
                canvas.delete(item)
            tooltip_items.clear()
        
        canvas.bind('<Leave>', on_mouse_leave)
        
        title_text = f"Gaming Contributions - {game_name}" if game_name else "Gaming Contributions"
        canvas.create_text(canvas_width//2, 18, text=title_text, font=('Arial', 12, 'bold'), fill='black')
        
        day_labels = ['Mon', 'Wed', 'Fri']
        day_indices = [0, 2, 4]
        
        for i, (label, day_idx) in enumerate(zip(day_labels, day_indices)):
            y_pos = margin_top + day_idx * (square_size + gap_size) + square_size // 2
            canvas.create_text(margin_left - 10, y_pos, text=label, font=('Arial', 8), fill='#586069', anchor='e')
        
        current_month = None
        for week in range(weeks_to_show):
            if week < len(date_matrix):
                week_start_date = date_matrix[week][0]
                if week_start_date.month != current_month:
                    current_month = week_start_date.month
                    x_pos = margin_left + week * (square_size + gap_size)
                    canvas.create_text(x_pos, margin_top - 18, text=week_start_date.strftime('%b'), 
                                     font=('Arial', 8), fill='#586069', anchor='w')
        
        square_data.clear()
        today = datetime.now().date()
        for week in range(weeks_to_show):
            for day in range(7):
                activity = activity_matrix[day, week]
                current_date = date_matrix[week][day]
                
                x1 = margin_left + week * (square_size + gap_size)
                y1 = margin_top + day * (square_size + gap_size)
                x2 = x1 + square_size
                y2 = y1 + square_size
                
                if start_date <= current_date <= end_date:
                    if activity == 0:
                        color = colors[0]
                    else:
                        intensity = min(4, max(1, int((activity / max_activity) * 4)))
                        color = colors[intensity]
                else:
                    color = '#f6f8fa'
                
                square_id = canvas.create_rectangle(x1, y1, x2, y2, fill=color, outline=color, width=0)
                
                if current_date == today:
                    canvas.create_rectangle(x1, y1, x2, y2, fill='', outline='red', width=2)
                
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
        
        legend_x = margin_left + 50
        legend_y = margin_top + 7 * (square_size + gap_size) + 25
        
        canvas.create_text(legend_x - 15, legend_y, text="Less", font=('Arial', 8), fill='#586069', anchor='e')
        
        for i, color in enumerate(colors):
            square_x = legend_x + i * (square_size + 2)
            canvas.create_rectangle(square_x, legend_y - 5, square_x + square_size, legend_y + 5, 
                                  fill=color, outline=color, width=0)
        
        canvas.create_text(legend_x + len(colors) * (square_size + 2) + 15, legend_y, 
                          text="More", font=('Arial', 8), fill='#586069', anchor='w')
        
        total_sessions = sum(data['sessions'] for data in daily_activity.values())
        max_streak = calculate_gaming_streak(daily_activity, start_date, end_date)
        summary_text = f"{total_sessions} sessions in {show_year} • Longest streak: {max_streak} days"
        
        summary_y = legend_y + 25
        canvas.create_text(canvas_width//2, summary_y, text=summary_text, 
                          font=('Arial', 9), fill='#586069')
    
    return {
        'draw_function': draw_heatmap_on_canvas,
        'square_data': square_data,
        'canvas_key': canvas_key
    }


def setup_contributions_tooltip_callback(window, canvas_key='-CONTRIBUTIONS-CANVAS-'):
    """Set up tooltip callback for contributions canvas"""
    tooltip_key = '-CONTRIBUTIONS-TOOLTIP-'
    
    def tooltip_callback(hovered_square):
        try:
            window[tooltip_key].update(visible=False)
        except Exception as e:
            pass
    
    return tooltip_callback