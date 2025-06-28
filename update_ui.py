"""
UI components for the auto-update system.
Provides dialogs and interfaces for managing application updates.
"""

import PySimpleGUI as sg
import threading
import re
import html
import tempfile
import os
import requests
from io import BytesIO
from PIL import Image
from typing import Optional, Dict, Any, List, Tuple

from auto_updater import get_updater
from emoji_utils import emoji_image, get_emoji

def extract_images_from_html(html_content: str) -> List[Tuple[str, str]]:
    """
    Extract image URLs and alt text from HTML content.
    Returns list of (url, alt_text) tuples.
    """
    images = []
    img_pattern = r'<img[^>]*src=["\']([^"\']+)["\'][^>]*(?:alt=["\']([^"\']*)["\'])?[^>]*>'
    
    for match in re.finditer(img_pattern, html_content, re.IGNORECASE):
        url = match.group(1)
        alt_text = match.group(2) if match.group(2) else "Image"
        images.append((url, alt_text))
    
    return images

def download_and_resize_image(url: str, max_width: int = 400, max_height: int = 300) -> Optional[bytes]:
    """
    Download an image from URL and resize it for display.
    Returns image bytes or None if failed.
    """
    try:
        # Download image with shorter timeout for better responsiveness
        response = requests.get(url, timeout=3, stream=True)
        response.raise_for_status()
        
        # Open with PIL
        img = Image.open(BytesIO(response.content))
        
        # Convert to RGB if necessary (for PNG with transparency)
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        
        # Resize while maintaining aspect ratio
        img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        
        # Convert to bytes
        img_bytes = BytesIO()
        img.save(img_bytes, format='PNG')
        return img_bytes.getvalue()
        
    except Exception as e:
        print(f"Failed to download/process image from {url}: {e}")
        return None

def clean_html_to_text(html_content: str) -> str:
    """
    Convert HTML/markdown to clean text, removing HTML tags but preserving structure.
    """
    if not html_content:
        return "No release notes available."
    
    text = html_content
    
    # Remove image tags (we'll handle them separately)
    text = re.sub(r'<img[^>]*>', '', text)
    
    # Convert HTML headers to text headers
    text = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n=== \1 ===\n', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n--- \1 ---\n', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n• \1\n', text, flags=re.IGNORECASE | re.DOTALL)
    
    # Convert markdown headers to text headers (if not already HTML)
    text = re.sub(r'^### (.*)', r'• \1', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.*)', r'--- \1 ---', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.*)', r'=== \1 ===', text, flags=re.MULTILINE)
    
    # Convert HTML formatting
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'"\1"', text, flags=re.IGNORECASE | re.DOTALL)
    
    # Convert markdown formatting (if not already HTML)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # **bold**
    text = re.sub(r'\*(.*?)\*', r'\1', text)      # *italic*
    text = re.sub(r'`(.*?)`', r'"\1"', text)      # `code`
    
    # Convert links
    text = re.sub(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r'\2 (\1)', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 (\2)', text)  # Markdown links
    
    # Convert HTML lists
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'  • \1', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'</?[uo]l[^>]*>', '', text, flags=re.IGNORECASE)
    
    # Remove remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    
    # Clean up HTML entities
    text = html.unescape(text)
    
    # Clean up excessive whitespace
    text = re.sub(r'\n\s*\n\s*\n', '\n\n', text)  # Max 2 consecutive newlines
    text = re.sub(r'^\s+', '', text, flags=re.MULTILINE)  # Remove leading spaces on lines
    text = re.sub(r'\s+$', '', text, flags=re.MULTILINE)  # Remove trailing spaces on lines
    
    # Limit length if too long
    if len(text) > 1000:
        text = text[:1000] + "\n\n[Release notes truncated - full notes available on GitHub]"
    
    return text.strip()

