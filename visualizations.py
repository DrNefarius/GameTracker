"""
Visualization functions for the GamesList application.
Handles all chart and graph generation including matplotlib integration.
"""

import os
import io
import tempfile
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
from datetime import timedelta, datetime
from collections import defaultdict

from utilities import format_timedelta_with_seconds

def isolate_matplotlib_env():
    """
    Set up matplotlib to be isolated from the main application's settings.
    This prevents matplotlib from changing the global GUI scale.
    """
    # Reset any global settings
    plt.rcdefaults()
    
    # Use a non-interactive backend to completely isolate from Tkinter
    plt.switch_backend('Agg')  # Agg is a non-interactive backend
    
    # Set fixed DPI and font settings for charts only
    plt.rcParams['figure.dpi'] = 100
    plt.rcParams['savefig.dpi'] = 100
    plt.rcParams['font.size'] = 10
    
    # Make sure any existing figures are closed
    plt.close('all')

def draw_figure(canvas, figure):
    """Draw a matplotlib figure on a PySimpleGUI canvas"""
    figure_canvas_agg = FigureCanvasTkAgg(figure, canvas)
    figure_canvas_agg.draw()
    figure_canvas_agg.get_tk_widget().pack(side='top', fill='both', expand=1)
    return figure_canvas_agg

def create_status_pie_chart(data):
    """Create a pie chart showing game status distribution"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    status_counts = {'Completed': 0, 'In progress': 0, 'Pending': 0}
    
    for entry in data:
        try:
            # Handle different possible data structures
            if isinstance(entry, tuple) and len(entry) > 1:
                # Case: (index, [data_list])
                row_data = entry[1]
                if isinstance(row_data, list) and len(row_data) > 4:
                    status = row_data[4]
                else:
                    status = 'Pending'  # Default if can't get status
            elif isinstance(entry, list) and len(entry) > 4:
                # Case: direct data list
                status = entry[4]
            else:
                # Can't determine status
                status = 'Pending'
                
            if status in status_counts:
                status_counts[status] += 1
            else:
                status_counts['Pending'] += 1
        except (IndexError, TypeError):
            # Handle any unexpected data format
            status_counts['Pending'] += 1
    
    # Create figure with fixed dimensions for consistency
    fig, ax = plt.subplots(figsize=(4, 3.5))
    
    # Define colors for each status - using more vibrant colors for better visibility
    colors = ['#5cb85c',  # Vibrant green for Completed
              '#f0ad4e',  # Warm orange for In progress
              '#d9534f']  # Deeper red for Pending
    
    # Create pie chart
    wedges, texts, autotexts = ax.pie(
        status_counts.values(), 
        labels=status_counts.keys(),
        autopct='%1.1f%%', 
        startangle=90,
        colors=colors,
        textprops={'fontsize': 10}  # Fixed font size for labels
    )
    
    # Make the autopct text readable
    for autotext in autotexts:
        autotext.set_fontsize(9)  # Fixed font size for percentage values
    
    # Equal aspect ratio ensures that pie is drawn as a circle
    ax.axis('equal')
    plt.title('Game Status Distribution', fontsize=12)  # Fixed font size for title
    
    # Save to a bytes buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf

def create_year_bar_chart(data):
    """Create a bar chart showing games by release year"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    year_data = defaultdict(lambda: {'Completed': 0, 'In progress': 0, 'Pending': 0})
    
    for entry in data:
        try:
            # Handle different possible data structures
            if isinstance(entry, tuple) and len(entry) > 1:
                # Case: (index, [data_list])
                row_data = entry[1]
                if isinstance(row_data, list) and len(row_data) > 4:
                    release_date = row_data[1]
                    status = row_data[4]
                else:
                    continue  # Skip if invalid data
            elif isinstance(entry, list) and len(entry) > 4:
                # Case: direct data list
                release_date = entry[1]
                status = entry[4]
            else:
                continue  # Skip if invalid data
                
            # Extract year from release date
            if release_date and release_date != '-':
                try:
                    year = release_date.split('-')[0]
                    if status in ['Completed', 'In progress', 'Pending']:
                        year_data[year][status] += 1
                    else:
                        # Default to Pending for any other status
                        year_data[year]['Pending'] += 1
                except (IndexError, AttributeError):
                    continue
        except (IndexError, TypeError):
            continue  # Skip invalid entries
    
    # Sort years
    sorted_years = sorted(year_data.keys())
    
    # Create lists for plotting
    years = []
    completed = []
    in_progress = []
    pending = []
    
    # Only include years with data
    for year in sorted_years:
        if year_data[year]['Completed'] > 0 or year_data[year]['In progress'] > 0 or year_data[year]['Pending'] > 0:
            years.append(year)
            completed.append(year_data[year]['Completed'])
            in_progress.append(year_data[year]['In progress'])
            pending.append(year_data[year]['Pending'])
    
    # Create figure with fixed dimensions
    fig, ax = plt.subplots(figsize=(7.5, 3))
    
    # Set position of bar on X axis
    x = np.arange(len(years))
    width = 0.25  # Narrower bars to fit three categories
    
    # Create bars with more visible colors
    ax.bar(x - width, completed, width, label='Completed', color='#5cb85c')  # Green
    ax.bar(x, in_progress, width, label='In progress', color='#f0ad4e')  # Orange/Yellow
    ax.bar(x + width, pending, width, label='Pending', color='#d9534f')  # Red
    
    # Add labels and title with fixed font sizes
    ax.set_xlabel('Release Year', fontsize=10)
    ax.set_ylabel('Number of Games', fontsize=10)
    ax.set_title('Games by Release Year and Status', fontsize=12)
    ax.set_xticks(x)
    ax.set_xticklabels(years, rotation=45, fontsize=8)
    ax.tick_params(axis='both', which='major', labelsize=8)
    ax.legend(fontsize=8)
    
    plt.tight_layout()
    
    # Save to a bytes buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf

