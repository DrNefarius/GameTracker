"""
Emoji rendering utilities for PySimpleGUI applications.
Provides cross-platform emoji rendering using Pillow (PIL) with font fallbacks.
"""

import os
import sys
import platform
import base64
import re
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import PySimpleGUI as sg

class EmojiRenderer:
    """Handles emoji rendering with caching and font management"""
    
    def __init__(self):
        self.cache = {}
        self.emoji_font = None
        self._find_emoji_font()
    
    def _find_emoji_font(self):
        """Find the best available emoji font for the current platform"""
        system = platform.system().lower()
        
        # Platform-specific emoji font paths
        font_paths = []
        
        if system == "windows":
            # Windows emoji fonts
            font_paths = [
                "C:/Windows/Fonts/seguiemj.ttf",  # Segoe UI Emoji
                "C:/Windows/Fonts/NotoColorEmoji.ttf",  # Noto Color Emoji (if installed)
            ]
        elif system == "darwin":  # macOS
            # macOS emoji fonts
            font_paths = [
                "/System/Library/Fonts/Apple Color Emoji.ttc",
                "/Library/Fonts/Apple Color Emoji.ttc",
            ]
        else:  # Linux and others
            # Linux emoji fonts
            font_paths = [
                "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
                "/usr/share/fonts/TTF/NotoColorEmoji.ttf",
                "/usr/share/fonts/noto/NotoColorEmoji.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Fallback
            ]
        
        # Try to load the first available font
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    # Test loading the font
                    test_font = ImageFont.truetype(font_path, 16)
                    self.emoji_font_path = font_path
                    print(f"Found emoji font: {font_path}")
                    return
                except Exception as e:
                    print(f"Could not load font {font_path}: {e}")
                    continue
        
        # If no emoji font found, use default
        print("No emoji font found, using default font")
        self.emoji_font_path = None
    
    def render_emoji(self, emoji_char, size=16, bg_color=(255, 255, 255, 0)):
        """Render an emoji character to a PIL Image"""
        cache_key = (emoji_char, size, bg_color)
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Create image
        img = Image.new('RGBA', (size, size), bg_color)
        draw = ImageDraw.Draw(img)
        
        try:
            if self.emoji_font_path:
                # Try to use emoji font - use slightly smaller font to ensure it fits
                font_size = max(8, int(size * 0.85))  # Use 85% of the target size
                font = ImageFont.truetype(self.emoji_font_path, font_size)
                
                # Special handling for problematic emojis
                emoji_adjustments = {
                    '⏱️': {'x_offset': 1, 'y_offset': 0},  # Stopwatch - shift right slightly
                    '⭐': {'x_offset': 0, 'y_offset': 1},   # Star - shift down slightly
                    '🏆': {'x_offset': 0, 'y_offset': 1},   # Trophy - shift down slightly
                    '👑': {'x_offset': 0, 'y_offset': 1},   # Crown - shift down slightly
                    '⏰': {'x_offset': 1, 'y_offset': 0},   # Alarm clock - shift right slightly
                    '⌚': {'x_offset': 1, 'y_offset': 0},   # Watch - shift right slightly
                }
                
                # Get the bounding box of the text
                bbox = draw.textbbox((0, 0), emoji_char, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                
                # Calculate position to center the emoji properly
                # Account for the bbox offset (bbox[0] and bbox[1] are the left and top offsets)
                x = (size - text_width) // 2 - bbox[0]
                y = (size - text_height) // 2 - bbox[1]
                
                # Apply special adjustments for problematic emojis
                if emoji_char in emoji_adjustments:
                    adj = emoji_adjustments[emoji_char]
                    x += adj.get('x_offset', 0)
                    y += adj.get('y_offset', 0)
                
                # Ensure the emoji doesn't go outside bounds with some padding
                padding = 1
                x = max(padding, min(x, size - text_width - padding))
                y = max(padding, min(y, size - text_height - padding))
                
                # Draw the emoji
                draw.text((x, y), emoji_char, font=font, fill=(0, 0, 0, 255))
            else:
                # Fallback: create a colored rectangle with the first letter
                fallback_char = emoji_char[0] if emoji_char else "?"
                
                # Use default font
                try:
                    font = ImageFont.load_default()
                except:
                    font = None
                
                # Draw a colored background
                colors = [
                    (255, 100, 100),  # Red
                    (100, 255, 100),  # Green
                    (100, 100, 255),  # Blue
                    (255, 255, 100),  # Yellow
                    (255, 100, 255),  # Magenta
                    (100, 255, 255),  # Cyan
                ]
                color_index = hash(emoji_char) % len(colors)
                bg_color = colors[color_index]
                
                # Draw colored rectangle
                draw.rectangle([0, 0, size-1, size-1], fill=bg_color, outline=(0, 0, 0))
                
                # Draw fallback character
                if font:
                    bbox = draw.textbbox((0, 0), fallback_char, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                    
                    # Better centering for fallback text
                    x = (size - text_width) // 2 - bbox[0]
                    y = (size - text_height) // 2 - bbox[1]
                    
                    # Ensure text stays within bounds
                    x = max(0, min(x, size - text_width))
                    y = max(0, min(y, size - text_height))
                    
                    draw.text((x, y), fallback_char, font=font, fill=(255, 255, 255))
        
        except Exception as e:
            print(f"Error rendering emoji '{emoji_char}': {e}")
            # Ultimate fallback: just a colored square
            colors = [(255, 100, 100), (100, 255, 100), (100, 100, 255)]
            color = colors[hash(emoji_char) % len(colors)]
            draw.rectangle([0, 0, size-1, size-1], fill=color)
        
        # Cache and return
        self.cache[cache_key] = img
        return img
    
    def emoji_to_base64(self, emoji_char, size=16):
        """Convert emoji to base64 string for PySimpleGUI Image element"""
        img = self.render_emoji(emoji_char, size)
        
        # Convert to PNG bytes
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        # Encode to base64
        img_base64 = base64.b64encode(buffer.getvalue()).decode()
        return img_base64

# Global renderer instance
_renderer = EmojiRenderer()

def clear_emoji_cache():
    """Clear the emoji rendering cache to force re-rendering"""
    global _renderer
    _renderer.cache.clear()
    print("Emoji cache cleared")

def emoji_image(emoji_char, size=16):
    """Create a PySimpleGUI Image element with rendered emoji"""
    try:
        img_base64 = _renderer.emoji_to_base64(emoji_char, size)
        return sg.Image(data=img_base64, size=(size, size))
    except Exception as e:
        print(f"Error creating emoji image for '{emoji_char}': {e}")
        # Return a simple text element as fallback
        return sg.Text(emoji_char, font=('Arial', size//2))

def get_emoji(name):
    """Get emoji character by name"""
    emoji_dict = {
        # Common emojis used in the application
        'game': '🎮',
        'time': '⏱️',
        'chart': '📊',
        'stats': '📈',
        'light_bulb': '💡',
        'star': '⭐',
        'tools': '🔧',
        'file': '📁',
        'bug': '🐛',
        'search': '🔍',
        'email': '📧',
        'rocket': '🚀',
        'handshake': '🤝',
        'lightning': '⚡',
        'pray': '🙏',
        'dev': '👨‍💻',
        'chat': '💬',
        'support': '🛠️',
        'community': '👥',
        'crystal_ball': '🔮',
        'book': '📖',
        
        # Additional useful emojis
        'check': '✅',
        'cross': '❌',
        'warning': '⚠️',
        'info': 'ℹ️',
        'heart': '❤️',
        'thumbs_up': '👍',
        'thumbs_down': '👎',
        'fire': '🔥',
        'trophy': '🏆',
        'medal': '🏅',
        'target': '🎯',
        'calendar': '📅',
        'clock': '🕐',
        'folder': '📂',
        'gear': '⚙️',
        'wrench': '🔧',
        'hammer': '🔨',
        'key': '🔑',
        'lock': '🔒',
        'unlock': '🔓',
        'shield': '🛡️',
        'sword': '⚔️',
        'bow': '🏹',
        'magic': '✨',
        'diamond': '💎',
        'gem': '💍',
        'crown': '👑',
        'joystick': '🕹️',
        'computer': '💻',
        'mouse': '🖱️',
        'keyboard': '⌨️',
        'headphones': '🎧',
        'microphone': '🎤',
        'speaker': '🔊',
        'volume': '🔉',
        'mute': '🔇',
        'battery': '🔋',
        'plug': '🔌',
        'wifi': '📶',
        'signal': '📡',
        'satellite': '🛰️',
        'globe': '🌍',
        'map': '🗺️',
        'compass': '🧭',
        'telescope': '🔭',
        'microscope': '🔬',
        'test_tube': '🧪',
        'dna': '🧬',
        'atom': '⚛️',
        'magnet': '🧲',
        'battery_low': '🪫',
        'floppy': '💾',
        'cd': '💿',
        'dvd': '📀',
        'camera': '📷',
        'video': '📹',
        'film': '🎬',
        'tv': '📺',
        'radio': '📻',
        'phone': '📱',
        'telephone': '☎️',
        'pager': '📟',
        'fax': '📠',
        'printer': '🖨️',
        'scanner': '🖨️',
        'desktop': '🖥️',
        'laptop': '💻',
        'tablet': '📱',
        'watch': '⌚',
        'stopwatch': '⏱️',
        'timer': '⏲️',
        'alarm': '⏰',
        'hourglass': '⏳',
        'sand': '⌛',
    }
    
    return emoji_dict.get(name, '❓')  # Return question mark if emoji not found

def render_emoji_text(text, size=16):
    """Render text that may contain emoji names in {emoji_name} format"""
    
    # Find all {emoji_name} patterns
    pattern = r'\{([^}]+)\}'
    matches = re.findall(pattern, text)
    
    # Replace each match with actual emoji
    result_text = text
    for match in matches:
        emoji_char = get_emoji(match)
        result_text = result_text.replace(f'{{{match}}}', emoji_char)
    
    return result_text

# Convenience functions
def create_emoji_button(emoji_name, button_text="", size=16, **kwargs):
    """Create a button with an emoji image"""
    emoji_char = get_emoji(emoji_name)
    try:
        img_base64 = _renderer.emoji_to_base64(emoji_char, size)
        return sg.Button(button_text, image_data=img_base64, **kwargs)
    except Exception as e:
        print(f"Error creating emoji button for '{emoji_name}': {e}")
        return sg.Button(f"{emoji_char} {button_text}", **kwargs)

def create_emoji_text(emoji_name, text="", size=16, **kwargs):
    """Create a text element with emoji and text"""
    emoji_char = get_emoji(emoji_name)
    full_text = f"{emoji_char} {text}" if text else emoji_char
    return sg.Text(full_text, **kwargs)

def emoji_text_with_images(text_parts, size=16):
    """Create a row of text and emoji images from a list of parts"""
    elements = []
    for part in text_parts:
        if isinstance(part, dict) and 'emoji' in part:
            # This is an emoji specification
            emoji_char = get_emoji(part['emoji'])
            elements.append(emoji_image(emoji_char, size))
            if 'text' in part:
                elements.append(sg.Text(part['text']))
        else:
            # This is regular text
            elements.append(sg.Text(str(part)))
    return elements 