def show_update_notification(update_info: Dict[str, Any]) -> str:
    """
    Show update notification dialog.
    Returns: 'download', 'disable', or 'close'
    """
    current_version = update_info.get('current_version', 'Unknown')
    new_version = update_info.get('version', 'Unknown')
    release_name = update_info.get('name', f'Version {new_version}')
    raw_release_notes = update_info.get('notes', 'No release notes available.')
    
    # Convert HTML/markdown to clean text
    clean_text = clean_html_to_text(raw_release_notes)
    
    # Extract images from the release notes
    images = extract_images_from_html(raw_release_notes)
    
    # Create release notes elements (text only initially)
    release_notes_elements = [
        [sg.Multiline(clean_text, size=(65, 12), disabled=True, no_scrollbar=False, key='-RELEASE-TEXT-')]
    ]
    
    # Show image count and load button if images exist
    images_loaded = False
    loaded_count = 0
    loaded_images_info = []  # Store image data and info together
    if images:
        image_count = len(images)
        release_notes_elements.append([sg.Text("")])  # Spacing
        release_notes_elements.append([
            sg.Text(f"📷 {image_count} image{'s' if image_count != 1 else ''} available in release notes", font=('Arial', 10), text_color='white'),
            sg.Button("Load Images", key="-LOAD-IMAGES-")
        ])
        release_notes_elements.append([sg.Text("", key='-IMAGES-CONTAINER-')])  # Container for loaded images
    
    # Create main layout with scrollable release notes section
    top_section = [
        [sg.Text("🎉 Update Available!", font=('Arial', 14, 'bold'), text_color='#4A90E2')],
        [sg.HorizontalSeparator()],
        [sg.Text(f"Current Version: {current_version}")],
        [sg.Text(f"New Version: {new_version}", font=('Arial', 10, 'bold'))],
        [sg.Text(f"Release: {release_name}")],
        [sg.Text("")]
    ]
    
    # Create scrollable release notes section
    notes_section = [
        [sg.Text("Release Notes:", font=('Arial', 10, 'bold'))],
        [sg.Column(
            release_notes_elements,
            size=(650, 350),
            scrollable=True,
            vertical_scroll_only=True,
            key='-NOTES-COLUMN-'
        )]
    ]
    
    bottom_section = [
        [sg.Text("")],
        [sg.Text("What would you like to do?", font=('Arial', 10, 'bold'))],
        [
            sg.Button("📥 Download & Install", key="-DOWNLOAD-", button_color=('white', '#2E7D4F')),
            sg.Button("🚫 Disable Startup Checks", key="-DISABLE-"),
            sg.Button("✖ Close", key="-CLOSE-")
        ]
    ]
    
    layout = top_section + notes_section + bottom_section
    
    window = sg.Window(
        "GamesList Manager - Update Available", 
        layout, 
        modal=True, 
        icon='gameslisticon.ico',
        element_justification='left',
        size=(700, 650),  # Reduced from 700 to 650 for better proportions
        resizable=True,
        finalize=True
    )
    
    def load_images_synchronously():
        """Load images synchronously when user clicks the button"""
        nonlocal images_loaded, loaded_count, loaded_images_info
        
        if images_loaded:
            return
            
        # Update button to show loading
        window['-LOAD-IMAGES-'].update("Loading...", disabled=True)
        window.refresh()
        
        # Load images and store data with captions
        loaded_images_info = []  # Reset the list
        loaded_count = 0
        
        for i, (img_url, alt_text) in enumerate(images[:3]):  # Limit to 3 images
            caption = f"{alt_text}" if alt_text and alt_text != "Image" else f"Image {i+1}"
            
            # Download and process image
            image_data = download_and_resize_image(img_url, max_width=500, max_height=400)
            
            if image_data:
                loaded_images_info.append({'data': image_data, 'caption': caption})
                loaded_count += 1
            else:
                loaded_images_info.append({'data': None, 'caption': caption})
        
        # Update main window status
        if loaded_count > 0:
            window['-IMAGES-CONTAINER-'].update(f"✅ Loaded {loaded_count} of {min(len(images), 3)} images")
            window['-LOAD-IMAGES-'].update("View Images", disabled=False)  # Ensure button stays enabled
            
            # Show images in separate window
            show_images_window_external(loaded_images_info, new_version, len(images))
        else:
            window['-IMAGES-CONTAINER-'].update("❌ Failed to load any images")
            window['-LOAD-IMAGES-'].update("Load Failed", disabled=True)
        
        images_loaded = True

    def show_images_window(image_elements, version):
        """Show images in a separate window"""
        if not image_elements:
            return
        
        layout = [
            [sg.Text(f"Release Images - Version {version}", font=('Arial', 14, 'bold'), text_color='white')],
            [sg.HorizontalSeparator()],
            [sg.Column(
                image_elements,
                size=(600, 500),
                scrollable=True,
                vertical_scroll_only=True
            )],
            [sg.Text("")],
            [sg.Button("Close", key="-CLOSE-")]
        ]
        
        images_window = sg.Window(
            f"Release Images - GamesList Manager v{version}",
            layout,
            modal=True,
            icon='gameslisticon.ico',
            element_justification='center',
            size=(650, 500), 
            resizable=True
        )
        
        while True:
            event, values = images_window.read()
            if event in (sg.WIN_CLOSED, '-CLOSE-'):
                break
        
        images_window.close()
    
    result = 'close'
    while True:
        event, values = window.read()
        
        if event in (sg.WIN_CLOSED, '-CLOSE-'):
            result = 'close'
            break
        elif event == '-DOWNLOAD-':
            result = 'download'
            break
        elif event == '-DISABLE-':
            result = 'disable'
            break
        elif event == '-LOAD-IMAGES-':
            if not images_loaded:
                load_images_synchronously()
            elif images_loaded and loaded_count > 0 and loaded_images_info:
                # Show images window again
                show_images_window_external(loaded_images_info, new_version, len(images))
                # Ensure button stays enabled after closing image window
                window['-LOAD-IMAGES-'].update("View Images", disabled=False)
            else:
                # Fallback - reload images if something went wrong
                load_images_synchronously()
    
    window.close()
    return result

