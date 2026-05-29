"""
Visualization functions for the GamesList application.
Handles all chart and graph generation including matplotlib integration.
"""

import os
import io
import tempfile
from contextlib import contextmanager
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import numpy as np
from datetime import timedelta, datetime
from collections import defaultdict, Counter

from utilities import format_timedelta_with_seconds, iter_game_rows

# Fixed rcParams for all chart output. Previously `isolate_matplotlib_env` set
# these globally via plt.rcdefaults() + plt.switch_backend('Agg'), which mutated
# process-wide state and could clobber matplotlib settings used elsewhere (or in
# the host app's own Tk UI). We now apply them inside a scoped rc_context and
# build figures via Figure + FigureCanvasAgg so nothing leaks.
_CHART_RC = {
    'figure.dpi': 100,
    'savefig.dpi': 100,
    'font.size': 10,
}


@contextmanager
def chart_rc_scope():
    """Apply our chart rcParams temporarily, leaving global matplotlib state intact."""
    with matplotlib.rc_context(_CHART_RC):
        yield


def new_chart_figure(figsize):
    """Create a Figure wired to an Agg canvas so savefig works regardless of backend.
    
    Using Figure()+FigureCanvasAgg instead of plt.subplots() avoids registering the
    figure with pyplot's global figure manager (no GC leak if a caller forgets
    plt.close(fig)).
    """
    fig = Figure(figsize=figsize)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(1, 1, 1)
    return fig, ax


