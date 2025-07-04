"""
Ratings functionality for the GamesList application.
Handles game and session ratings, including display, calculation, and editing.
"""

import PySimpleGUI as sg
from datetime import datetime
from collections import Counter

from constants import STAR_FILLED, STAR_EMPTY, RATING_TAGS, NEGATIVE_TAGS, NEUTRAL_TAGS, POSITIVE_TAGS

def format_rating(rating):
    """Format a rating as stars (1-5)"""
    if rating is None:
        return ""
    try:
        stars = int(rating.get('stars', 0))
        return STAR_FILLED * stars + STAR_EMPTY * (5 - stars)
    except (ValueError, TypeError, AttributeError):
        return ""

def calculate_session_rating_average(sessions):
    """Calculate the average rating from sessions that have ratings"""
    # Look for ratings in the new unified feedback format
    rated_sessions = [s for s in sessions if 'feedback' in s and s['feedback'] and 'rating' in s['feedback'] and s['feedback']['rating'] is not None]
    if not rated_sessions:
        return None
    
    # Calculate weighted average based on session duration
    total_weight = 0
    weighted_sum = 0
    
    for session in rated_sessions:
        try:
            # Get the star rating from the unified feedback structure
            stars = session['feedback']['rating'].get('stars', 0)
            
            # Get duration weight - newer sessions get higher weight
            if 'duration' in session:
                duration_str = session['duration']
                parts = duration_str.split(':')
                if len(parts) == 3:
                    h, m, s = map(int, parts)
                    duration_mins = h * 60 + m + s/60
                    weight = max(1, duration_mins / 30)  # At least weight of 1, otherwise scale by minutes
                    
                    weighted_sum += stars * weight
                    total_weight += weight
        except (ValueError, TypeError, AttributeError):
            continue
    
    if total_weight > 0:
        return round(weighted_sum / total_weight, 1)
    return None

def get_session_rating_summary(sessions):
    """Get a summary of session ratings including most common tags"""
    # Look for ratings in the unified feedback format
    rated_sessions = [s for s in sessions if 'feedback' in s and s['feedback'] and 'rating' in s['feedback'] and s['feedback']['rating'] is not None]
    if not rated_sessions:
        return None
    
    # Calculate average rating
    average_rating = calculate_session_rating_average(sessions)
    if average_rating is None:
        return None
    
    # Collect all tags from session ratings
    all_tags = []
    for session in rated_sessions:
        try:
            tags = session['feedback']['rating'].get('tags', [])
            all_tags.extend(tags)
        except (ValueError, TypeError, AttributeError):
            continue
    
    # Count tag frequency
    tag_counter = Counter(all_tags)
    most_common_tags = [tag for tag, count in tag_counter.most_common(5)]  # Top 5 most common tags
    
    return {
        'average_stars': int(round(average_rating)),
        'exact_average': average_rating,
        'total_rated_sessions': len(rated_sessions),
        'most_common_tags': most_common_tags
    }

def show_rating_popup(existing_rating=None, parent_window=None):
    """Show a popup for rating a game or session"""
    is_edit = existing_rating is not None
    
    if existing_rating is None:
        existing_rating = {'stars': 0, 'tags': [], 'comment': ''}
    
    existing_stars = int(existing_rating.get('stars', 0))  # Convert to int to handle float values
    existing_tags = existing_rating.get('tags', [])
    existing_comment = existing_rating.get('comment', '')
    
    # Create a layout with star rating, tags, and comment
    layout = [
        [sg.Text(f"{'Edit' if is_edit else 'Enter'} rating (1-5 stars):")],
        [sg.Text(STAR_FILLED * existing_stars + STAR_EMPTY * (5 - existing_stars), key='-STARS-', font=('Arial', 20))],
        [sg.Slider(range=(1, 5), default_value=existing_stars if existing_stars > 0 else 3, 
                 orientation='h', size=(30, 15), key='-RATING-', enable_events=True)],
        [sg.Text("Select tags that describe your experience (optional):")],
        [sg.Frame("Negative", [
            [sg.Column([[
                sg.Checkbox(tag, default=tag in existing_tags, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1)) 
                for tag in NEGATIVE_TAGS[:5]
            ]], vertical_alignment='top')],
            [sg.Column([[
                sg.Checkbox(tag, default=tag in existing_tags, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1)) 
                for tag in NEGATIVE_TAGS[5:]
            ]], vertical_alignment='top')]
        ], font=('Arial', 9), relief=sg.RELIEF_SUNKEN, pad=((5, 5), (2, 2)))],
        [sg.Frame("Neutral", [
            [sg.Column([[
                sg.Checkbox(tag, default=tag in existing_tags, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1)) 
                for tag in NEUTRAL_TAGS[:5]
            ]], vertical_alignment='top')],
            [sg.Column([[
                sg.Checkbox(tag, default=tag in existing_tags, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1)) 
                for tag in NEUTRAL_TAGS[5:]
            ]], vertical_alignment='top')]
        ], font=('Arial', 9), relief=sg.RELIEF_SUNKEN, pad=((5, 5), (2, 2)))],
        [sg.Frame("Positive", [
            [sg.Column([[
                sg.Checkbox(tag, default=tag in existing_tags, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1)) 
                for tag in POSITIVE_TAGS[:5]
            ]], vertical_alignment='top')],
            [sg.Column([[
                sg.Checkbox(tag, default=tag in existing_tags, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1)) 
                for tag in POSITIVE_TAGS[5:10]
            ]], vertical_alignment='top')],
            [sg.Column([[
                sg.Checkbox(tag, default=tag in existing_tags, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1)) 
                for tag in POSITIVE_TAGS[10:15]
            ]], vertical_alignment='top')],
            [sg.Column([[
                sg.Checkbox(tag, default=tag in existing_tags, key=f'-TAG-{RATING_TAGS.index(tag)}-', size=(11, 1)) 
                for tag in POSITIVE_TAGS[15:]
            ]], vertical_alignment='top')]
        ], font=('Arial', 9), relief=sg.RELIEF_SUNKEN, pad=((5, 5), (2, 2)))],
        [sg.Text("Comments (optional):")],
        [sg.Multiline(default_text=existing_comment, size=(40, 3), key='-COMMENT-')],
        [sg.Button('OK'), sg.Button('Cancel')]
    ]
    
    # Calculate center position relative to parent window
    popup_location = None
    if parent_window:
        from utilities import calculate_popup_center_location
        popup_location = calculate_popup_center_location(parent_window, popup_width=500, popup_height=400)
    
    popup = sg.Window(f"{'Edit' if is_edit else 'Add'} Rating", layout, modal=True, icon='gameslisticon.ico', finalize=True, location=popup_location)
    
    # Update stars display when slider moves
    while True:
        event, values = popup.read()
        if event in (sg.WIN_CLOSED, 'Cancel'):
            popup.close()
            return None
        elif event == '-RATING-':
            # Update the stars display
            stars = int(values['-RATING-'])
            popup['-STARS-'].update(STAR_FILLED * stars + STAR_EMPTY * (5 - stars))
        elif event == 'OK':
            # Get the rating values
            stars = int(values['-RATING-'])
            tags = []
            for i, tag in enumerate(RATING_TAGS):
                if values[f'-TAG-{i}-']:
                    tags.append(tag)
            
            comment = values['-COMMENT-'].strip()
            
            # Create the rating object
            rating = {
                'stars': stars,
                'tags': tags,
                'comment': comment,
                'timestamp': datetime.now().isoformat()
            }
            
            popup.close()
            return rating 