def show_download_progress() -> sg.Window:
    """
    Show download progress dialog.
    Returns the window object for progress updates.
    """
    layout = [
        [sg.Text("Downloading Update...", font=('Arial', 12, 'bold'))],
        [sg.ProgressBar(100, orientation='h', size=(50, 20), key='-PROGRESS-')],
        [sg.Text("0%", key='-PROGRESS-TEXT-', justification='center')],
        [sg.Text("Please wait while the update is downloaded.", justification='center')],
        [sg.Button("Cancel", key='-CANCEL-')]
    ]
    
    window = sg.Window(
        "Downloading Update", 
        layout, 
        modal=True, 
        icon='gameslisticon.ico',
        element_justification='center'
    )
    
    return window

def show_staging_progress() -> sg.Window:
    """
    Show staging progress dialog.
    Returns the window object for progress updates.
    """
    layout = [
        [sg.Text("Staging Update...", font=('Arial', 12, 'bold'))],
        [sg.ProgressBar(100, orientation='h', size=(50, 20), key='-PROGRESS-')],
        [sg.Text("0%", key='-PROGRESS-TEXT-', justification='center')],
        [sg.Text("Preparing update for installation...", key='-STATUS-TEXT-', justification='center')],
        [sg.Text("Please wait, this may take a few moments.", font=('Arial', 9), text_color='#4A90E2')]
    ]
    
    window = sg.Window(
        "Staging Update", 
        layout, 
        modal=True, 
        icon='gameslisticon.ico',
        element_justification='center',
        finalize=True
    )
    
    return window

def show_install_confirmation(download_path: str) -> bool:
    """
    Show confirmation dialog before installing update.
    Returns True if user confirms, False otherwise.
    """
    layout = [
        [emoji_image(get_emoji('warning'), size=20), sg.Text("Ready to Install Update", font=('Arial', 14, 'bold'))],
        [sg.HorizontalSeparator()],
        [sg.Text("The update has been downloaded successfully.")],
        [sg.Text(f"Download location: {download_path}")],
        [sg.Text("")],
        [sg.Text("⚠️  Important:", font=('Arial', 10, 'bold'), text_color='#FF6600')],
        [sg.Text("• The application will close to allow file updates")],
        [sg.Text("• A backup of the current version will be created")],
        [sg.Text("• An external updater will replace the files")],
        [sg.Text("• The application will restart automatically")],
        [sg.Text("• Please save any unsaved work before proceeding")],
        [sg.Text("")],
        [sg.Text("Do you want to install the update now?", font=('Arial', 10, 'bold'))],
        [
            sg.Button("✅ Install Now", key="-INSTALL-", button_color=('white', '#2E7D4F')),
            sg.Button("❌ Cancel", key="-CANCEL-")
        ]
    ]
    
    window = sg.Window(
        "Install Update", 
        layout, 
        modal=True, 
        icon='gameslisticon.ico',
        element_justification='left'
    )
    
    result = False
    while True:
        event, values = window.read()
        
        if event in (sg.WIN_CLOSED, '-CANCEL-'):
            result = False
            break
        elif event == '-INSTALL-':
            result = True
            break
    
    window.close()
    return result