def _unique_chart_labels(names, max_len=20):
    """Shorten long names for a chart axis without two entries colliding.

    Games that share a long common prefix - e.g. 'The Legend of Zelda: Breath
    of the Wild' and 'The Legend of Zelda: Tears of the Kingdom', or several
    'Xenoblade Chronicles ...' titles - head-truncate to identical strings.
    Identical labels are a problem on a categorical bar axis: matplotlib maps
    equal labels to the SAME position, stacking the bars (and their value
    labels) on top of each other. When a head-truncation collides we fall back
    to a head+tail (middle ellipsis) form that keeps the distinguishing end of
    the name.
    """
    def head(n):
        return n if len(n) <= max_len else n[:max_len] + '...'

    def middle(n):
        if len(n) <= max_len:
            return n
        head_len = max(1, max_len * 2 // 3)
        tail_len = max(1, max_len - head_len)
        return n[:head_len] + '...' + n[-tail_len:]

    labels = [head(n) for n in names]
    collisions = {lab for lab, count in Counter(labels).items() if count > 1}
    return [middle(orig) if lab in collisions else lab
            for orig, lab in zip(names, labels)]


# Legacy helper: still used by session_visualizations.py and session_management.py
# which build figures via plt.subplots(). They rely on the Agg backend + clean
# rcParams being applied before they draw. New code should prefer
# chart_rc_scope() + new_chart_figure() instead, which don't touch globals.
def isolate_matplotlib_env():
    plt.rcdefaults()
    try:
        plt.switch_backend('Agg')
    except Exception as e:
        print(f"isolate_matplotlib_env: could not switch to Agg backend ({e}); continuing")
    for k, v in _CHART_RC.items():
        plt.rcParams[k] = v
    plt.close('all')

def create_status_pie_chart(data):
    """Create a pie chart showing game status distribution"""
    status_counts = {'Completed': 0, 'Dropped': 0, 'In progress': 0, 'Pending': 0}
    
    for row in iter_game_rows(data):
        try:
            status = row[4] if len(row) > 4 else 'Pending'
        except (IndexError, TypeError):
            status = 'Pending'
        if status in status_counts:
            status_counts[status] += 1
        else:
            status_counts['Pending'] += 1
    
    with chart_rc_scope():
        fig, ax = new_chart_figure((4, 3.5))
        colors = ['#5cb85c', '#8e8e8e', '#f0ad4e', '#d9534f']
        if sum(status_counts.values()) == 0:
            ax.text(0.5, 0.5, "No game status data available",
                    ha='center', va='center', fontsize=10, transform=ax.transAxes)
            ax.axis('off')
            ax.set_title('Game Status Distribution', fontsize=12)
        else:
            _, _, autotexts = ax.pie(
                status_counts.values(),
                labels=status_counts.keys(),
                autopct='%1.1f%%',
                startangle=90,
                colors=colors,
                textprops={'fontsize': 10}
            )
            for autotext in autotexts:
                autotext.set_fontsize(9)
            ax.axis('equal')
            ax.set_title('Game Status Distribution', fontsize=12)
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        return buf

def create_year_bar_chart(data):
    """Create a bar chart showing games by release year"""
    year_data = defaultdict(lambda: {'Completed': 0, 'Dropped': 0, 'In progress': 0, 'Pending': 0})
    
    for row in iter_game_rows(data):
        if len(row) <= 4:
            continue
        release_date = row[1]
        status = row[4]
        if release_date and release_date != '-':
            try:
                year = release_date.split('-')[0]
                if status in ('Completed', 'Dropped', 'In progress', 'Pending'):
                    year_data[year][status] += 1
                else:
                    year_data[year]['Pending'] += 1
            except (IndexError, AttributeError):
                continue
    
    # Sort years
    sorted_years = sorted(year_data.keys())
    
    # Create lists for plotting
    years = []
    completed = []
    dropped = []
    in_progress = []
    pending = []
    
    # Only include years with data
    for year in sorted_years:
        if (year_data[year]['Completed'] > 0 or year_data[year]['Dropped'] > 0
                or year_data[year]['In progress'] > 0 or year_data[year]['Pending'] > 0):
            years.append(year)
            completed.append(year_data[year]['Completed'])
            dropped.append(year_data[year]['Dropped'])
            in_progress.append(year_data[year]['In progress'])
            pending.append(year_data[year]['Pending'])
    
    with chart_rc_scope():
        fig, ax = new_chart_figure((7.5, 3))
        x = np.arange(len(years))
        width = 0.2
        ax.bar(x - 1.5 * width, completed, width, label='Completed', color='#5cb85c')
        ax.bar(x - 0.5 * width, dropped, width, label='Dropped', color='#8e8e8e')
        ax.bar(x + 0.5 * width, in_progress, width, label='In progress', color='#f0ad4e')
        ax.bar(x + 1.5 * width, pending, width, label='Pending', color='#d9534f')
        ax.set_xlabel('Release Year', fontsize=10)
        ax.set_ylabel('Number of Games', fontsize=10)
        ax.set_title('Games by Release Year and Status', fontsize=12)
        ax.set_xticks(x)
        ax.set_xticklabels(years, rotation=45, fontsize=8)
        ax.tick_params(axis='both', which='major', labelsize=8)
        ax.legend(fontsize=8)
        fig.tight_layout()
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        return buf

def create_playtime_distribution(data):
    """Create a chart showing playtime distribution among top games"""
    # Collect playtime data
    playtime_data = []
    
    for row in iter_game_rows(data):
        if len(row) <= 3:
            continue
        name = row[0]
        time_str = row[3]
        if time_str and time_str not in ('00:00:00', '00:00', ''):
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
                if seconds > 0:
                    playtime_data.append((name, seconds))
            except (ValueError, IndexError, AttributeError):
                continue
    
    # Sort by playtime (descending)
    playtime_data.sort(key=lambda x: x[1], reverse=True)
    
    # Take top 10 games
    top_games = playtime_data[:10]
    
    with chart_rc_scope():
        fig, ax = new_chart_figure((5, 4))
        if top_games:
            names = _unique_chart_labels([item[0] for item in top_games], max_len=20)
            times = [item[1] / 3600 for item in top_games]
            # Plot against explicit numeric y-positions rather than the names.
            # matplotlib treats bar labels as categories, so two games whose
            # (truncated) names are equal would be drawn at the SAME y-position,
            # stacking the bars and their value labels on top of each other.
            # Explicit positions guarantee exactly one row per game.
            y_pos = list(range(len(top_games)))
            bars = ax.barh(y_pos, times, color='#6f42c1')
            ax.set_yticks(y_pos)
            ax.set_yticklabels(names)
            ax.set_xlabel('Hours Played', fontsize=10)
            ax.set_title('Top Games by Playtime', fontsize=12)
            ax.tick_params(axis='both', which='major', labelsize=8)
            for i, bar in enumerate(bars):
                hours = int(times[i])
                minutes = int((times[i] - hours) * 60)
                time_label = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
                ax.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height() / 2,
                        time_label, va='center', fontsize=8)
        else:
            ax.text(0.5, 0.5, "No play time data available",
                    ha='center', va='center', fontsize=10)
            ax.set_title('Top Games by Playtime', fontsize=12)
        fig.tight_layout()
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        return buf

