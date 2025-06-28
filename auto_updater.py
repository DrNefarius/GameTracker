"""
Auto-updater for GamesList Manager.
Checks for new releases on GitHub and provides update functionality.
"""

import json
import os
import re
import sys
import threading
import time
import zipfile
import shutil
import subprocess
import platform
from datetime import datetime
from urllib.request import urlopen, urlretrieve
from urllib.error import URLError, HTTPError
from typing import Optional, Dict, Any, Callable

# Windows-specific subprocess flags
if platform.system().lower() == 'windows':
    try:
        CREATE_NEW_CONSOLE = subprocess.CREATE_NEW_CONSOLE
    except AttributeError:
        CREATE_NEW_CONSOLE = 0x00000010
else:
    CREATE_NEW_CONSOLE = 0

from constants import VERSION, GITHUB_OWNER, GITHUB_REPO, GITHUB_API_BASE
from config import get_config_dir, load_config, save_config

class AutoUpdater:
    """Handles automatic updates from GitHub releases"""
    
    def __init__(self, startup_check=True):
        self.current_version = VERSION
        self.startup_check = startup_check
        self.update_check_thread = None
        self.is_checking = False
        self.latest_release_info = None
        self.update_callbacks = []  # Callbacks for update notifications
        
        # Load configuration
        self.config = load_config()
        self.check_on_startup_enabled = self.config.get('check_updates_on_startup', True)
    
    def register_update_callback(self, callback: Callable):
        """Register a callback to be called when updates are found"""
        self.update_callbacks.append(callback)
    
    def version_compare(self, version1: str, version2: str) -> int:
        """
        Compare two version strings with support for various formats.
        Returns: -1 if version1 < version2, 0 if equal, 1 if version1 > version2
        
        Supports formats like:
        - 1.7.1, 1.8.0 (semantic versioning)
        - 1.8-release, 2.0-beta (with suffixes)
        - v1.8, v2.0 (with prefixes)
        """
        def normalize_version(v):
            """Extract numeric version parts from various formats"""
            
            # Remove common prefixes
            v = v.lstrip('v').lstrip('V')
            
            # Extract the numeric part (e.g., "1.8-release" -> "1.8")
            # Match pattern: digits, dots, and digits
            match = re.match(r'^(\d+(?:\.\d+)*)', v)
            if match:
                numeric_part = match.group(1)
            else:
                # If no numeric pattern found, try to extract just numbers and dots
                numeric_part = re.sub(r'[^0-9.]', '', v)
            
            # Split into parts and pad to ensure consistent comparison
            parts = numeric_part.split('.')
            # Pad with zeros to ensure we have at least 3 parts (major.minor.patch)
            while len(parts) < 3:
                parts.append('0')
            
            # Convert to integers, handling empty parts
            int_parts = []
            for part in parts[:3]:  # Only take first 3 parts
                try:
                    int_parts.append(int(part) if part else 0)
                except ValueError:
                    int_parts.append(0)
            
            return tuple(int_parts)
        
        try:
            v1_tuple = normalize_version(version1)
            v2_tuple = normalize_version(version2)
            
            print(f"Version comparison: {version1} ({v1_tuple}) vs {version2} ({v2_tuple})")
            
            if v1_tuple < v2_tuple:
                return -1
            elif v1_tuple > v2_tuple:
                return 1
            else:
                return 0
        except Exception as e:
            print(f"Error comparing versions {version1} vs {version2}: {e}")
            # If version parsing fails, assume no update needed
            return 0
    
    def check_for_updates(self) -> Optional[Dict[str, Any]]:
        """
        Check GitHub releases for updates.
        Returns release info if update available, None otherwise.
        """
        try:
            # Get latest release info from GitHub API
            api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
            
            with urlopen(api_url, timeout=10) as response:
                if response.status != 200:
                    print(f"GitHub API returned status {response.status}")
                    return None
                
                release_data = json.loads(response.read().decode('utf-8'))
            
            # Extract version from tag_name (e.g., "v1.8.2" -> "1.8.2")
            raw_tag = release_data.get('tag_name', '')
            latest_version = raw_tag.lstrip('v').lstrip('V')  # Handle both v and V prefixes
            release_name = release_data.get('name', '')
            release_notes = release_data.get('body', '')
            release_url = release_data.get('html_url', '')
            
            print(f"Update check: Current={self.current_version}, GitHub tag='{raw_tag}', Parsed={latest_version}")
            download_url = None
            
            # Find appropriate download URL for current platform
            assets = release_data.get('assets', [])
            system_name = platform.system().lower()
            
            for asset in assets:
                asset_name = asset.get('name', '').lower()
                if system_name == 'windows' and ('.exe' in asset_name or '.zip' in asset_name):
                    download_url = asset.get('browser_download_url')
                    break
                elif system_name == 'darwin' and ('.dmg' in asset_name or '.zip' in asset_name):
                    download_url = asset.get('browser_download_url')
                    break
                elif system_name == 'linux' and ('.tar.gz' in asset_name or '.zip' in asset_name):
                    download_url = asset.get('browser_download_url')
                    break
            
            # Check if update is available
            comparison_result = self.version_compare(self.current_version, latest_version)
            print(f"Version comparison result: {comparison_result} ({'UPDATE AVAILABLE' if comparison_result < 0 else 'NO UPDATE' if comparison_result == 0 else 'DOWNGRADE'})")
            
            if comparison_result < 0:
                self.latest_release_info = {
                    'version': latest_version,
                    'name': release_name,
                    'notes': release_notes,
                    'url': release_url,
                    'download_url': download_url,
                    'published_at': release_data.get('published_at'),
                    'current_version': self.current_version
                }
                
                return self.latest_release_info
            
            # No update available
            return None
            
        except (URLError, HTTPError, json.JSONDecodeError, Exception) as e:
            print(f"Error checking for updates: {str(e)}")
            return None
    
    def check_existing_download(self, version: str) -> Optional[str]:
        """
        Check if an update for the specified version was already downloaded.
        Returns the path to the existing download if found, None otherwise.
        """
        try:
            downloads_dir = os.path.join(get_config_dir(), 'downloads')
            if not os.path.exists(downloads_dir):
                return None
            
            # Normalize version for comparison (remove common prefixes/suffixes)
            normalized_version = version.lower().replace('v', '').replace('-release', '').replace('.', '')
            
            # Look for files that match the version pattern
            for filename in os.listdir(downloads_dir):
                file_path = os.path.join(downloads_dir, filename)
                if os.path.isfile(file_path):
                    # Normalize filename for comparison
                    normalized_filename = filename.lower().replace('v', '').replace('-release', '').replace('.', '').replace('-', '')
                    
                    # Check if version is contained in filename
                    if normalized_version in normalized_filename:
                        print(f"Found existing download for version {version}: {file_path}")
                        
                        # Verify file is not empty or corrupted
                        file_size = os.path.getsize(file_path)
                        if file_size > 10000:  # At least 10KB for a realistic update
                            return file_path
                        else:
                            print(f"Download file is too small ({file_size} bytes), considering it corrupted: {file_path}")
            
            print(f"No existing download found for version {version}")
            return None
            
        except Exception as e:
            print(f"Error checking for existing downloads: {str(e)}")
            return None

    def download_update(self, progress_callback: Optional[Callable] = None, cancellation_flag: Optional[threading.Event] = None) -> Optional[str]:
        """
        Download the latest update.
        Returns path to downloaded file or None if failed/cancelled.
        """
        if not self.latest_release_info or not self.latest_release_info.get('download_url'):
            print("No download URL available")
            return None
        
        try:
            download_url = self.latest_release_info['download_url']
            filename = download_url.split('/')[-1]
            
            # Create downloads directory
            downloads_dir = os.path.join(get_config_dir(), 'downloads')
            os.makedirs(downloads_dir, exist_ok=True)
            
            download_path = os.path.join(downloads_dir, filename)
            
            # Download with cancellation support
            print(f"Downloading update from {download_url}")
            success = self._download_with_cancellation(
                download_url, 
                download_path, 
                progress_callback, 
                cancellation_flag
            )
            
            if success:
                print(f"Update downloaded to {download_path}")
                return download_path
            else:
                # Clean up partial download
                if os.path.exists(download_path):
                    try:
                        os.remove(download_path)
                        print(f"Cleaned up partial download: {download_path}")
                    except Exception as e:
                        print(f"Failed to clean up partial download: {e}")
                return None
            
        except Exception as e:
            print(f"Error downloading update: {str(e)}")
            return None
    
    def _download_with_cancellation(self, url: str, filepath: str, progress_callback: Optional[Callable] = None, cancellation_flag: Optional[threading.Event] = None) -> bool:
        """
        Download a file with cancellation support.
        Returns True if successful, False if cancelled or failed.
        """
        try:
            from urllib.request import urlopen
            
            # Open the URL
            response = urlopen(url)
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            chunk_size = 8192  # 8KB chunks
            
            with open(filepath, 'wb') as f:
                while True:
                    # Check for cancellation
                    if cancellation_flag and cancellation_flag.is_set():
                        print("Download cancelled by user")
                        return False
                    
                    # Read chunk
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    
                    # Write chunk
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    # Update progress
                    if progress_callback and total_size > 0:
                        progress = min(100, (downloaded / total_size) * 100)
                        progress_callback(progress)
            
            return True
            
        except Exception as e:
            print(f"Download error: {e}")
            return False
    
    def install_update(self, download_path: str, progress_callback: Optional[Callable] = None) -> bool:
        """
        Install the downloaded update using staged approach to handle file locking.
        Returns True if successful, False otherwise.
        """
        try:
            if progress_callback:
                progress_callback(5, "Checking download file...")
            
            if not os.path.exists(download_path):
                print(f"Download file not found: {download_path}")
                return False
            
            if progress_callback:
                progress_callback(10, "Preparing installation...")
            
            # Get current executable directory
            if getattr(sys, 'frozen', False):
                # Running as executable
                current_dir = os.path.dirname(sys.executable)
                executable_name = os.path.basename(sys.executable)
            else:
                # Running as script
                current_dir = os.path.dirname(os.path.abspath(__file__))
                executable_name = "main.py"
            
            # Create staging and backup directories
            config_dir = get_config_dir()
            staging_dir = os.path.join(config_dir, 'staging')
            backup_dir = os.path.join(config_dir, 'backup')
            
            if progress_callback:
                progress_callback(20, "Cleaning up previous staging...")
            
            # Clean up any previous staging
            if os.path.exists(staging_dir):
                shutil.rmtree(staging_dir)
            os.makedirs(staging_dir, exist_ok=True)
            os.makedirs(backup_dir, exist_ok=True)
            
            if progress_callback:
                progress_callback(30, "Extracting update files...")
            
            # Extract update to staging directory
            if download_path.endswith('.zip'):
                print("Extracting update to staging directory...")
                with zipfile.ZipFile(download_path, 'r') as zip_ref:
                    zip_ref.extractall(staging_dir)
            else:
                print(f"Unsupported file format: {download_path}")
                return False
            
            if progress_callback:
                progress_callback(60, "Creating backup of current version...")
            
            # Create backup of current version
            backup_name = f"backup_{self.current_version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            backup_path = os.path.join(backup_dir, backup_name)
            
            print(f"Creating backup at {backup_path}")
            shutil.copytree(current_dir, backup_path, ignore=shutil.ignore_patterns('*.log', '__pycache__', '*.pyc'))
            
            if progress_callback:
                progress_callback(90, "Creating updater script...")
            
            # Create updater script that will run after this process exits
            updater_script = self._create_updater_script(staging_dir, current_dir, executable_name, backup_path)
            
            if progress_callback:
                progress_callback(100, "Staging complete!")
            
            print("Update staged successfully. Updater script created.")
            print(f"Updater script: {updater_script}")
            
            return True
            
        except Exception as e:
            print(f"Error staging update: {str(e)}")
            return False
    
    def _create_updater_script(self, staging_dir: str, target_dir: str, executable_name: str, backup_path: str) -> str:
        """Create an updater script that runs after the main process exits"""
        system_name = platform.system().lower()
        
        if system_name == 'windows':
            return self._create_windows_updater_script(staging_dir, target_dir, executable_name, backup_path)
        else:
            return self._create_unix_updater_script(staging_dir, target_dir, executable_name, backup_path)
    
    def _create_windows_updater_script(self, staging_dir: str, target_dir: str, executable_name: str, backup_path: str) -> str:
        """Create Windows batch updater script"""
        script_path = os.path.join(get_config_dir(), 'updater.bat')
        
        # Get current process ID to wait for it to exit
        current_pid = os.getpid()
        
        script_content = f'''@echo off
echo GamesList Manager Updater
echo.

echo Waiting for main application to close...
:WAIT_LOOP
tasklist /FI "PID eq {current_pid}" 2>NUL | find /I "{current_pid}" >NUL
if %ERRORLEVEL% == 0 (
    timeout /t 1 /nobreak > NUL
    goto WAIT_LOOP
)

echo Updating application files...

rem Copy new files from staging to target directory
echo Copying files from "{staging_dir}" to "{target_dir}"
xcopy /E /Y /I "{staging_dir}\\*" "{target_dir}\\"

if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to copy files!
    echo Attempting to restore from backup...
    xcopy /E /Y /I "{backup_path}\\*" "{target_dir}\\"
    echo.
    echo Update failed! Application restored from backup.
    pause
    exit /b 1
)

echo Update completed successfully!

rem Clean up staging directory
echo Cleaning up...
rmdir /S /Q "{staging_dir}" 2>NUL

echo Starting updated application...
cd /D "{target_dir}"
start "" "{executable_name}"

rem Wait a moment then clean up this script
timeout /t 2 /nobreak > NUL
del "%~f0" 2>NUL
'''
        
        try:
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(script_content)
            return script_path
        except Exception as e:
            print(f"Failed to create updater script: {e}")
            return None
    
    def _create_unix_updater_script(self, staging_dir: str, target_dir: str, executable_name: str, backup_path: str) -> str:
        """Create Unix shell updater script"""
        script_path = os.path.join(get_config_dir(), 'updater.sh')
        
        # Get current process ID to wait for it to exit
        current_pid = os.getpid()
        
        script_content = f'''#!/bin/bash
echo "GamesList Manager Updater"
echo ""

echo "Waiting for main application to close..."
while kill -0 {current_pid} 2>/dev/null; do
    sleep 1
done

echo "Updating application files..."

# Copy new files from staging to target directory
echo "Copying files from '{staging_dir}' to '{target_dir}'"
if cp -R "{staging_dir}/"* "{target_dir}/"; then
    echo "Update completed successfully!"
else
    echo "ERROR: Failed to copy files!"
    echo "Attempting to restore from backup..."
    cp -R "{backup_path}/"* "{target_dir}/"
    echo ""
    echo "Update failed! Application restored from backup."
    exit 1
fi

# Clean up staging directory
echo "Cleaning up..."
rm -rf "{staging_dir}"

echo "Starting updated application..."
cd "{target_dir}"
if [[ "{executable_name}" == *.py ]]; then
    python "{executable_name}" &
else
    ./{executable_name} &
fi

# Wait a moment then clean up this script
sleep 2
rm "$0" 2>/dev/null
'''
        
        try:
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(script_content)
            # Make script executable on Unix systems
            os.chmod(script_path, 0o755)
            return script_path
        except Exception as e:
            print(f"Failed to create updater script: {e}")
            return None
    
    def check_for_updates_async(self, callback: Optional[Callable] = None):
        """
        Check for updates in background thread.
        
        WARNING: This method should only be used when the callback doesn't involve GUI operations,
        as PySimpleGUI requires all GUI operations to be on the main thread.
        Use check_for_updates() directly for startup checks or when showing GUI dialogs.
        """
        if self.is_checking:
            return
        
        def check_thread():
            self.is_checking = True
            try:
                update_info = self.check_for_updates()
                if callback:
                    callback(update_info)
            finally:
                self.is_checking = False
        
        self.update_check_thread = threading.Thread(target=check_thread, daemon=True)
        self.update_check_thread.start()
    
    def check_on_startup(self):
        """Check for updates on application startup if enabled"""
        if self.check_on_startup_enabled:
            try:
                update_info = self.check_for_updates()
                if update_info:
                    # Notify registered callbacks (synchronously on main thread)
                    for cb in self.update_callbacks:
                        try:
                            cb(update_info)
                        except Exception as e:
                            print(f"Error in update callback: {str(e)}")
            except Exception as e:
                print(f"Error checking for updates on startup: {str(e)}")
    
    def _save_config(self):
        """Save updater configuration"""
        config = load_config()
        config['check_updates_on_startup'] = self.check_on_startup_enabled
        save_config(config)
    
    def set_check_on_startup_enabled(self, enabled: bool):
        """Enable or disable checking for updates on startup"""
        self.check_on_startup_enabled = enabled
        self._save_config()
    
    def get_update_info(self) -> Optional[Dict[str, Any]]:
        """Get information about the latest available update"""
        return self.latest_release_info
    
    def restart_application(self):
        """Exit the application so the updater script can run"""
        try:
            # Check if there's a pending update script
            config_dir = get_config_dir()
            system_name = platform.system().lower()
            
            if system_name == 'windows':
                updater_script = os.path.join(config_dir, 'updater.bat')
            else:
                updater_script = os.path.join(config_dir, 'updater.sh')
            
            if os.path.exists(updater_script):
                print("Starting updater script and exiting application...")
                # Start the updater script in the background
                if system_name == 'windows':
                    # Use subprocess.Popen with CREATE_NEW_CONSOLE to run independently
                    subprocess.Popen([updater_script], creationflags=CREATE_NEW_CONSOLE)
                else:
                    # Start the script in background
                    subprocess.Popen(['/bin/bash', updater_script])
                
                # Small delay to ensure script starts
                time.sleep(0.5)
            else:
                print("No updater script found. Restarting normally...")
                # Fallback to normal restart if no update pending
                if getattr(sys, 'frozen', False):
                    subprocess.Popen([sys.executable])
                else:
                    # Find main.py in the current directory
                    main_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'main.py')
                    subprocess.Popen([sys.executable, main_script])
            
            # Exit current instance to allow updater to work
            sys.exit(0)
            
        except Exception as e:
            print(f"Error during restart: {str(e)}")
            # Force exit anyway
            sys.exit(1)

# Global updater instance
_updater_instance = None

def get_updater() -> AutoUpdater:
    """Get the global updater instance"""
    global _updater_instance
    if _updater_instance is None:
        _updater_instance = AutoUpdater()
    return _updater_instance

def initialize_updater(check_on_startup=True) -> AutoUpdater:
    """Initialize the global updater instance"""
    global _updater_instance
    _updater_instance = AutoUpdater(startup_check=check_on_startup)
    return _updater_instance 