def show_update_settings() -> Dict[str, bool]:
    """
    Show update settings dialog.
    Returns dictionary with update preferences.
    """
    updater = get_updater()
    
    layout = [
        [sg.Text("Update Settings", font=('Arial', 14, 'bold'))],
        [sg.HorizontalSeparator()],
        [sg.Text("")],
        [sg.Checkbox(
            "Check for updates when application starts", 
            default=updater.check_on_startup_enabled,
            key='-CHECK-ON-STARTUP-',
            tooltip="Check for updates each time you start the application"
        )],
        [sg.Text("")],
        [sg.Frame("Manual Actions", [
            [sg.Button("🔍 Check for Updates Now", key='-CHECK-NOW-')],
            [sg.Button("📁 Open Downloads Folder", key='-OPEN-DOWNLOADS-')],
            [sg.Button("🗑️ Clear Downloaded Updates", key='-CLEAR-DOWNLOADS-')]
        ])],
        [sg.Text("")],
        [
            sg.Button("💾 Save Settings", key="-SAVE-", button_color=('white', '#2E7D4F')),
            sg.Button("❌ Cancel", key="-CANCEL-")
        ]
    ]
    
    window = sg.Window(
        "Update Settings", 
        layout, 
        modal=True, 
        icon='gameslisticon.ico',
        element_justification='left'
    )
    
    result = None
    while True:
        event, values = window.read()
        
        if event in (sg.WIN_CLOSED, '-CANCEL-'):
            result = None
            break
        elif event == '-SAVE-':
            result = {
                'check_on_startup_enabled': values['-CHECK-ON-STARTUP-']
            }
            break
        elif event == '-CHECK-NOW-':
            # Check for updates manually
            update_result = check_for_updates_manual()
            if update_result == 'download':
                # User chose to download/install, close settings dialog
                window.close()
                return None
        elif event == '-OPEN-DOWNLOADS-':
            # Open downloads folder
            import os
            import subprocess
            from config import get_config_dir
            downloads_dir = os.path.join(get_config_dir(), 'downloads')
            if os.path.exists(downloads_dir):
                subprocess.run(['explorer', downloads_dir], shell=True)
            else:
                sg.popup("Downloads folder not found.", title="Info")
        elif event == '-CLEAR-DOWNLOADS-':
            # Clear downloaded updates
            clear_downloads()
    
    window.close()
    return result

def check_for_updates_manual():
    """Manually check for updates with progress indication"""
    
    # Show checking dialog
    layout = [
        [sg.Text("Checking for updates...", font=('Arial', 12))],
        [sg.ProgressBar(100, orientation='h', size=(40, 20), key='-PROGRESS-')],
        [sg.Text("Please wait...", justification='center')],
    ]
    
    progress_window = sg.Window(
        "Checking for Updates", 
        layout, 
        modal=True, 
        icon='gameslisticon.ico',
        element_justification='center',
        finalize=True
    )
    
    # Animate progress bar while checking
    progress = 0
    update_info = None
    
    def check_thread():
        nonlocal update_info
        updater = get_updater()
        update_info = updater.check_for_updates()
    
    thread = threading.Thread(target=check_thread, daemon=True)
    thread.start()
    
    # Show progress animation
    while thread.is_alive():
        event, values = progress_window.read(timeout=100)
        if event == sg.WIN_CLOSED:
            break
            
        progress = (progress + 5) % 100
        progress_window['-PROGRESS-'].update(progress)
    
    progress_window.close()
    
    # Show results
    if update_info:
        # Directly show update notification and handle the process
        result = show_update_notification(update_info)
        if result == 'download':
            handle_update_process(update_info)
            return 'download'
        elif result == 'disable':
            updater = get_updater()
            updater.set_check_on_startup_enabled(False)
            return 'disable'
        return result
    else:
        sg.popup(
            "You're running the latest version!\n\n"
            f"Current version: {get_updater().current_version}",
            title="No Updates Available"
        )
        return None