def create_rating_distribution_chart(data):
    """Create a chart showing distribution of game ratings"""
    # Collect rating data
    rating_data = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    rated_games = 0
    
    for row in iter_game_rows(data):
        if len(row) <= 9:
            continue
        rating = row[9]
        try:
            if rating and isinstance(rating, dict) and 'stars' in rating:
                stars = int(rating['stars'])
                if 1 <= stars <= 5:
                    rating_data[stars] += 1
                    rated_games += 1
        except (TypeError, ValueError):
            continue
    
    with chart_rc_scope():
        fig, ax = new_chart_figure((5, 4))
        if rated_games > 0:
            star_counts = list(rating_data.values())
            star_labels = [f"{i} ★" for i in range(1, 6)]
            colors = ['#ffcccc', '#ffdab3', '#ffffb3', '#c2e0c6', '#a3d977']
            ax.bar(star_labels, star_counts, color=colors)
            ax.set_xlabel('Rating', fontsize=10)
            ax.set_ylabel('Number of Games', fontsize=10)
            ax.set_title(f'Game Ratings Distribution (Total: {rated_games} games)', fontsize=12)
            avg_rating = sum(i * rating_data[i] for i in range(1, 6)) / rated_games if rated_games else 0
            ax.text(0.5, -0.25, f"Average Rating: {avg_rating:.2f} ★",
                    ha='center', va='center', transform=ax.transAxes, fontsize=10, fontweight='bold')
        else:
            ax.text(0.5, 0.5, "No rating data available",
                    ha='center', va='center', fontsize=10)
            ax.set_title("Game Ratings Distribution", fontsize=12)
        fig.subplots_adjust(bottom=0.25)
        fig.tight_layout()
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        return buf

def create_genre_distribution_chart(data):
    """Create a horizontal bar chart of IGDB genres across the library.

    Only games with an IGDB metadata dict (index 10) contribute; games without
    IGDB data are ignored (not counted as "Unknown" so the chart stays meaningful
    even when the library is only partially enriched).
    """
    genre_counts = defaultdict(int)
    games_with_igdb = 0

    for row in iter_game_rows(data):
        if len(row) <= 10:
            continue
        igdb = row[10]
        if not isinstance(igdb, dict):
            continue
        games_with_igdb += 1
        genres = igdb.get('genres') or []
        for g in genres:
            if g and isinstance(g, str):
                genre_counts[g] += 1

    with chart_rc_scope():
        fig, ax = new_chart_figure((6, 4))
        if genre_counts:
            # Top 12 genres keep the chart readable.
            top = sorted(genre_counts.items(), key=lambda kv: kv[1], reverse=True)[:12]
            labels = [k for k, _ in top][::-1]
            values = [v for _, v in top][::-1]
            ax.barh(labels, values, color='#17a2b8')
            ax.set_xlabel('Number of Games', fontsize=10)
            ax.set_title(
                f'Genres Distribution ({games_with_igdb} game{"s" if games_with_igdb != 1 else ""} with IGDB data)',
                fontsize=12,
            )
            ax.tick_params(axis='both', which='major', labelsize=8)
            for i, v in enumerate(values):
                ax.text(v + 0.1, i, str(v), va='center', fontsize=8)
        else:
            ax.text(
                0.5, 0.5,
                "No IGDB metadata yet. Use Options -> Enrich Library from IGDB.",
                ha='center', va='center', fontsize=10, transform=ax.transAxes,
            )
            ax.axis('off')
            ax.set_title('Genres Distribution', fontsize=12)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        return buf


def update_summary_charts(data_with_indices):
    """Update all charts in the Summary tab.
    
    No longer mutates global matplotlib state (backend / rcParams); each chart
    function applies its own scoped rcParams via chart_rc_scope and builds its
    Figure via FigureCanvasAgg directly.
    """
    try:
        # Create temporary files for each chart
        temp_dir = tempfile.gettempdir()
        pie_chart_file = os.path.join(temp_dir, 'pie_chart_temp.png')
        year_chart_file = os.path.join(temp_dir, 'year_chart_temp.png')
        playtime_chart_file = os.path.join(temp_dir, 'playtime_chart_temp.png')
        rating_chart_file = os.path.join(temp_dir, 'rating_chart_temp.png')
        genre_chart_file = os.path.join(temp_dir, 'genre_chart_temp.png')
        
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

        genre_data = create_genre_distribution_chart(data_with_indices)
        with open(genre_chart_file, 'wb') as f:
            f.write(genre_data.getvalue())
        
        return {
            'pie_chart': pie_chart_file,
            'year_chart': year_chart_file,
            'playtime_chart': playtime_chart_file,
            'rating_chart': rating_chart_file,
            'genre_chart': genre_chart_file,
        }
            
    except Exception as e:
        print(f"Error updating charts: {str(e)}")
        return None 