def create_playtime_distribution(data):
    """Create a chart showing playtime distribution among top games"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    # Collect playtime data
    playtime_data = []
    
    for entry in data:
        try:
            # Handle different possible data structures
            if isinstance(entry, tuple) and len(entry) > 1:
                # Case: (index, [data_list])
                row_data = entry[1]
                if isinstance(row_data, list) and len(row_data) > 3:
                    name = row_data[0]
                    time_str = row_data[3]
                else:
                    continue  # Skip if invalid data
            elif isinstance(entry, list) and len(entry) > 3:
                # Case: direct data list
                name = entry[0]
                time_str = entry[3]
            else:
                continue  # Skip if invalid data
            
            if time_str and time_str not in ['00:00:00', '00:00', '']:
                try:
                    parts = time_str.split(':')
                    if len(parts) == 3:
                        h, m, s = map(int, parts)
                        seconds = h * 3600 + m * 60 + s
                    elif len(parts) == 2:
                        h, m = map(int, parts)
                        seconds = h * 3600 + m * 60
                    else:
                        continue
                    
                    # Only include if there's actual playtime
                    if seconds > 0:
                        playtime_data.append((name, seconds))
                except (ValueError, IndexError):
                    continue
        except (IndexError, TypeError):
            continue  # Skip invalid entries
    
    # Sort by playtime (descending)
    playtime_data.sort(key=lambda x: x[1], reverse=True)
    
    # Take top 10 games
    top_games = playtime_data[:10]
    
    # Create figure with fixed dimensions
    fig, ax = plt.subplots(figsize=(5, 4))
    
    if top_games:
        # Extract data for plotting - use shorter names
        names = [item[0][:20] + '...' if len(item[0]) > 20 else item[0] for item in top_games]
        times = [item[1] / 3600 for item in top_games]  # Convert to hours
        
        # Create horizontal bar chart with a more visible color
        bars = ax.barh(names, times, color='#6f42c1')  # Stronger purple
        
        # Add labels and title
        ax.set_xlabel('Hours Played', fontsize=10)
        ax.set_title('Top Games by Playtime', fontsize=12)
        
        # Set axis label sizes
        ax.tick_params(axis='both', which='major', labelsize=8)
        
        # Add time labels to bars
        for i, bar in enumerate(bars):
            hours = int(times[i])
            minutes = int((times[i] - hours) * 60)
            if hours > 0:
                time_label = f"{hours}h {minutes}m"
            else:
                time_label = f"{minutes}m"
            ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2, 
                   time_label, va='center', fontsize=8)
    else:
        ax.text(0.5, 0.5, "No play time data available", 
                ha='center', va='center', fontsize=10)
        ax.set_title('Top Games by Playtime', fontsize=12)
    
    plt.tight_layout()
    
    # Save to a bytes buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf

def create_rating_distribution_chart(data):
    """Create a chart showing distribution of game ratings"""
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    # Collect rating data
    rating_data = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    rated_games = 0
    
    for entry in data:
        try:
            # Handle different possible data structures
            if isinstance(entry, tuple) and len(entry) > 1:
                # Case: (index, [data_list])
                row_data = entry[1]
                if isinstance(row_data, list) and len(row_data) > 9:
                    rating = row_data[9]
                else:
                    continue  # Skip if invalid data
            else:
                continue  # Skip if invalid data
            
            if rating and isinstance(rating, dict) and 'stars' in rating:
                stars = int(rating['stars'])
                if 1 <= stars <= 5:
                    rating_data[stars] += 1
                    rated_games += 1
        except (IndexError, TypeError, ValueError):
            continue  # Skip invalid entries
    
    # Create figure with fixed dimensions
    fig, ax = plt.subplots(figsize=(5, 4))
    
    if rated_games > 0:
        # Extract data for plotting
        star_counts = list(rating_data.values())
        star_labels = [f"{i} ★" for i in range(1, 6)]
        
        # Create bar chart with gradient color based on rating
        colors = ['#ffcccc', '#ffdab3', '#ffffb3', '#c2e0c6', '#a3d977']  # Red to green gradient
        bars = ax.bar(star_labels, star_counts, color=colors)
        
        # Remove the value labels on top of bars for cleaner appearance
        # (Commented out to make the chart cleaner)
        # for bar in bars:
        #     height = bar.get_height()
        #     ax.text(bar.get_x() + bar.get_width() / 2., height + 0.1,
        #            f'{height:.0f}', ha='center', va='bottom', fontsize=9)
        
        # Add labels and title
        ax.set_xlabel('Rating', fontsize=10)
        ax.set_ylabel('Number of Games', fontsize=10)
        ax.set_title(f'Game Ratings Distribution (Total: {rated_games} games)', fontsize=12)
        
        # Add average rating text with better positioning to avoid overlap
        avg_rating = sum(i * rating_data[i] for i in range(1, 6)) / rated_games if rated_games else 0
        ax.text(0.5, -0.25, f"Average Rating: {avg_rating:.2f} ★", 
               ha='center', va='center', transform=ax.transAxes, fontsize=10, fontweight='bold')
        
    else:
        ax.text(0.5, 0.5, "No rating data available", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Game Ratings Distribution", fontsize=12)
    
    # Adjust layout to provide more space for the average rating text
    plt.subplots_adjust(bottom=0.25)  # Add more bottom margin
    plt.tight_layout()
    
    # Save to a buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf

def update_summary_charts(data_with_indices):
    """Update all charts in the Summary tab"""
    try:
        # Save the current matplotlib settings to restore later
        original_backend = plt.get_backend()
        
        # Create temporary files for each chart
        temp_dir = tempfile.gettempdir()
        pie_chart_file = os.path.join(temp_dir, 'pie_chart_temp.png')
        year_chart_file = os.path.join(temp_dir, 'year_chart_temp.png')
        playtime_chart_file = os.path.join(temp_dir, 'playtime_chart_temp.png')
        rating_chart_file = os.path.join(temp_dir, 'rating_chart_temp.png')
        
        # Use the create_X functions but save to files
        pie_data = create_status_pie_chart(data_with_indices)
        with open(pie_chart_file, 'wb') as f:
            f.write(pie_data.getvalue())
            
        year_data = create_year_bar_chart(data_with_indices)
        with open(year_chart_file, 'wb') as f:
            f.write(year_data.getvalue())
            
        playtime_data = create_playtime_distribution(data_with_indices)
        with open(playtime_chart_file, 'wb') as f:
            f.write(playtime_data.getvalue())
            
        rating_data = create_rating_distribution_chart(data_with_indices)
        with open(rating_chart_file, 'wb') as f:
            f.write(rating_data.getvalue())
        
        # Close all matplotlib figures and reset to avoid affecting PySimpleGUI
        plt.close('all')
        plt.rcdefaults()
        try:
            plt.switch_backend(original_backend)
        except:
            pass
        
        return {
            'pie_chart': pie_chart_file,
            'year_chart': year_chart_file,
            'playtime_chart': playtime_chart_file,
            'rating_chart': rating_chart_file
        }
            
    except Exception as e:
        print(f"Error updating charts: {str(e)}")
        return None 