def clear_downloads():
    """Clear downloaded update files"""
    import os
    import shutil
    from config import get_config_dir
    
    downloads_dir = os.path.join(get_config_dir(), 'downloads')
    
    if not os.path.exists(downloads_dir):
        sg.popup("No downloads to clear.", title="Info")
        return
    
    try:
        # Count files
        file_count = len([f for f in os.listdir(downloads_dir) 
                         if os.path.isfile(os.path.join(downloads_dir, f))])
        
        if file_count == 0:
            sg.popup("No downloads to clear.", title="Info")
            return
        
        # Confirm deletion
        if sg.popup_yes_no(
            f"This will delete {file_count} downloaded update file(s).\n\n"
            "Are you sure you want to continue?",
            title="Clear Downloads"
        ) == "Yes":
            shutil.rmtree(downloads_dir)
            os.makedirs(downloads_dir, exist_ok=True)
            sg.popup(f"Cleared {file_count} downloaded files.", title="Success")
    
    except Exception as e:
        sg.popup(f"Error clearing downloads: {str(e)}", title="Error")

def handle_update_process(update_info: Dict[str, Any]):
    """Handle the complete update process with UI"""
    updater = get_updater()
    new_version = update_info.get('version', 'Unknown')
    
    # First, check if this version was already downloaded
    existing_download = updater.check_existing_download(new_version)
    
    if existing_download and os.path.exists(existing_download):
        # Ask user if they want to use existing download
        choice = sg.popup_yes_no(
            f"Found existing download for version {new_version}:\n\n"
            f"{existing_download}\n\n"
            "Would you like to use this existing download?",
            title="Existing Download Found"
        )
        
        if choice == "Yes":
            download_path = existing_download
            download_result = "success"
        else:
            # User wants to re-download, proceed normally
            download_path, download_result = _download_with_progress(updater)
    else:
        # No existing download, proceed with normal download
        download_path, download_result = _download_with_progress(updater)
    
    # Check download result
    if download_result == "cancelled":
        # User cancelled - don't show any error message
        return
    elif download_result == "failed":
        sg.popup("Failed to download update. Please try again later.", title="Download Failed")
        return
    
    # Confirm installation
    if show_install_confirmation(download_path):
        # Stage update for installation with progress
        def staging_progress_callback(progress, status):
            """Update staging progress bar"""
            if staging_window and not staging_window.is_closed():
                staging_window['-PROGRESS-'].update(progress)
                staging_window['-PROGRESS-TEXT-'].update(f"{int(progress)}%")
                staging_window['-STATUS-TEXT-'].update(status)
        
        # Show staging progress
        staging_window = show_staging_progress()
        staging_success = False
        
        def staging_thread():
            nonlocal staging_success
            staging_success = updater.install_update(download_path, staging_progress_callback)
        
        # Start staging in background
        thread = threading.Thread(target=staging_thread, daemon=True)
        thread.start()
        
        # Handle staging progress window events
        while thread.is_alive():
            event, values = staging_window.read(timeout=100)
            if event == sg.WIN_CLOSED:
                break
        
        staging_window.close()
        
        # Check staging result
        if staging_success:
            sg.popup(
                "Update has been staged successfully!\n\n"
                "The application will now close and the updater will:\n"
                "• Replace the application files\n"
                "• Restart the application automatically\n\n"
                "Click OK to begin the update process.",
                title="Ready to Update"
            )
            updater.restart_application()
        else:
            sg.popup(
                "Failed to stage update.\n\n"
                "Please try again or install manually.",
                title="Update Failed"
            )
    else:
        sg.popup(
            "Update downloaded but not installed.\n\n"
            f"You can install it later from: {download_path}",
            title="Installation Postponed"
        )

