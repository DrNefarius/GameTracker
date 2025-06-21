"""
Session visualization functions.
Handles creation of charts and graphs for session data analysis.
"""

import io
import re
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from visualizations import isolate_matplotlib_env


def create_session_timeline_chart(sessions, game_name=None):
    """Create a timeline chart of gaming sessions"""
    isolate_matplotlib_env()
    
    dates = []
    durations = []
    
    for session in sessions:
        try:
            if 'start' in session and 'duration' in session:
                try:
                    start_date = datetime.fromisoformat(session['start'])
                    duration_str = session['duration']
                    parts = duration_str.split(':')
                    if len(parts) == 3:
                        h, m, s = map(int, parts)
                        duration_hours = h + m/60 + s/3600
                        dates.append(start_date)
                        durations.append(duration_hours)
                except (ValueError, TypeError):
                    continue
        except Exception as e:
            print(f"Error processing session for timeline: {str(e)}")
            continue
    
    fig, ax = plt.subplots(figsize=(7, 2.5))
    
    if dates and durations:
        sorted_data = sorted(zip(dates, durations))
        dates, durations = zip(*sorted_data)
        
        ax.bar(dates, durations, width=0.6, color='#5cb85c')
        
        date_range = max(dates) - min(dates)
        
        if date_range.days <= 7:
            ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%a %m/%d'))
        elif date_range.days <= 31:
            ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%d'))
        elif date_range.days <= 365:
            ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%d'))
            ax.xaxis.set_major_locator(plt.matplotlib.dates.WeekdayLocator(interval=1))
        else:
            ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%y'))
            ax.xaxis.set_major_locator(plt.matplotlib.dates.MonthLocator(interval=1))
        
        plt.xticks(rotation=30, ha='right')
        fig.autofmt_xdate()
        
        title = f"Session Timeline for {game_name}" if game_name else "All Gaming Sessions Timeline"
        ax.set_title(title, fontsize=12)
        ax.set_ylabel('Hours Played', fontsize=10)
        
        plt.tight_layout()
    else:
        ax.text(0.5, 0.5, "No session data available for timeline", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Session Timeline", fontsize=12)
    
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf


def create_session_distribution_chart(sessions, game_name=None, chart_type='line'):
    """Create a chart showing distribution of session lengths
    
    Args:
        sessions: List of gaming sessions
        game_name: Name of the game (optional)
        chart_type: Type of chart to create ('line', 'scatter', 'box', 'histogram')
    """
    # Isolate matplotlib from the main application
    isolate_matplotlib_env()
    
    # Prepare the data
    durations = []
    session_dates = []
    
    for session in sessions:
        try:
            if 'duration' in session and 'start' in session:
                # Parse duration
                duration_str = session['duration']
                parts = duration_str.split(':')
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    duration_minutes = h * 60 + m + s/60
                    durations.append(duration_minutes)
                    
                    # Parse session date for line/scatter plots
                    try:
                        session_date = datetime.fromisoformat(session['start'])
                        session_dates.append(session_date)
                    except:
                        session_dates.append(None)
        except Exception as e:
            print(f"Error processing session for distribution: {str(e)}")
            continue
    
    # Create the chart
    fig, ax = plt.subplots(figsize=(7, 2.5))
    
    if durations:
        # Determine if we should use minutes or hours
        if max(durations) <= 60:  # All sessions under an hour
            y_label = 'Session Length (minutes)'
            duration_values = durations
        else:
            # Convert to hours for better visualization
            y_label = 'Session Length (hours)'
            duration_values = [d/60 for d in durations]
        
        if chart_type == 'line':
            # Create a line chart showing session lengths over time
            if session_dates and all(d is not None for d in session_dates):
                # Sort by date
                sorted_data = sorted(zip(session_dates, duration_values))
                dates, values = zip(*sorted_data)
                
                # Plot line chart
                ax.plot(dates, values, marker='o', markersize=4, linewidth=1.5, 
                       color='#5cb85c', markerfacecolor='#3e8e41', alpha=0.8)
                
                # Add trend line
                if len(dates) > 1:
                    # Convert dates to numbers for trend calculation
                    date_nums = [(d - dates[0]).total_seconds() / (24 * 3600) for d in dates]
                    z = np.polyfit(date_nums, values, 1)
                    p = np.poly1d(z)
                    trend_values = [p(x) for x in date_nums]
                    ax.plot(dates, trend_values, '--', color='red', alpha=0.6, linewidth=2, 
                           label=f'Trend: {"↗" if z[0] > 0 else "↘"} {abs(z[0]):.2f} {y_label.split("(")[1].split(")")[0]}/day')
                    ax.legend(fontsize=8)
                
                # Format x-axis with better readability
                date_range = max(session_dates) - min(session_dates)
                
                if date_range.days <= 7:  # Within a week - show day names
                    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%a %m/%d'))
                elif date_range.days <= 31:  # Within a month - show month/day
                    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%d'))
                elif date_range.days <= 365:  # Within a year - show month/day
                    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%d'))
                    ax.xaxis.set_major_locator(plt.matplotlib.dates.WeekdayLocator(interval=1))
                else:  # More than a year - show month/year
                    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%y'))
                    ax.xaxis.set_major_locator(plt.matplotlib.dates.MonthLocator(interval=1))
                
                plt.xticks(rotation=30, ha='right')
                fig.autofmt_xdate()
            else:
                # Fallback to simple line if no dates
                ax.plot(range(len(duration_values)), duration_values, marker='o', 
                       markersize=4, linewidth=1.5, color='#5cb85c')
                ax.set_xlabel('Session Number', fontsize=10)
            
        elif chart_type == 'scatter':
            # Create a scatter plot showing individual sessions more clearly
            if session_dates and all(d is not None for d in session_dates):
                # Color-code by session length
                scatter = ax.scatter(session_dates, duration_values, c=duration_values, 
                                   cmap='viridis', alpha=0.7, s=50, edgecolor='black', linewidth=0.5)
                
                # Add colorbar
                cbar = plt.colorbar(scatter, ax=ax)
                cbar.set_label(y_label, fontsize=8)
                
                # Format x-axis with better readability
                date_range = max(session_dates) - min(session_dates)
                
                if date_range.days <= 7:  # Within a week - show day names
                    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%a %m/%d'))
                elif date_range.days <= 31:  # Within a month - show month/day
                    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%d'))
                elif date_range.days <= 365:  # Within a year - show month/day
                    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%d'))
                    ax.xaxis.set_major_locator(plt.matplotlib.dates.WeekdayLocator(interval=1))
                else:  # More than a year - show month/year
                    ax.xaxis.set_major_formatter(plt.matplotlib.dates.DateFormatter('%m/%y'))
                    ax.xaxis.set_major_locator(plt.matplotlib.dates.MonthLocator(interval=1))
                
                plt.xticks(rotation=30, ha='right')
                fig.autofmt_xdate()
            else:
                # Fallback scatter without dates
                ax.scatter(range(len(duration_values)), duration_values, alpha=0.7, 
                          c=duration_values, cmap='viridis', s=50, edgecolor='black', linewidth=0.5)
                ax.set_xlabel('Session Number', fontsize=10)
            
        elif chart_type == 'box':
            # Create a box plot showing statistical distribution
            box_plot = ax.boxplot(duration_values, vert=True, patch_artist=True, 
                                 boxprops=dict(facecolor='#6f42c1', alpha=0.7),
                                 medianprops=dict(color='red', linewidth=2))
            
            # Add some statistics as text
            stats_text = f"Mean: {np.mean(duration_values):.1f}\n"
            stats_text += f"Median: {np.median(duration_values):.1f}\n"
            stats_text += f"Std: {np.std(duration_values):.1f}"
            
            ax.text(1.15, np.median(duration_values), stats_text, 
                   verticalalignment='center', fontsize=9,
                   bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue", alpha=0.8))
            
            ax.set_xticklabels(['All Sessions'])
            ax.set_xlabel('Distribution', fontsize=10)
            
        else:  # histogram (original behavior)
            # Create bins for histogram (in minutes)
            if max(durations) <= 60:  # All sessions under an hour
                bins = range(0, int(max(durations)) + 10, 5)  # 5-minute bins
            else:
                # Convert to hours for better visualization
                max_duration = max(duration_values)
                bin_size = max(0.5, max_duration / 10)  # Dynamic bin size
                bins = np.arange(0, max_duration + bin_size, bin_size)
            
            # Plot histogram
            ax.hist(duration_values, bins=bins, color='#6f42c1', edgecolor='black', alpha=0.7)
            ax.set_xlabel(y_label, fontsize=10)
            ax.set_ylabel('Number of Sessions', fontsize=10)
        
        # Common settings for all chart types
        if chart_type != 'histogram':
            ax.set_ylabel(y_label, fontsize=10)
        
        # Add title with chart type information
        chart_type_names = {
            'line': 'Timeline', 
            'scatter': 'Scatter Plot', 
            'box': 'Box Plot', 
            'histogram': 'Distribution'
        }
        
        title = f"Session Length {chart_type_names.get(chart_type, 'Distribution')}"
        if game_name:
            title += f" for {game_name}"
        else:
            title += " for All Games"
        
        ax.set_title(title, fontsize=12)
        
        # Add session count to title
        session_count_text = f"({len(durations)} sessions)"
        ax.text(0.99, 0.95, session_count_text, transform=ax.transAxes, 
               fontsize=9, horizontalalignment='right', verticalalignment='top',
               bbox=dict(boxstyle="round,pad=0.3", facecolor="white", alpha=0.8))
        
        # Adjust for better appearance
        plt.tight_layout()
    else:
        ax.text(0.5, 0.5, "No session data available for distribution analysis", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Session Distribution", fontsize=12)
    
    # Save to a buffer
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf


def create_status_timeline_chart(history, game_name=None):
    """Create a timeline visualization of status changes"""
    isolate_matplotlib_env()
    
    if not history:
        fig, ax = plt.subplots(figsize=(7, 2.5))
        ax.text(0.5, 0.5, "No status change data available", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Status Change Timeline", fontsize=12)
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    timestamps = []
    statuses = []
    colors = []
    
    status_colors = {
        'Pending': '#d9534f',
        'In progress': '#f0ad4e',
        'Completed': '#5cb85c'
    }
    
    for change in sorted(history, key=lambda x: x.get('timestamp', '')):
        try:
            if 'timestamp' in change and 'to' in change:
                timestamp = datetime.fromisoformat(change['timestamp'])
                status = change['to']
                
                timestamps.append(timestamp)
                statuses.append(status)
                colors.append(status_colors.get(status, '#777777'))
        except (ValueError, TypeError):
            continue
    
    fig, ax = plt.subplots(figsize=(7, 3))
    
    if timestamps:
        for i in range(len(timestamps)):
            ax.scatter(timestamps[i], i, color=colors[i], s=100, zorder=3)
            ax.text(timestamps[i], i + 0.1, statuses[i], ha='center', va='bottom', fontsize=9)
            
            if i > 0:
                ax.plot([timestamps[i-1], timestamps[i]], [i-1, i], 'k-', alpha=0.3, zorder=1)
        
        now = datetime.now()
        if timestamps[0] <= now:
            ax.axvline(x=now, color='blue', linestyle='--', alpha=0.5, label='Today')
        
        plt.gcf().autofmt_xdate()
        date_format = plt.matplotlib.dates.DateFormatter('%Y-%m-%d')
        ax.xaxis.set_major_formatter(date_format)
        
        title = f"Status Timeline for {game_name}" if game_name else "Status Change Timeline"
        ax.set_title(title, fontsize=12)
        ax.set_xlabel('Date', fontsize=10)
        ax.set_yticks([])
        
        legend_elements = [
            plt.Line2D([0], [0], marker='o', color='w', label='Pending',
                      markerfacecolor=status_colors['Pending'], markersize=10),
            plt.Line2D([0], [0], marker='o', color='w', label='In progress',
                      markerfacecolor=status_colors['In progress'], markersize=10),
            plt.Line2D([0], [0], marker='o', color='w', label='Completed',
                      markerfacecolor=status_colors['Completed'], markersize=10)
        ]
        ax.legend(handles=legend_elements, loc='upper right', fontsize=8)
        
        plt.tight_layout()
    else:
        ax.text(0.5, 0.5, "No status change data available", 
                ha='center', va='center', fontsize=10)
        ax.set_title("Status Change Timeline", fontsize=12)
    
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf


def calculate_gaming_streak(daily_activity, start_date, end_date):
    """Calculate the longest consecutive gaming streak"""
    max_streak = 0
    current_streak = 0
    
    current_date = start_date
    while current_date <= end_date:
        if current_date in daily_activity:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
        current_date += timedelta(days=1)
    
    return max_streak


def create_comments_word_cloud_visualization(comments):
    """Create a word frequency visualization from rating comments"""
    isolate_matplotlib_env()
    
    if not comments:
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.text(0.5, 0.5, "No rating comments available for analysis", 
                ha='center', va='center', fontsize=12)
        ax.set_title("Rating Comments Analysis", fontsize=14)
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    all_words = []
    for comment_data in comments:
        comment_text = comment_data['comment'].lower()
        words = re.findall(r'\b[a-zA-Z]{3,}\b', comment_text)
        all_words.extend(words)
    
    stop_words = {'the', 'and', 'but', 'for', 'are', 'was', 'were', 'been', 'have', 'has', 'had', 
                  'will', 'would', 'could', 'should', 'can', 'may', 'might', 'must', 'shall',
                  'this', 'that', 'these', 'those', 'with', 'from', 'they', 'them', 'their',
                  'you', 'your', 'yours', 'she', 'her', 'hers', 'his', 'him', 'its', 'our',
                  'ours', 'very', 'just', 'more', 'some', 'all', 'any', 'most', 'much',
                  'many', 'few', 'little', 'big', 'small', 'good', 'bad', 'best', 'worst',
                  'really', 'quite', 'pretty', 'too', 'also', 'only', 'even', 'still',
                  'get', 'got', 'make', 'made', 'take', 'took', 'come', 'came', 'see', 'saw',
                  'know', 'knew', 'think', 'thought', 'say', 'said', 'tell', 'told',
                  'game', 'play', 'played', 'playing', 'games'}
    
    filtered_words = [word for word in all_words if word not in stop_words and len(word) > 2]
    
    if not filtered_words:
        fig, ax = plt.subplots(figsize=(7, 3))
        ax.text(0.5, 0.5, "No meaningful words found in comments for analysis", 
                ha='center', va='center', fontsize=12)
        ax.set_title("Rating Comments Analysis", fontsize=14)
        
        buf = io.BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight')
        buf.seek(0)
        plt.close(fig)
        return buf
    
    word_counts = Counter(filtered_words)
    top_words = word_counts.most_common(15)
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    
    if top_words:
        words, counts = zip(*top_words)
        colors = plt.cm.viridis(np.linspace(0, 1, len(words)))
        bars = ax1.barh(range(len(words)), counts, color=colors)
        ax1.set_yticks(range(len(words)))
        ax1.set_yticklabels(words)
        ax1.set_xlabel('Frequency')
        ax1.set_title('Most Common Words in Comments')
        ax1.invert_yaxis()
        
        for i, (bar, count) in enumerate(zip(bars, counts)):
            ax1.text(bar.get_width() + 0.1, bar.get_y() + bar.get_height()/2, 
                    str(count), va='center', fontsize=9)
    
    # Sentiment analysis
    rating_sentiment = defaultdict(list)
    for comment_data in comments:
        stars = comment_data['stars']
        comment_words = re.findall(r'\b[a-zA-Z]{3,}\b', comment_data['comment'].lower())
        positive_words = {'good', 'great', 'amazing', 'awesome', 'fantastic', 'excellent', 
                         'wonderful', 'brilliant', 'outstanding', 'superb', 'perfect',
                         'love', 'loved', 'enjoy', 'enjoyed', 'fun', 'exciting', 'thrilling'}
        negative_words = {'bad', 'terrible', 'awful', 'horrible', 'boring', 'annoying',
                         'frustrating', 'disappointing', 'worst', 'hate', 'hated', 'sucks',
                         'broken', 'buggy', 'glitchy', 'repetitive', 'tedious'}
        
        positive_count = sum(1 for word in comment_words if word in positive_words)
        negative_count = sum(1 for word in comment_words if word in negative_words)
        sentiment_score = positive_count - negative_count
        rating_sentiment[stars].append(sentiment_score)
    
    avg_sentiment = {}
    for rating, sentiments in rating_sentiment.items():
        if sentiments:
            avg_sentiment[rating] = sum(sentiments) / len(sentiments)
    
    if avg_sentiment:
        ratings = sorted(avg_sentiment.keys())
        sentiments = [avg_sentiment[r] for r in ratings]
        
        colors = ['red' if s < 0 else 'green' if s > 0 else 'gray' for s in sentiments]
        ax2.bar([f"{r}★" for r in ratings], sentiments, color=colors, alpha=0.7)
        ax2.set_ylabel('Average Sentiment Score')
        ax2.set_title('Comment Sentiment by Rating')
        ax2.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        ax2.grid(True, alpha=0.3)
    else:
        ax2.text(0.5, 0.5, "Insufficient data for sentiment analysis", 
                ha='center', va='center', transform=ax2.transAxes)
        ax2.set_title('Comment Sentiment by Rating')
    
    plt.tight_layout()
    
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close(fig)
    
    return buf 