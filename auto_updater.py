"""
Auto-updater for GamesList Manager.
Checks for new releases on GitHub and provides update functionality.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import zipfile
import platform
from datetime import datetime
from urllib.request import urlopen, urlretrieve, Request
from urllib.error import URLError, HTTPError
from typing import Optional, Dict, Any, Callable

try:
    from packaging.version import Version, InvalidVersion
    _HAS_PACKAGING = True
except ImportError:  # packaging is bundled with pip/setuptools on most installs
    _HAS_PACKAGING = False

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

# GitHub asks unauthenticated clients to send a User-Agent identifying the app.
USER_AGENT = f"GamesListManager/{VERSION} (+https://github.com/{GITHUB_OWNER}/{GITHUB_REPO})"


def _github_open(url: str, timeout: float = 30.0):
    """urlopen wrapper that sets a User-Agent and surfaces 403/429 clearly."""
    req = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'})
    try:
        return urlopen(req, timeout=timeout)
    except HTTPError as e:
        if e.code in (403, 429):
            # Include the reset hint if GitHub supplied it so callers can log meaningfully.
            reset = e.headers.get('X-RateLimit-Reset') if e.headers else None
            remaining = e.headers.get('X-RateLimit-Remaining') if e.headers else None
            print(f"GitHub API rate limited (HTTP {e.code}); remaining={remaining}, reset={reset}")
        raise


def _log_update(msg: str) -> None:
    """Append a timestamped line to a persistent updater log.

    A packaged GUI app has no console, so ``print`` output is lost. This log
    (``<config dir>/update_log.txt``) survives the staging->restart->relaunch
    cycle and is the primary way to diagnose update problems in the field.
    """
    try:
        line = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
        print(line)
        with open(os.path.join(get_config_dir(), 'update_log.txt'), 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass


def _process_image_path() -> Optional[str]:
    """Full path of the .exe that started THIS process (Windows only).

    A Flet desktop build embeds CPython *in-process* inside ``GameTracker.exe``
    (loaded via ``python3*.dll``), so it sets neither ``sys.frozen`` nor a
    meaningful ``sys.executable`` — the classic frozen-detection signals fail.
    The Win32 ``GetModuleFileNameW(NULL)`` returns the real host .exe regardless
    of how Python was embedded, so it correctly resolves to ``GameTracker.exe``.
    """
    if platform.system().lower() != 'windows':
        return None
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetModuleFileNameW(None, buf, len(buf)):
            return buf.value
    except Exception as e:
        print(f"Could not resolve process image path: {e}")
    return None


def _resolve_install_target():
    """Return ``(install_dir, executable_name)`` for staging + relaunch.

    Handles all three runtimes the project ships/runs as:
      * **Flet packaged build** — Python embedded in the app .exe; detected via
        the real process image path (``GetModuleFileNameW``), confirmed by the
        embedded-runtime files sitting beside it.
      * **classic cx_Freeze / PyInstaller** frozen build — sets ``sys.frozen``.
      * **running from source** — relaunch the Flet entry ``app_flet.py`` (NOT
        the legacy PySimpleGUI ``main.py``).
    """
    # 1) Classic frozen build (cx_Freeze / PyInstaller).
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable), os.path.basename(sys.executable)

    # 2) Flet packaged desktop build (embedded in-process Python).
    img = _process_image_path()
    if img:
        d = os.path.dirname(img)
        base = os.path.basename(img)
        # Guard: ignore a dev python.exe/flet.exe and only trust the image when
        # the packaged embedded-runtime layout is present next to it.
        if base.lower() not in ('python.exe', 'pythonw.exe', 'flet.exe') and (
            os.path.exists(os.path.join(d, 'python3.dll'))
            or os.path.exists(os.path.join(d, 'serious_python_windows_plugin.dll'))
            or os.path.isdir(os.path.join(d, 'data', 'flutter_assets', 'app'))
        ):
            return d, base

    # 3) Running from source -> the Flet entry point, not legacy main.py.
    return os.path.dirname(os.path.abspath(__file__)), 'app_flet.py'


def _serious_python_extract_dir() -> Optional[str]:
    """Return the Flet (serious_python) app-extraction dir, or None.

    A packaged Flet desktop app extracts ``app.zip`` to a per-user dir
    (``…\\<company>\\<product>\\flet\\app``) and **re-extracts on launch whenever
    app.zip changes** — which it always does right after an update. To re-extract
    it must delete the previous extraction; if any file there is still locked
    (notably ``gameslisticon.ico``, which the Windows Shell holds open while a
    toast referencing it via AUMID IconUri is on screen / in Action Center), the
    delete fails with a sharing violation and the app dies with a white window.

    We let the updater script clear this dir (with retries) *after* the old
    process is gone, so the relaunched app re-extracts cleanly instead of racing
    that transient lock. Detected by serious_python's ``.hash`` marker beside our
    own module; returns None from source / classic frozen builds (where
    ``__file__`` is the repo / there is no ``.hash``), so we never delete those.
    """
    try:
        d = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        return None
    if (os.path.basename(d).lower() == 'app'
            and os.path.basename(os.path.dirname(d)).lower() == 'flet'
            and os.path.exists(os.path.join(d, '.hash'))):
        return d
    return None


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
    
    def version_compare(self, version1: str, version2: str) -> Optional[int]:
        """
        Compare two version strings with support for various formats.
        Returns: -1 if version1 < version2, 0 if equal, 1 if version1 > version2,
        or None if either version could not be parsed (so callers do not silently
        treat a parse failure as "no update").
        
        Uses packaging.version when available (proper semver + prerelease handling),
        otherwise falls back to a numeric-tuple comparison.
        """
        def normalize_tuple(v: str):
            v = v.lstrip('v').lstrip('V')
            match = re.match(r'^(\d+(?:\.\d+)*)', v)
            numeric_part = match.group(1) if match else re.sub(r'[^0-9.]', '', v)
            parts = numeric_part.split('.') if numeric_part else []
            while len(parts) < 3:
                parts.append('0')
            return tuple(int(p) if p.isdigit() else 0 for p in parts[:3])
        
        try:
            if _HAS_PACKAGING:
                v1 = Version(version1.lstrip('v').lstrip('V'))
                v2 = Version(version2.lstrip('v').lstrip('V'))
                print(f"Version comparison: {version1} ({v1}) vs {version2} ({v2})")
                if v1 < v2:
                    return -1
                if v1 > v2:
                    return 1
                return 0
        except (InvalidVersion, ValueError) as e:
            print(f"packaging.Version could not parse '{version1}' or '{version2}' ({e}); falling back to tuple compare")
        except Exception as e:
            print(f"Unexpected error in packaging version parse: {e}; falling back to tuple compare")
        
        try:
            v1_tuple = normalize_tuple(version1)
            v2_tuple = normalize_tuple(version2)
            print(f"Version comparison (tuple): {version1} ({v1_tuple}) vs {version2} ({v2_tuple})")
            if v1_tuple < v2_tuple:
                return -1
            if v1_tuple > v2_tuple:
                return 1
            return 0
        except Exception as e:
            print(f"Error comparing versions {version1} vs {version2}: {e}")
            # Surface parse failure as "unknown" rather than silently "equal".
            return None
    
    def check_for_updates(self) -> Optional[Dict[str, Any]]:
        """
        Check GitHub releases for updates.
        Returns release info if update available, None otherwise.
        """
        try:
            # Get latest release info from GitHub API
            api_url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
            
            with _github_open(api_url, timeout=10) as response:
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
            if comparison_result is None:
                print(f"Version comparison result: UNKNOWN (could not parse '{self.current_version}' or '{latest_version}')")
                return None
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
            # Use a generous timeout to avoid hanging forever on a dead connection,
            # while still giving large release archives time to start streaming.
            response = _github_open(url, timeout=60)
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
    
    @staticmethod
    def _safe_extract_zip(zip_ref: zipfile.ZipFile, target_dir: str) -> None:
        """
        Extract a zip archive to target_dir, rejecting any entry whose resolved path
        would escape target_dir (zip-slip protection). Also skips absolute paths and
        symlinks, which GitHub release archives should not contain.
        """
        target_abs = os.path.realpath(target_dir)
        members = []
        for info in zip_ref.infolist():
            name = info.filename
            # Reject absolute paths and drive letters outright.
            if name.startswith('/') or name.startswith('\\') or (len(name) > 1 and name[1] == ':'):
                raise ValueError(f"Refusing to extract absolute path from archive: {name!r}")
            
            # Reject symlinks (high bit 0xA on unix external_attr).
            if (info.external_attr >> 28) == 0xA:
                raise ValueError(f"Refusing to extract symlink from archive: {name!r}")
            
            # Resolve where this member would land and ensure it stays inside target_dir.
            dest_path = os.path.realpath(os.path.join(target_abs, name))
            if dest_path != target_abs and not dest_path.startswith(target_abs + os.sep):
                raise ValueError(f"Refusing zip-slip path that escapes staging dir: {name!r}")
            members.append(info)
        
        zip_ref.extractall(target_dir, members=members)
    
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
            
            # Resolve the install dir + executable to relaunch. Works for the
            # Flet packaged build (embedded Python in GameTracker.exe), classic
            # frozen builds, and source runs.
            current_dir, executable_name = _resolve_install_target()
            _log_update("=== install_update staging ===")
            _log_update(f"download_path={download_path!r}")
            _log_update(f"__file__={os.path.abspath(__file__)!r}")
            _log_update(f"sys.frozen={getattr(sys, 'frozen', False)!r} "
                        f"sys.executable={sys.executable!r}")
            _log_update(f"process_image={_process_image_path()!r}")
            _log_update(f"install target: dir={current_dir!r}, executable={executable_name!r}")
            
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
                    self._safe_extract_zip(zip_ref, staging_dir)
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
            
            # Clean up old backups (keep only last 3)
            self._cleanup_old_backups(backup_dir)
            
            if progress_callback:
                progress_callback(90, "Creating updater script...")
            
            # Create updater script that will run after this process exits. Pass
            # the serious_python extraction dir (if any) so the script can clear
            # it pre-relaunch and avoid the re-extract lock race.
            sp_extract_dir = _serious_python_extract_dir()
            _log_update(f"serious_python extract dir (to clear pre-relaunch): {sp_extract_dir!r}")
            updater_script = self._create_updater_script(
                staging_dir, current_dir, executable_name, backup_path, sp_extract_dir)
            _log_update(f"updater script written: {updater_script!r}")
            
            if progress_callback:
                progress_callback(100, "Staging complete!")
            
            print("Update staged successfully. Updater script created.")
            print(f"Updater script: {updater_script}")
            
            return True
            
        except Exception as e:
            print(f"Error staging update: {str(e)}")
            return False
    
    def _cleanup_old_backups(self, backup_dir: str, keep_count: int = 3):
        """
        Clean up old backup directories, keeping only the most recent ones.
        
        Args:
            backup_dir: Directory containing backup folders
            keep_count: Number of most recent backups to keep (default: 3)
        """
        try:
            if not os.path.exists(backup_dir):
                return
            
            # Get all backup directories
            backup_folders = []
            for item in os.listdir(backup_dir):
                item_path = os.path.join(backup_dir, item)
                if os.path.isdir(item_path) and item.startswith('backup_'):
                    # Extract timestamp from folder name (backup_version_YYYYMMDD_HHMMSS)
                    parts = item.split('_')
                    if len(parts) >= 4:  # backup + version + date + time
                        try:
                            # Combine date and time parts for sorting
                            date_str = parts[-2]  # YYYYMMDD
                            time_str = parts[-1]  # HHMMSS
                            timestamp_str = f"{date_str}_{time_str}"
                            timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
                            backup_folders.append((timestamp, item_path, item))
                        except (ValueError, IndexError):
                            # Skip folders with invalid timestamp format
                            continue
            
            # Sort by timestamp (newest first)
            backup_folders.sort(key=lambda x: x[0], reverse=True)
            
            # Keep only the most recent backups
            if len(backup_folders) > keep_count:
                backups_to_remove = backup_folders[keep_count:]
                
                print(f"Cleaning up {len(backups_to_remove)} old backup(s), keeping {keep_count} most recent")
                
                for timestamp, backup_path, backup_name in backups_to_remove:
                    try:
                        print(f"Removing old backup: {backup_name}")
                        shutil.rmtree(backup_path)
                    except Exception as e:
                        print(f"Failed to remove backup {backup_name}: {e}")
            else:
                print(f"Backup cleanup: {len(backup_folders)} backup(s) found, no cleanup needed")
                
        except Exception as e:
            print(f"Error during backup cleanup: {e}")
    
    def _create_updater_script(self, staging_dir: str, target_dir: str, executable_name: str, backup_path: str, sp_extract_dir: Optional[str] = None) -> str:
        """Create an updater script that runs after the main process exits"""
        system_name = platform.system().lower()

        if system_name == 'windows':
            return self._create_windows_updater_script(staging_dir, target_dir, executable_name, backup_path, sp_extract_dir)
        else:
            return self._create_unix_updater_script(staging_dir, target_dir, executable_name, backup_path, sp_extract_dir)

    def _create_windows_updater_script(self, staging_dir: str, target_dir: str, executable_name: str, backup_path: str, sp_extract_dir: Optional[str] = None) -> str:
        """Create Windows PowerShell updater script for better Unicode support"""
        script_path = os.path.join(get_config_dir(), 'updater.ps1')
        
        # Get current process ID to wait for it to exit
        current_pid = os.getpid()
        
        # Extract version from backup path for success flag
        backup_name = os.path.basename(backup_path)
        parts = backup_name.split('_')
        previous_version = parts[1] if len(parts) >= 2 else 'Unknown'
        
        # Escape paths with quotes and handle Unicode properly
        staging_dir_escaped = staging_dir.replace("'", "''")
        target_dir_escaped = target_dir.replace("'", "''")
        backup_path_escaped = backup_path.replace("'", "''")
        executable_name_escaped = executable_name.replace("'", "''")
        log_path_escaped = os.path.join(get_config_dir(), 'updater_log.txt').replace("'", "''")

        # Optional: clear the Flet (serious_python) app-extraction cache before
        # relaunch so the updated app re-extracts cleanly. The big failure mode:
        # if a prior update relaunch failed to extract, that Flet process keeps
        # running (a white window) with the extraction dir as its WORKING
        # DIRECTORY — which the single-instance guard can't catch (it lives in
        # Python, which never starts on a failed extract). That zombie holds the
        # dir so every later update fails too. So before clearing, terminate any
        # lingering app instances: the legit pre-update instance already exited
        # (we waited on its PID above), so anything still running here is stale.
        if sp_extract_dir:
            sp_extract_dir_escaped = sp_extract_dir.replace("'", "''")
            proc_name_escaped = os.path.splitext(os.path.basename(executable_name))[0].replace("'", "''")
            flet_cache_clear = f'''
# Clear the Flet (serious_python) extraction cache so the app re-extracts clean.
# Move the PROCESS working directory out of the extraction dir first. This
# PowerShell process inherits the old app's cwd (serious_python chdir's into the
# extraction dir), and a directory cannot be deleted while it is a live
# process's current directory. NOTE: Set-Location only changes PowerShell's $PWD,
# NOT the OS-level process cwd that holds the lock -- so use the .NET call, which
# maps to Win32 SetCurrentDirectory and actually releases the handle.
[System.IO.Directory]::SetCurrentDirectory($env:TEMP)
Set-Location -Path $env:TEMP
Write-Host ("Updater process cwd is now: " + [System.IO.Directory]::GetCurrentDirectory())

# Terminate any lingering/zombie app instances (e.g. a previous failed-extract
# white window) still holding the extraction dir as their working directory.
$stale = @(Get-Process -Name '{proc_name_escaped}' -ErrorAction SilentlyContinue)
Write-Host ("Stale '{proc_name_escaped}' instances found: " + $stale.Count)
if ($stale.Count -gt 0) {{
    foreach ($p in $stale) {{
        try {{ Stop-Process -Id $p.Id -Force -ErrorAction Stop; Write-Host ("  killed PID " + $p.Id) }}
        catch {{ Write-Host ("  failed to kill PID " + $p.Id + ": " + $_) }}
    }}
    Start-Sleep -Milliseconds 500
}}

$fletApp = '{sp_extract_dir_escaped}'
Write-Host "Flet extraction dir to clear: $fletApp"
if (Test-Path $fletApp) {{
    Write-Host "Clearing Flet extraction cache (exists=True)..."
    $lastErr = $null
    for ($i = 0; $i -lt 40; $i++) {{
        try {{
            Remove-Item -Path $fletApp -Recurse -Force -ErrorAction Stop
            break
        }} catch {{
            $lastErr = $_
            Start-Sleep -Milliseconds 250
        }}
    }}
    if (Test-Path $fletApp) {{
        Write-Host "WARNING: Flet cache still present after $i retries. Last error: $lastErr"
        Write-Host "Remaining items (may reveal the locked file):"
        Get-ChildItem -LiteralPath $fletApp -Recurse -ErrorAction SilentlyContinue | ForEach-Object {{ Write-Host ("  " + $_.FullName) }}
    }} else {{
        Write-Host "Flet extraction cache cleared after $i retries."
    }}
}} else {{
    Write-Host "Flet extraction dir does not exist; nothing to clear."
}}
'''
        else:
            flet_cache_clear = (
                '\nWrite-Host "No Flet extraction dir was resolved at staging time '
                '(sp_extract_dir was None) - cache-clear skipped."\n'
            )

        script_content = f'''# GamesList Manager Updater (PowerShell)
# This script handles Unicode paths properly

# Persistent log: a packaged GUI relaunch has no visible console, so transcribe
# everything to a file we can inspect after the fact.
try {{ Start-Transcript -Path '{log_path_escaped}' -Append -Force | Out-Null }} catch {{}}
Write-Host ("===== Updater run @ " + (Get-Date -Format o) + " =====")
Write-Host "Waiting on PID: {current_pid}"
Write-Host "Target dir: '{target_dir_escaped}'"
Write-Host "Executable: '{executable_name_escaped}'"

Write-Host "GamesList Manager Updater"
Write-Host ""

# Wait for main application to close
Write-Host "Waiting for main application to close..."
while (Get-Process -Id {current_pid} -ErrorAction SilentlyContinue) {{
    Start-Sleep -Seconds 1
}}
Write-Host "Main application has exited."

Write-Host "Updating application files..."

# Copy new files from staging to target directory
Write-Host "Copying files from '{staging_dir_escaped}' to '{target_dir_escaped}'"

try {{
    # Use robocopy for better Unicode and long path support
    $result = robocopy '{staging_dir_escaped}' '{target_dir_escaped}' /E /R:3 /W:1 /MT:1
    
    # robocopy exit codes: 0-7 are success, 8+ are failures
    if ($LASTEXITCODE -ge 8) {{
        throw "Robocopy failed with exit code $LASTEXITCODE"
    }}
    
    Write-Host "Update completed successfully!"
    
    # Clean up staging directory
    Write-Host "Cleaning up..."
    Remove-Item -Path '{staging_dir_escaped}' -Recurse -Force -ErrorAction SilentlyContinue
    {flet_cache_clear}
    # Strip the dying app's leaked Flet runtime env vars before relaunch. They are
    # instance-specific (per-launch server/callback ports, console log, asset dir)
    # and were inherited: old app -> this PowerShell -> the new app. If the new
    # app inherits them, its serious_python bootstrap reuses the OLD (dead) ports
    # instead of assigning fresh ones, so the window shows the theme but never
    # renders controls; and PYTHONINSPECT=1 then opens a stray interactive console
    # bound to the process. A normal double-click gets a clean env, which is why
    # manual launch works. Clearing them here makes the relaunch behave the same.
    Get-ChildItem Env: | Where-Object {{ $_.Name -like 'FLET_*' -or $_.Name -eq 'PYTHONINSPECT' }} | ForEach-Object {{
        Write-Host ("Clearing inherited env before relaunch: " + $_.Name)
        Remove-Item ("Env:" + $_.Name) -ErrorAction SilentlyContinue
    }}

    # Start the updated application like a user double-click: hand it to Explorer
    # so its parent is the (console-less) shell, NOT this updater. The updater owns
    # a console (CREATE_NEW_CONSOLE), and serious_python attaches the new app to
    # its PARENT's console (AttachConsole(ATTACH_PARENT_PROCESS)) for Python output
    # -- which tethers the app to a terminal window (close one -> close the other).
    # Launching via Explorer gives the app no parent console (and a clean shell
    # environment), exactly like a double-click, so there is no tether.
    Write-Host "Starting updated application..."
    Set-Location -Path '{target_dir_escaped}'
    $appPath = Join-Path '{target_dir_escaped}' '{executable_name_escaped}'
    if ($appPath -like '*.py') {{
        # Source/dev fallback (not a packaged build): run with the interpreter.
        Start-Process -FilePath 'python' -ArgumentList ('"' + $appPath + '"') -WorkingDirectory '{target_dir_escaped}'
    }} else {{
        Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $appPath + '"')
    }}
    Write-Host "Relaunched '{executable_name_escaped}' via Explorer (detached). Updater done."
    try {{ Stop-Transcript | Out-Null }} catch {{}}

    # Wait a moment then clean up this script
    Start-Sleep -Seconds 2
    Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue

}} catch {{
    Write-Host "ERROR: Failed to copy files!"
    Write-Host "Attempting to restore from backup..."

    try {{
        robocopy '{backup_path_escaped}' '{target_dir_escaped}' /E /R:3 /W:1 /MT:1 | Out-Null
        # robocopy exit codes: 0-7 are success, 8+ are failures
        if ($LASTEXITCODE -ge 8) {{
            Write-Host "ERROR: Backup restoration also failed! (robocopy exit $LASTEXITCODE)"
        }} else {{
            Write-Host "Application restored from backup."
        }}
    }} catch {{
        Write-Host "ERROR: Backup restoration failed: $_"
    }}

    Write-Host ""
    Write-Host "Update failed!"
    try {{ Stop-Transcript | Out-Null }} catch {{}}
    Write-Host "Press any key to continue..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    exit 1
}}
'''
        
        try:
            # Write PowerShell script with UTF-8 BOM for proper Unicode support
            with open(script_path, 'w', encoding='utf-8-sig') as f:
                f.write(script_content)
            return script_path
        except Exception as e:
            print(f"Failed to create updater script: {e}")
            return None
    
    def _create_unix_updater_script(self, staging_dir: str, target_dir: str, executable_name: str, backup_path: str, sp_extract_dir: Optional[str] = None) -> str:
        """Create Unix shell updater script with proper Unicode support"""
        script_path = os.path.join(get_config_dir(), 'updater.sh')

        # Get current process ID to wait for it to exit
        current_pid = os.getpid()

        # Extract version from backup path for success flag
        backup_name = os.path.basename(backup_path)
        parts = backup_name.split('_')
        previous_version = parts[1] if len(parts) >= 2 else 'Unknown'

        # Properly escape paths for shell scripts
        staging_dir_escaped = shlex.quote(staging_dir)
        target_dir_escaped = shlex.quote(target_dir)
        backup_path_escaped = shlex.quote(backup_path)
        executable_name_escaped = shlex.quote(executable_name)

        # See the Windows variant: clear the Flet (serious_python) extraction
        # cache before relaunch so the app re-extracts cleanly. Retry briefly in
        # case a file is momentarily held.
        if sp_extract_dir:
            flet_cache_clear = (
                f'cd /tmp 2>/dev/null || cd /\n'
                f'echo "Clearing Flet extraction cache: {shlex.quote(sp_extract_dir)}"\n'
                f'for i in $(seq 1 40); do\n'
                f'    rm -rf {shlex.quote(sp_extract_dir)} 2>/dev/null && break\n'
                f'    sleep 0.25\n'
                f'done\n'
            )
        else:
            flet_cache_clear = ""
        
        script_content = f'''#!/bin/bash
# GamesList Manager Updater (Bash)
# This script handles Unicode paths properly

echo "GamesList Manager Updater"
echo ""

# Wait for main application to close
echo "Waiting for main application to close..."
while kill -0 {current_pid} 2>/dev/null; do
    sleep 1
done

echo "Updating application files..."

# Copy new files from staging to target directory  
echo "Copying files from {staging_dir_escaped} to {target_dir_escaped}"

# Use rsync for better Unicode and reliability support
if command -v rsync >/dev/null 2>&1; then
    # rsync is available - better for handling special characters and permissions
    if rsync -av --delete {staging_dir_escaped}/ {target_dir_escaped}/; then
        echo "Update completed successfully!"
        update_success=true
    else
        echo "ERROR: rsync failed!"
        update_success=false
    fi
else
    # Fallback to cp if rsync is not available
    if cp -R {staging_dir_escaped}/* {target_dir_escaped}/; then
        echo "Update completed successfully!"
        update_success=true
    else
        echo "ERROR: cp failed!"
        update_success=false
    fi
fi

# Handle update result
if [ "$update_success" = false ]; then
    echo "Attempting to restore from backup..."
    if command -v rsync >/dev/null 2>&1; then
        rsync -av --delete {backup_path_escaped}/ {target_dir_escaped}/
    else
        cp -R {backup_path_escaped}/* {target_dir_escaped}/
    fi
    echo ""
    echo "Update failed! Application restored from backup."
    exit 1
fi

# Clean up staging directory
echo "Cleaning up..."
rm -rf {staging_dir_escaped}
{flet_cache_clear}
# Strip the dying app's leaked Flet runtime env vars (per-launch ports etc.) so
# the relaunched app's serious_python bootstrap assigns fresh ones instead of
# reusing the old/dead ports (which leaves the window blank). See the Windows
# updater for the full rationale.
for _v in $(env | sed -n 's/^\\(FLET_[A-Za-z0-9_]*\\)=.*/\\1/p'); do
    echo "Clearing inherited env before relaunch: $_v"
    unset "$_v"
done
unset PYTHONINSPECT

echo "Starting updated application..."
cd {target_dir_escaped}

if [[ {executable_name_escaped} == *.py ]]; then
    python {executable_name_escaped} &
else
    ./{executable_name_escaped} &
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
        """Check for updates on application startup if enabled and show success message if update completed"""
        # First, check if we just completed an update
        try:
            update_success_info = self.check_for_update_success()
            if update_success_info:
                # Import here to avoid circular imports
                from update_ui import show_update_success_popup
                show_update_success_popup(update_success_info)
        except Exception as e:
            print(f"Error checking for update success: {str(e)}")
        
        # Then check for new updates if enabled
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
    
    def _create_update_flag(self, new_version: str):
        """Create a flag file to indicate an update is in progress"""
        try:
            flag_file = os.path.join(get_config_dir(), 'update_flag.json')
            
            flag_data = {
                'previous_version': self.current_version,
                'new_version': new_version,
                'update_time': datetime.now().isoformat()
            }
            
            with open(flag_file, 'w') as f:
                json.dump(flag_data, f, indent=2)
            
            print(f"Created update flag: {flag_file}")
            
        except Exception as e:
            print(f"Failed to create update flag: {e}")
    
    def check_for_update_success(self) -> Optional[Dict[str, str]]:
        """
        Check if app was recently updated and return update info.
        Returns update info if flag exists, None otherwise.
        """
        try:
            flag_file = os.path.join(get_config_dir(), 'update_flag.json')
            
            if not os.path.exists(flag_file):
                return None
            
            with open(flag_file, 'r') as f:
                flag_data = json.load(f)
            
            # Remove the flag file after reading
            os.remove(flag_file)
            print(f"Removed update flag: {flag_file}")
            
            return flag_data
            
        except Exception as e:
            print(f"Error checking update flag: {e}")
            return None

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
                updater_script = os.path.join(config_dir, 'updater.ps1')
            else:
                updater_script = os.path.join(config_dir, 'updater.sh')
            
            if os.path.exists(updater_script):
                print("Starting updater script and exiting application...")
                
                # Create update flag before restart
                if self.latest_release_info:
                    self._create_update_flag(self.latest_release_info.get('version', 'Unknown'))
                
                # Start the updater script in the background. IMPORTANT: launch it
                # with cwd=config_dir. A child inherits the parent's working
                # directory, and for a Flet build that cwd is the serious_python
                # extraction dir (…\flet\app) — so without this the updater would
                # be *sitting inside* the very directory it must delete, locking
                # it (a directory cannot be removed while it is a live process's
                # current directory). config_dir is neutral and always exists.
                if system_name == 'windows':
                    # Use PowerShell to run the .ps1 script with proper Unicode support
                    # -WindowStyle Hidden hides the PowerShell window
                    # -ExecutionPolicy Bypass allows script execution
                    subprocess.Popen([
                        'powershell.exe',
                        '-WindowStyle', 'Hidden',
                        '-ExecutionPolicy', 'Bypass',
                        '-File', updater_script
                    ], creationflags=CREATE_NEW_CONSOLE, cwd=config_dir)
                else:
                    # Start the script in background
                    subprocess.Popen(['/bin/bash', updater_script], cwd=config_dir)
                
                # Small delay to ensure script starts
                time.sleep(0.5)
            else:
                print("No updater script found. Restarting normally...")
                # Fallback to a normal relaunch if no update is pending.
                install_dir, exe_name = _resolve_install_target()
                target = os.path.join(install_dir, exe_name)
                if exe_name.lower().endswith('.py'):
                    # Source run: relaunch the script with the current interpreter.
                    subprocess.Popen([sys.executable, target])
                else:
                    # Packaged/frozen: relaunch the app executable directly.
                    subprocess.Popen([target], cwd=install_dir)
            
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