def _download_with_progress(updater):
    """Helper function to handle download with progress display"""
    import threading
    
    # Create cancellation flag
    cancellation_flag = threading.Event()
    
    def progress_callback(progress):
        """Update progress bar during download"""
        if progress_window and not progress_window.is_closed():
            progress_window['-PROGRESS-'].update(progress)
            progress_window['-PROGRESS-TEXT-'].update(f"{int(progress)}%")
    
    # Show download progress
    progress_window = show_download_progress()
    download_result = "failed"  # Default to failed
    download_path = None
    
    def download_thread():
        nonlocal download_result, download_path
        download_path = updater.download_update(progress_callback, cancellation_flag)
        if download_path is not None:
            download_result = "success" 
        elif cancellation_flag.is_set():
            download_result = "cancelled"
        else:
            download_result = "failed"
    
    # Start download in background
    thread = threading.Thread(target=download_thread, daemon=True)
    thread.start()
    
    # Handle progress window events
    while thread.is_alive():
        event, values = progress_window.read(timeout=100)
        if event in (sg.WIN_CLOSED, '-CANCEL-'):
            # Signal cancellation
            cancellation_flag.set()
            sg.popup("Download cancelled.", title="Cancelled")
            progress_window.close()
            
            # Wait a moment for cleanup to complete
            thread.join(timeout=2.0)
            return None, "cancelled"
    
    progress_window.close()
    return download_path, download_result

def show_images_window_external(image_data_list, version, total_images):
    """Show images in a separate window"""
    if not image_data_list:
        return
    
    # Create fresh elements each time (can't reuse PySimpleGUI elements)
    image_elements = []
    
    for image_info in image_data_list:
        caption = image_info['caption']
        
        if image_info['data']:
            image_elements.append([sg.Text(f"📷 {caption}", font=('Arial', 10, 'bold'), text_color='white')])
            image_elements.append([sg.Image(data=image_info['data'])])
            image_elements.append([sg.Text("")])  # Spacing
        else:
            image_elements.append([sg.Text(f"❌ Failed to load: {caption}", font=('Arial', 9), text_color='#CC0000')])
    
    if total_images > 3:
        image_elements.append([sg.Text(f"Note: {total_images - 3} additional images available on GitHub", 
                                      font=('Arial', 9), text_color='white')])
    
    layout = [
        [sg.Text(f"Release Images - Version {version}", font=('Arial', 14, 'bold'), text_color='white')],
        [sg.HorizontalSeparator()],
        [sg.Column(
            image_elements,
            size=(600, 500),
            scrollable=True,
            vertical_scroll_only=True
        )],
        [sg.Text("")],
        [sg.Button("Close", key="-CLOSE-")]
    ]
    
    images_window = sg.Window(
        f"Release Images - GamesList Manager v{version}",
        layout,
        modal=True,
        icon='gameslisticon.ico',
        element_justification='center',
        size=(650, 620),  # Reduced to match content size
        resizable=True
    )
    
    while True:
        event, values = images_window.read()
        if event in (sg.WIN_CLOSED, '-CLOSE-'):
            break
    
    images_window.close()

def show_update_success_popup(update_info: Dict[str, str]):
    """Show a popup indicating successful update"""
    previous_version = update_info.get('previous_version', 'Unknown')
    new_version = update_info.get('new_version', 'Unknown')
    
    layout = [
        [sg.Text("🎉 Update Successful!", font=('Arial', 14, 'bold'), text_color='#4A90E2')],
        [sg.HorizontalSeparator()],
        [sg.Text("")],
        [sg.Text(f"GamesList Manager has been successfully updated!", font=('Arial', 11))],
        [sg.Text("")],
        [sg.Text(f"Previous Version: {previous_version}")],
        [sg.Text(f"Current Version: {new_version}", font=('Arial', 10, 'bold'), text_color='#2E7D4F')],
        [sg.Text("")],
        [sg.Text("✅ All files updated successfully")],
        [sg.Text("✅ Backup of previous version created")],
        [sg.Text("✅ Application restarted automatically")],
        [sg.Text("")],
        [sg.Button("Awesome!", key="-OK-", button_color=('white', '#2E7D4F'), size=(12, 1))]
    ]
    
    window = sg.Window(
        "Update Complete",
        layout,
        modal=True,
        icon='gameslisticon.ico',
        element_justification='center',
        size=(400, 350),
        resizable=False
    )
    
    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, '-OK-'):
            break
    
    window.close() 